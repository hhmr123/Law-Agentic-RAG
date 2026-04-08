from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from openai import BadRequestError

from backend import db
from backend.config import settings
from backend.ragflow_gateway import RAGFlowGateway
from backend.retrieval_pipeline import HybridRetrievalPipeline, SearchHit

TraceEmitter = Callable[[dict[str, str]], None] | None

SYSTEM_PROMPT = """
你是 MY_agent 的核心法律助理，用于 Agentic RAG 项目。

回答时请遵守：
1. 只要检索上下文可用，优先基于检索结果作答。
2. 不要编造法条、条文编号或来源。
3. 如果上下文不足，请明确区分“文档可支持的结论”和“通用法律常识”。
4. 先给结论，再给简短解释。
""".strip()

ORCHESTRATED_ROUTER_PROMPT = """
你是法律 RAG 系统的路由助手。
请判断用户问题是否需要先检索知识库。

只能输出一个词：
- SEARCH
- DIRECT

当用户在问法条、法律概念、上传文档、需要来源支撑的事实、论文/简历/资料内容时，输出 SEARCH。
当用户只是闲聊或明显不需要知识库时，输出 DIRECT。
""".strip()

SESSION_SUMMARY_PROMPT = """
请为法律助理总结当前会话。
要求：
1. 不超过 120 个中文字符。
2. 保留争议背景、已经确认的事实、尚未解决的核心焦点。
3. 不要写空话。
""".strip()


class AgentService:
    def __init__(
        self,
        gateway: RAGFlowGateway | None = None,
        retriever: HybridRetrievalPipeline | None = None,
    ) -> None:
        self.gateway = gateway or RAGFlowGateway()
        self.retriever = retriever or HybridRetrievalPipeline(gateway=self.gateway)

    def answer(
        self,
        *,
        message: str,
        session_id: str | None = None,
        event_callback: TraceEmitter = None,
    ) -> dict[str, Any]:
        if not self._has_model_credentials():
            raise ValueError(self._missing_model_config_message())

        # The backend always works with a session id, even though the current
        # frontend does not expose a "session manager" UI. A new session id is
        # created automatically when the caller does not provide one.
        active_session_id = session_id or f"session_{uuid4().hex}"
        trace: list[dict[str, str]] = []
        captured_sources: list[dict[str, Any]] = []

        self._record_trace(
            trace,
            event_callback,
            "receive_question",
            "后端已接收用户问题。",
        )

        model = self._build_model()
        db.save_chat_message(active_session_id, "user", message)
        session_summary = self._refresh_session_summary_if_needed(
            model=model,
            session_id=active_session_id,
            trace=trace,
            event_callback=event_callback,
        )

        if self._should_use_orchestrated_mode():
            self._record_trace(
                trace,
                event_callback,
                "mode",
                "当前模型已切换到编排式检索模式，以避免不稳定的原生工具调用。",
            )
            answer = self._answer_with_orchestrated_retrieval(
                model=model,
                message=message,
                session_summary=session_summary,
                trace=trace,
                captured_sources=captured_sources,
                event_callback=event_callback,
            )
        else:
            self._record_trace(
                trace,
                event_callback,
                "mode",
                "当前模型使用原生 Agent 工具调用模式。",
            )
            try:
                answer = self._answer_with_native_agent(
                    model=model,
                    message=message,
                    session_id=active_session_id,
                    session_summary=session_summary,
                    trace=trace,
                    captured_sources=captured_sources,
                    event_callback=event_callback,
                )
            except Exception as exc:
                if not self._should_fallback_to_direct_mode(exc):
                    raise
                self._record_trace(
                    trace,
                    event_callback,
                    "fallback",
                    "原生工具调用失败，系统已切换到编排式检索模式。",
                )
                answer = self._answer_with_orchestrated_retrieval(
                    model=model,
                    message=message,
                    session_summary=session_summary,
                    trace=trace,
                    captured_sources=captured_sources,
                    event_callback=event_callback,
                )

        db.save_chat_message(active_session_id, "assistant", answer)
        self._refresh_session_summary_if_needed(
            model=model,
            session_id=active_session_id,
            trace=trace,
            event_callback=event_callback,
            force=False,
        )
        return {
            "session_id": active_session_id,
            "answer": answer,
            "sources": captured_sources,
            "trace": trace,
        }

    def _build_model(self) -> Any:
        if settings.model_provider == "gemini":
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError as exc:
                raise ValueError(
                    "Gemini native integration requires langchain-google-genai. "
                    "Please install project dependencies again."
                ) from exc

            return ChatGoogleGenerativeAI(
                model=settings.model,
                google_api_key=settings.google_api_key,
                temperature=0.2,
                max_retries=2,
            )

        return init_chat_model(
            model=settings.model,
            model_provider="openai",
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=0.2,
        )

    def _answer_with_native_agent(
        self,
        *,
        model: Any,
        message: str,
        session_id: str,
        session_summary: str,
        trace: list[dict[str, str]],
        captured_sources: list[dict[str, Any]],
        event_callback: TraceEmitter,
    ) -> str:
        retriever = self.retriever
        knowledge_tool_calls = 0

        def emit_retrieval(step: str, detail: str) -> None:
            self._record_trace(trace, event_callback, step, detail)

        @tool("search_knowledge_base")
        def search_knowledge_base(question: str) -> str:
            """Search the legal knowledge base for relevant statutes or uploaded document context."""
            nonlocal knowledge_tool_calls
            if knowledge_tool_calls >= 1:
                return (
                    "TOOL_CALL_LIMIT_REACHED: search_knowledge_base has already been called once in this turn. "
                    "Use the existing retrieval result and produce the final answer directly."
                )

            knowledge_tool_calls += 1
            results = retriever.search(
                question=question,
                top_k=3,
                model=model,
                trace_callback=emit_retrieval,
            )
            if not results:
                self._record_trace(
                    trace,
                    event_callback,
                    "retrieval",
                    "法律检索链路未返回可靠片段。",
                )
                return "No reliable knowledge-base context was found."

            captured_sources[:] = self._to_source_items(results)
            self._record_trace(
                trace,
                event_callback,
                "retrieval",
                f"已检索到 {len(results)} 个上下文块。模式：{retriever.last_search_mode}。",
            )
            return self._format_retrieved_context(results)

        agent = create_agent(
            model=model,
            tools=[search_knowledge_base],
            system_prompt=SYSTEM_PROMPT,
        )

        messages = self._build_agent_messages(
            session_id=session_id,
            current_message=message,
            session_summary=session_summary,
        )
        result = agent.invoke(
            {"messages": messages},
            config={"recursion_limit": 8},
        )
        answer = self._extract_answer(result)

        if captured_sources:
            self._record_trace(
                trace,
                event_callback,
                "synthesize_answer",
                "已基于原生检索结果生成最终回答。",
            )
        else:
            self._record_trace(
                trace,
                event_callback,
                "respond_directly",
                "本轮未调用知识库工具，模型直接作答。",
            )
        return answer

    def _answer_with_orchestrated_retrieval(
        self,
        *,
        model: Any,
        message: str,
        session_summary: str,
        trace: list[dict[str, str]],
        captured_sources: list[dict[str, Any]],
        event_callback: TraceEmitter,
    ) -> str:
        route = self._route_question(model=model, message=message)
        route_label = "检索知识库" if route == "SEARCH" else "直接回答"
        self._record_trace(trace, event_callback, "route", f"路由决策：{route_label}。")

        prompt = self._build_direct_answer_prompt(message)
        if route == "SEARCH":
            results = self.retriever.search(
                question=message,
                top_k=3,
                model=model,
                trace_callback=lambda step, detail: self._record_trace(trace, event_callback, step, detail),
            )
            if results:
                captured_sources[:] = self._to_source_items(results)
                self._record_trace(
                    trace,
                    event_callback,
                    "retrieval",
                    f"已检索到 {len(results)} 个上下文块。模式：{self.retriever.last_search_mode}。",
                )
                prompt = self._build_grounded_answer_prompt(
                    message=message,
                    context=self._format_retrieved_context(results),
                )
            else:
                self._record_trace(
                    trace,
                    event_callback,
                    "retrieval",
                    "法律检索链路未返回可靠片段，模型将转为直接回答。",
                )

        answer_messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]
        if session_summary:
            answer_messages.append(
                SystemMessage(
                    content=f"Conversation summary so far:\n{session_summary}",
                )
            )
        answer_messages.append(HumanMessage(content=prompt))
        answer = self._invoke_text(model, answer_messages)
        self._record_trace(
            trace,
            event_callback,
            "synthesize_answer",
            "已在编排式检索模式下完成回答生成。",
        )
        return answer

    def _build_agent_messages(
        self,
        *,
        session_id: str,
        current_message: str,
        session_summary: str,
    ) -> list[Any]:
        # Prompt assembly uses a two-layer memory strategy:
        # 1. a compact summary for older turns
        # 2. the latest raw messages for short-term conversational detail
        history = db.get_session_messages(session_id, limit=12)
        messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]
        if session_summary:
            messages.append(
                SystemMessage(content=f"Conversation summary so far:\n{session_summary}")
            )

        for item in history:
            role = item.get("role")
            content = str(item.get("content", ""))
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system":
                messages.append(SystemMessage(content=content))

        if not (
            messages
            and isinstance(messages[-1], HumanMessage)
            and str(messages[-1].content) == current_message
        ):
            messages.append(HumanMessage(content=current_message))
        return messages

    def _route_question(self, *, model: Any, message: str) -> str:
        if self._heuristic_should_search(message):
            return "SEARCH"
        try:
            raw = self._invoke_text(
                model,
                [
                    SystemMessage(content=ORCHESTRATED_ROUTER_PROMPT),
                    HumanMessage(content=message),
                ],
            ).strip().upper()
        except Exception:
            return "SEARCH" if self._heuristic_should_search(message) else "DIRECT"

        if "SEARCH" in raw:
            return "SEARCH"
        if "DIRECT" in raw:
            return "DIRECT"
        return "SEARCH" if self._heuristic_should_search(message) else "DIRECT"

    def _refresh_session_summary_if_needed(
        self,
        *,
        model: Any,
        session_id: str,
        trace: list[dict[str, str]],
        event_callback: TraceEmitter,
        force: bool = False,
    ) -> str:
        total_messages = db.count_session_messages(session_id)
        existing = db.get_session_summary(session_id)
        if total_messages < settings.summary_trigger_messages:
            return existing["summary"] if existing else ""

        if (
            not force
            and existing
            and int(existing.get("message_count", 0)) >= total_messages - 1
        ):
            return str(existing["summary"])

        # Once a conversation becomes long enough, we summarize the older turns
        # and store that summary in SQLite so future prompts stay compact.
        history = db.get_session_messages(session_id, limit=40)
        if len(history) <= settings.summary_keep_recent:
            return str(existing["summary"]) if existing else ""

        to_summarize = history[:-settings.summary_keep_recent]
        summary_source = "\n".join(
            f"{item['role']}: {item['content']}" for item in to_summarize
        )
        summary = self._invoke_text(
            model,
            [
                SystemMessage(content=SESSION_SUMMARY_PROMPT),
                HumanMessage(content=summary_source),
            ],
        )
        db.upsert_session_summary(session_id, summary, total_messages)
        self._record_trace(
            trace,
            event_callback,
            "session_summary",
            "历史对话已压缩为会话摘要，用于控制上下文长度。",
        )
        return summary

    @staticmethod
    def _invoke_text(model: Any, messages: list[Any]) -> str:
        response = model.invoke(messages)
        content = getattr(response, "content", response)
        return AgentService._normalize_content(content)

    @staticmethod
    def _format_retrieved_context(results: list[SearchHit]) -> str:
        return "\n\n".join(
            (
                f"来源：{item.source}\n"
                f"章节：{item.title}\n"
                f"片段：{item.excerpt}\n"
                f"相关度：{item.score}"
            )
            for item in results
        )

    @staticmethod
    def _build_grounded_answer_prompt(*, message: str, context: str) -> str:
        return f"""
请基于下面的法律上下文回答用户问题。

上下文：
{context}

用户问题：
{message}

要求：
1. 先给结论，再给简短解释。
2. 如果上下文不足，请明确区分“文档依据”和“通用法律常识”。
3. 不要编造新的法条编号或来源。
""".strip()

    @staticmethod
    def _build_direct_answer_prompt(message: str) -> str:
        return f"""
请直接回答用户问题。

用户问题：
{message}

要求：
1. 先给结论，再给简短解释。
2. 如果答案本应依赖法律来源但当前没有可靠依据，请明确说明，不要编造支撑。
""".strip()

    @staticmethod
    def _to_source_items(results: list[SearchHit]) -> list[dict[str, Any]]:
        return [
            {
                "source": item.source,
                "title": item.title,
                "excerpt": item.excerpt,
                "score": item.score,
                "retrieval_path": item.retrieval_path,
            }
            for item in results
        ]

    @staticmethod
    def _heuristic_should_search(message: str) -> bool:
        lowered = message.lower()
        keywords = (
            "law",
            "statute",
            "article",
            "法",
            "法条",
            "民法典",
            "刑法",
            "拍卖法",
            "定金",
            "欺诈",
            "合同",
            "离婚",
            "法律",
            "上传",
            "文档",
            "pdf",
            "docx",
            "source",
            "citation",
            "resume",
            "paper",
        )
        return any(keyword in lowered or keyword in message for keyword in keywords)

    @staticmethod
    def _should_use_orchestrated_mode() -> bool:
        tool_mode = settings.agent_tool_mode
        if tool_mode == "orchestrated":
            return True
        if tool_mode == "native":
            return False
        if settings.model_provider == "gemini":
            return False

        base_url = settings.base_url.lower()
        model_name = settings.model.lower()
        if "generativelanguage.googleapis.com" in base_url:
            return True
        if "gemini" in model_name:
            return True
        return False

    @staticmethod
    def _should_fallback_to_direct_mode(exc: Exception) -> bool:
        message = str(exc).lower()
        if "thought_signature" in message:
            return True
        if "tools to work correctly" in message:
            return True
        if "function call is missing" in message:
            return True
        return isinstance(exc, BadRequestError)

    @staticmethod
    def _extract_answer(result: Any) -> str:
        if isinstance(result, dict):
            messages = result.get("messages") or []
            if messages:
                last = messages[-1]
                if hasattr(last, "content"):
                    content = last.content
                elif isinstance(last, dict):
                    content = last.get("content", "")
                else:
                    content = ""
                return AgentService._normalize_content(content)
            for key in ("output", "answer", "response"):
                if key in result:
                    return AgentService._normalize_content(result[key])
        return AgentService._normalize_content(result)

    @staticmethod
    def _normalize_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    else:
                        parts.append(str(item.get("text", "")) or str(item))
                else:
                    text = getattr(item, "text", None)
                    if text is not None:
                        parts.append(str(text))
                    else:
                        parts.append(str(item))
            return "".join(parts).strip()
        if isinstance(content, dict):
            if content.get("type") == "text":
                return str(content.get("text", "")).strip()
            if "text" in content:
                return str(content.get("text", "")).strip()
        return str(content).strip()

    @staticmethod
    def _has_model_credentials() -> bool:
        if settings.model_provider == "gemini":
            return bool(settings.google_api_key and settings.model)
        return bool(settings.api_key and settings.base_url and settings.model)

    @staticmethod
    def _missing_model_config_message() -> str:
        if settings.model_provider == "gemini":
            return (
                "Missing Gemini native model configuration. "
                "Please set MODEL_PROVIDER=gemini and provide GOOGLE_API_KEY "
                "(or GEMINI_API_KEY / ARK_API_KEY) plus MODEL."
            )
        return "Missing model configuration. Please set ARK_API_KEY, BASE_URL, and MODEL."

    @staticmethod
    def _record_trace(
        trace: list[dict[str, str]],
        event_callback: TraceEmitter,
        step: str,
        detail: str,
    ) -> None:
        item = {"step": step, "detail": detail}
        trace.append(item)
        if event_callback:
            event_callback(item)
