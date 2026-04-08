from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Iterable, TypedDict
from uuid import uuid4, uuid5, NAMESPACE_URL

from langgraph.graph import END, START, StateGraph
from langchain_core.messages import HumanMessage, SystemMessage

from backend import db
from backend.config import settings
from backend.ragflow_gateway import RAGFlowGateway, RetrievalResponse

TraceCallback = Callable[[str, str], None] | None


@dataclass
class SearchHit:
    source: str
    excerpt: str
    score: float
    chunk_id: str
    title: str
    level: int
    dense_score: float = 0.0
    lexical_score: float = 0.0
    parent_chunk_id: str | None = None
    retrieval_path: str = "base"


@dataclass
class CachedLeafChunk:
    chunk_id: str
    source: str
    title: str
    excerpt: str
    text: str
    level: int
    parent_chunk_id: str | None
    tokens: list[str]
    token_counts: Counter[str]
    doc_length: int
    embedding: list[float]


class RetrievalGraphState(TypedDict, total=False):
    question: str
    dataset_scope: str
    base_hits: list[SearchHit]
    combined_hits: list[SearchHit]
    final_hits: list[SearchHit]
    accepted: bool
    grade_reason: str
    keyword_query: str
    step_back_query: str
    hyde_query: str
    strict_local_fallback: bool


class HybridRetrievalPipeline:
    def __init__(self, gateway: RAGFlowGateway | None = None) -> None:
        self.gateway = gateway or RAGFlowGateway()
        self.last_search_mode = "hybrid-local"
        self._embedding_client: Any | None = None
        self._cache_signature: tuple[int, int] | None = None
        self._cache_dataset_scope: str = ""
        self._cached_leaf_chunks: list[CachedLeafChunk] = []
        self._chunk_map: dict[str, CachedLeafChunk] = {}
        self._bm25_doc_freq: Counter[str] = Counter()
        self._bm25_avgdl: float = 0.0
        self._total_leaf_docs: int = 0

    def index_document(self, *, document_id: int, filename: str, text: str) -> str:
        chunks = self._build_chunk_hierarchy(document_id=document_id, filename=filename, text=text)
        if not chunks:
            return "local-hybrid-empty"

        leaf_chunks = [item for item in chunks if item["is_leaf"]]
        embeddings = self._embed_texts([item["text"] for item in leaf_chunks])
        for item, vector in zip(leaf_chunks, embeddings):
            item["embedding"] = json.dumps(vector, ensure_ascii=False)

        db.replace_document_chunks(document_id, chunks)
        self.invalidate_cache()
        return "local-hybrid-indexed"

    def ensure_local_index(self) -> None:
        for doc in db.list_documents_with_text(limit=500):
            document_id = int(doc["id"])
            if db.document_has_chunks(document_id):
                continue
            self.index_document(
                document_id=document_id,
                filename=str(doc["filename"]),
                text=str(doc["extracted_text"]),
            )

    def prewarm(self) -> None:
        self.ensure_local_index()
        self._load_leaf_index()

    def invalidate_cache(self) -> None:
        self._cache_signature = None
        self._cache_dataset_scope = ""
        self._cached_leaf_chunks = []
        self._chunk_map = {}
        self._bm25_doc_freq = Counter()
        self._bm25_avgdl = 0.0
        self._total_leaf_docs = 0

    def search(
        self,
        *,
        question: str,
        top_k: int = 3,
        model: Any | None = None,
        trace_callback: TraceCallback = None,
    ) -> list[SearchHit]:
        search_started_at = perf_counter()
        # The retrieval pipeline is now orchestrated as a graph rather than a
        # single linear function. This makes the "retrieve -> grade -> rewrite
        # -> retrieve again -> rerank/fallback" path explicit in LangSmith.
        results = self._search_via_langgraph(
            question=question,
            top_k=top_k,
            model=model,
            trace_callback=trace_callback,
        )

        self._emit(
            trace_callback,
            "retrieval_perf",
            f"检索链路总耗时 {(perf_counter() - search_started_at) * 1000:.0f}ms。",
        )
        return results[:top_k]

    def _search_via_langgraph(
        self,
        *,
        question: str,
        top_k: int,
        model: Any | None,
        trace_callback: TraceCallback,
    ) -> list[SearchHit]:
        def primary_retrieve(state: RetrievalGraphState) -> RetrievalGraphState:
            if not self.gateway.enabled:
                return {
                    "dataset_scope": "",
                    "base_hits": [],
                    "combined_hits": [],
                    "strict_local_fallback": False,
                }

            base_response = self.gateway.search_with_meta(
                question=state["question"],
                top_k=max(8, settings.hybrid_candidate_pool),
                allow_local_fallback=False,
            )
            # "Hybrid retrieval" on the remote path means we first trust
            # RAGFlow's own retrieval engine to combine keyword/full-text
            # matching with vector similarity, instead of scanning local chunks
            # in Python on every query.
            base_hits = self._convert_remote_hits(base_response, label="base")
            self._emit(
                trace_callback,
                "hybrid_search",
                (
                    f"主检索已调用 RAGFlow 检索引擎。"
                    f" engine={base_response.engine}, mode={base_response.mode}, "
                    f"耗时={base_response.elapsed_ms:.0f}ms, 命中={len(base_hits)}。"
                ),
            )
            return {
                "dataset_scope": base_response.dataset_id,
                "base_hits": base_hits,
                "combined_hits": list(base_hits),
                "strict_local_fallback": False,
            }

        def route_after_primary(state: RetrievalGraphState) -> str:
            if not self.gateway.enabled:
                return "local_fallback"
            if state.get("base_hits"):
                return "grade_primary"
            self._emit(
                trace_callback,
                "fallback",
                "RAGFlow 未返回可用片段，系统切换到本地混合检索兜底。",
            )
            return "local_fallback"

        def grade_primary(state: RetrievalGraphState) -> RetrievalGraphState:
            accepted, reason = self._grade_retrieval(
                question=state["question"],
                hits=state.get("base_hits", [])[:4],
                model=model,
            )
            self._emit(
                trace_callback,
                "grade_documents",
                f"相关性门控结果：{'通过' if accepted else '未通过'}（{reason}）。",
            )
            return {"accepted": accepted, "grade_reason": reason}

        def route_after_grade(state: RetrievalGraphState) -> str:
            return "finalize_remote" if state.get("accepted") else "rewrite_queries"

        def rewrite_queries(state: RetrievalGraphState) -> RetrievalGraphState:
            # We generate multiple query variants because legal questions often
            # need different retrieval expressions:
            # - keyword query: preserve literal legal terms
            # - step-back query: rewrite into a more abstract legal issue
            # - HyDE query: generate a legal-style hypothetical answer and use
            #   that text as a semantic retrieval query
            keyword_query = self._generate_keyword_query(question=state["question"], model=model)
            step_back_query = self._generate_step_back_query(question=state["question"], model=model)
            hyde_query = self._generate_hyde_passage(question=state["question"], model=model)

            if keyword_query and keyword_query != state["question"]:
                self._emit(trace_callback, "query_rewrite", f"法律关键词重写：{keyword_query}")
            if step_back_query and step_back_query != state["question"]:
                self._emit(trace_callback, "query_rewrite", f"Step-Back 重写：{step_back_query}")
            if hyde_query:
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"HyDE 已生成 {len(hyde_query)} 字的法律风格假设答案。",
                )

            return {
                "keyword_query": keyword_query,
                "step_back_query": step_back_query,
                "hyde_query": hyde_query,
            }

        def expanded_retrieve(state: RetrievalGraphState) -> RetrievalGraphState:
            combined_hits = list(state.get("combined_hits", []))

            for label, query in (
                ("keyword", state.get("keyword_query", "")),
                ("step-back", state.get("step_back_query", "")),
                ("hyde", state.get("hyde_query", "")),
            ):
                if not query or query == state["question"]:
                    continue
                response = self.gateway.search_with_meta(
                    question=query,
                    top_k=max(8, settings.hybrid_candidate_pool),
                    allow_local_fallback=False,
                )
                if label == "keyword":
                    detail = (
                        f"关键词查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={response.elapsed_ms:.0f}ms, 命中={len(response.chunks)}。"
                    )
                elif label == "step-back":
                    detail = (
                        f"Step-Back 查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={response.elapsed_ms:.0f}ms, 命中={len(response.chunks)}。"
                    )
                else:
                    detail = (
                        f"HyDE 查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={response.elapsed_ms:.0f}ms, 命中={len(response.chunks)}。"
                    )
                self._emit(trace_callback, "hybrid_search", detail)
                combined_hits.extend(self._convert_remote_hits(response, label=label))

            combined_hits = self._refuse_irrelevant_hits(
                question=state["question"],
                hits=combined_hits,
                model=model,
            )
            return {
                "combined_hits": combined_hits,
                "strict_local_fallback": True,
            }

        def finalize_remote(state: RetrievalGraphState) -> RetrievalGraphState:
            candidate_hits = state.get("combined_hits") or state.get("base_hits", [])
            reranked_hits = self._rerank_remote_hits(
                question=state["question"],
                hits=candidate_hits,
                model=model,
            )
            if reranked_hits:
                self._emit(
                    trace_callback,
                    "retrieval_rerank",
                    f"已对远程候选法条完成重排，候选数={len(reranked_hits)}。",
                )

            merged_hits = self._dedupe_remote_hits(reranked_hits or candidate_hits, top_k=top_k)
            if merged_hits:
                self._emit(
                    trace_callback,
                    "retrieval_context",
                    "最终上下文采用 RAGFlow 原生片段，本轮无需本地父块自动合并。",
                )
                self.last_search_mode = "ragflow-http"
                return {"final_hits": merged_hits}

            self._emit(
                trace_callback,
                "fallback",
                "RAGFlow 在重写与重排后仍未形成稳定命中，系统切换到本地混合检索兜底。",
            )
            return {"final_hits": []}

        def route_after_finalize(state: RetrievalGraphState) -> str:
            return END if state.get("final_hits") else "local_fallback"

        def local_fallback(state: RetrievalGraphState) -> RetrievalGraphState:
            local_hits = self._search_via_local_hybrid(
                question=state["question"],
                top_k=top_k,
                model=model,
                trace_callback=trace_callback,
                dataset_scope=state.get("dataset_scope", ""),
                strict_on_irrelevant=state.get("strict_local_fallback", False),
            )
            return {"final_hits": local_hits}

        graph = StateGraph(RetrievalGraphState)
        graph.add_node("primary_retrieve", primary_retrieve)
        graph.add_node("grade_primary", grade_primary)
        graph.add_node("rewrite_queries", rewrite_queries)
        graph.add_node("expanded_retrieve", expanded_retrieve)
        graph.add_node("finalize_remote", finalize_remote)
        graph.add_node("local_fallback", local_fallback)

        graph.add_edge(START, "primary_retrieve")
        graph.add_conditional_edges(
            "primary_retrieve",
            route_after_primary,
            {
                "grade_primary": "grade_primary",
                "local_fallback": "local_fallback",
            },
        )
        graph.add_conditional_edges(
            "grade_primary",
            route_after_grade,
            {
                "finalize_remote": "finalize_remote",
                "rewrite_queries": "rewrite_queries",
            },
        )
        graph.add_edge("rewrite_queries", "expanded_retrieve")
        graph.add_edge("expanded_retrieve", "finalize_remote")
        graph.add_conditional_edges(
            "finalize_remote",
            route_after_finalize,
            {
                "local_fallback": "local_fallback",
                END: END,
            },
        )
        graph.add_edge("local_fallback", END)

        compiled = graph.compile()
        result = compiled.invoke(
            {
                "question": question,
                "dataset_scope": "",
                "base_hits": [],
                "combined_hits": [],
                "final_hits": [],
                "strict_local_fallback": False,
            },
            config={"run_name": "legal_retrieval_graph"},
        )
        return list(result.get("final_hits", []))

    def _search_via_ragflow(
        self,
        *,
        question: str,
        top_k: int,
        model: Any | None,
        trace_callback: TraceCallback,
    ) -> list[SearchHit]:
        base_response = self.gateway.search_with_meta(
            question=question,
            top_k=max(8, settings.hybrid_candidate_pool),
            allow_local_fallback=False,
        )
        base_hits = self._convert_remote_hits(base_response, label="base")
        self._emit(
            trace_callback,
            "hybrid_search",
            (
                f"主检索已调用 RAGFlow 检索引擎。"
                f" engine={base_response.engine}, mode={base_response.mode}, "
                f"耗时={base_response.elapsed_ms:.0f}ms, 命中={len(base_hits)}。"
            ),
        )

        if not base_hits:
            self._emit(
                trace_callback,
                "fallback",
                "RAGFlow 未返回可用片段，系统切换到本地混合检索兜底。",
            )
            return self._search_via_local_hybrid(
                question=question,
                top_k=top_k,
                model=model,
                trace_callback=trace_callback,
                dataset_scope=base_response.dataset_id,
            )

        accepted, reason = self._grade_retrieval(question=question, hits=base_hits[:4], model=model)
        self._emit(
            trace_callback,
            "grade_documents",
            f"相关性门控结果：{'通过' if accepted else '未通过'}（{reason}）。",
        )

        combined_hits = list(base_hits)
        if not accepted:
            keyword_query = self._generate_keyword_query(question=question, model=model)
            if keyword_query and keyword_query != question:
                keyword_response = self.gateway.search_with_meta(
                    question=keyword_query,
                    top_k=max(8, settings.hybrid_candidate_pool),
                    allow_local_fallback=False,
                )
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"法律关键词重写：{keyword_query}",
                )
                self._emit(
                    trace_callback,
                    "hybrid_search",
                    (
                        f"关键词查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={keyword_response.elapsed_ms:.0f}ms, 命中={len(keyword_response.chunks)}。"
                    ),
                )
                combined_hits.extend(self._convert_remote_hits(keyword_response, label="keyword"))

            step_back_query = self._generate_step_back_query(question=question, model=model)
            if step_back_query and step_back_query != question:
                step_response = self.gateway.search_with_meta(
                    question=step_back_query,
                    top_k=max(8, settings.hybrid_candidate_pool),
                    allow_local_fallback=False,
                )
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"Step-Back 重写：{step_back_query}",
                )
                self._emit(
                    trace_callback,
                    "hybrid_search",
                    (
                        f"Step-Back 查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={step_response.elapsed_ms:.0f}ms, 命中={len(step_response.chunks)}。"
                    ),
                )
                combined_hits.extend(self._convert_remote_hits(step_response, label="step-back"))

            hyde_query = self._generate_hyde_passage(question=question, model=model)
            if hyde_query:
                hyde_response = self.gateway.search_with_meta(
                    question=hyde_query,
                    top_k=max(8, settings.hybrid_candidate_pool),
                    allow_local_fallback=False,
                )
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"HyDE 已生成 {len(hyde_query)} 字的法律风格假设答案。",
                )
                self._emit(
                    trace_callback,
                    "hybrid_search",
                    (
                        f"HyDE 查询已发送到 RAGFlow 检索引擎。"
                        f" 耗时={hyde_response.elapsed_ms:.0f}ms, 命中={len(hyde_response.chunks)}。"
                    ),
                )
                combined_hits.extend(self._convert_remote_hits(hyde_response, label="hyde"))

            combined_hits = self._refuse_irrelevant_hits(
                question=question,
                hits=combined_hits,
                model=model,
            )

        reranked_hits = self._rerank_remote_hits(
            question=question,
            hits=combined_hits,
            model=model,
        )
        if reranked_hits:
            self._emit(
                trace_callback,
                "retrieval_rerank",
                f"已对远程候选法条完成重排，候选数={len(reranked_hits)}。",
            )

        merged_hits = self._dedupe_remote_hits(reranked_hits or combined_hits, top_k=top_k)
        if merged_hits:
            self._emit(
                trace_callback,
                "retrieval_context",
                "最终上下文采用 RAGFlow 原生片段，本轮无需本地父块自动合并。",
            )
            self.last_search_mode = "ragflow-http"
            return merged_hits[:top_k]

        self._emit(
            trace_callback,
            "fallback",
            "RAGFlow 在重写与重排后仍未形成稳定命中，系统切换到本地混合检索兜底。",
        )
        return self._search_via_local_hybrid(
            question=question,
            top_k=top_k,
            model=model,
            trace_callback=trace_callback,
            dataset_scope=base_response.dataset_id,
            strict_on_irrelevant=True,
        )

    def _search_via_local_hybrid(
        self,
        *,
        question: str,
        top_k: int,
        model: Any | None,
        trace_callback: TraceCallback,
        dataset_scope: str = "",
        strict_on_irrelevant: bool = False,
    ) -> list[SearchHit]:
        # This is no longer the main path. It is a local fallback kept for
        # resilience when remote RAGFlow retrieval is unavailable or unstable.
        self.ensure_local_index()
        cache_warmed = self._load_leaf_index(dataset_scope=dataset_scope)
        leaf_chunks = self._cached_leaf_chunks
        if not leaf_chunks:
            return []

        base_hits, hybrid_meta = self._run_hybrid_rank(question=question, label="base")
        self._emit(
            trace_callback,
            "hybrid_search",
            (
                f"本地兜底混合检索已执行，叶子分块数={len(leaf_chunks)}。"
                f" dataset_scope={dataset_scope or 'all'}, "
                f"cache={'warm' if cache_warmed else 'reused'}, "
                f"词法={hybrid_meta['lexical_ms']:.0f}ms, "
                f"向量={hybrid_meta['dense_ms']:.0f}ms, "
                f"总计={hybrid_meta['total_ms']:.0f}ms。"
            ),
        )

        accepted, reason = self._grade_retrieval(question=question, hits=base_hits[:4], model=model)
        self._emit(
            trace_callback,
            "grade_documents",
            f"相关性门控结果：{'通过' if accepted else '未通过'}（{reason}）。",
        )

        if strict_on_irrelevant and not accepted:
            self._emit(
                trace_callback,
                "fallback_guard",
                "本地兜底语料仍被判定为不相关，系统停止检索链路，避免强行生成错误答案。",
            )
            self.last_search_mode = "hybrid-local"
            return []

        combined_hits = list(base_hits)
        if not accepted:
            step_back_query = self._generate_step_back_query(question=question, model=model)
            if step_back_query and step_back_query != question:
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"Step-Back 重写：{step_back_query}",
                )
                combined_hits.extend(
                    self._run_hybrid_rank(
                        question=step_back_query,
                        label="step-back",
                    )[0]
                )

            hyde_query = self._generate_hyde_passage(question=question, model=model)
            if hyde_query:
                self._emit(
                    trace_callback,
                    "query_rewrite",
                    f"HyDE 已生成 {len(hyde_query)} 字的法律风格假设答案。",
                )
                combined_hits.extend(
                    self._run_hybrid_rank(
                        question=hyde_query,
                        label="hyde",
                    )[0]
                )

            combined_hits = self._refuse_irrelevant_hits(
                question=question,
                hits=combined_hits,
                model=model,
            )

        merged_hits = self._auto_merge_hits(combined_hits, top_k=top_k)
        if merged_hits:
            self._emit(
                trace_callback,
                "auto_merge",
                f"已将叶子分块提升并合并为 {len(merged_hits)} 个父级上下文块。",
            )
        self.last_search_mode = "hybrid-local"
        return merged_hits[:top_k]

    def _run_hybrid_rank(
        self,
        *,
        question: str,
        label: str,
    ) -> tuple[list[SearchHit], dict[str, float]]:
        started_at = perf_counter()
        lexical_started_at = perf_counter()
        lexical_scores = self._bm25_scores(question=question)
        lexical_ms = (perf_counter() - lexical_started_at) * 1000

        dense_started_at = perf_counter()
        dense_scores = self._dense_scores(question=question)
        dense_ms = (perf_counter() - dense_started_at) * 1000

        lexical_ranking = self._sorted_ranking(lexical_scores)
        dense_ranking = self._sorted_ranking(dense_scores)
        # The local fallback fuses lexical and dense rankings with RRF
        # (Reciprocal Rank Fusion). This is different from the remote RAGFlow
        # path, where the hybrid retrieval is handled by RAGFlow itself.
        fused_scores = self._rrf_fuse(
            rankings=[lexical_ranking, dense_ranking],
            rank_constant=settings.hybrid_rrf_k,
        )

        hits: list[SearchHit] = []
        for chunk_id, score in sorted(fused_scores.items(), key=lambda item: item[1], reverse=True):
            chunk = self._chunk_map.get(chunk_id)
            if not chunk:
                continue
            hits.append(
                SearchHit(
                    source=chunk.source,
                    excerpt=chunk.excerpt,
                    score=round(score, 4),
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    level=chunk.level,
                    dense_score=round(dense_scores.get(chunk_id, 0.0), 4),
                    lexical_score=round(lexical_scores.get(chunk_id, 0.0), 4),
                    parent_chunk_id=chunk.parent_chunk_id,
                    retrieval_path=label,
                )
            )
        return (
            hits[: max(8, settings.hybrid_candidate_pool)],
            {
                "lexical_ms": lexical_ms,
                "dense_ms": dense_ms,
                "total_ms": (perf_counter() - started_at) * 1000,
            },
        )

    def _convert_remote_hits(
        self,
        response: RetrievalResponse,
        *,
        label: str,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for item in response.chunks:
            stable_key = f"{item.source}|{item.excerpt}"
            chunk_id = f"remote::{uuid5(NAMESPACE_URL, stable_key).hex}"
            hits.append(
                SearchHit(
                    source=item.source,
                    excerpt=item.excerpt,
                    score=round(item.score, 4),
                    chunk_id=chunk_id,
                    title=item.source,
                    level=0,
                    dense_score=round(item.score, 4),
                    lexical_score=round(item.score, 4),
                    parent_chunk_id=None,
                    retrieval_path=label,
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[: max(8, settings.hybrid_candidate_pool)]

    @staticmethod
    def _dedupe_remote_hits(hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        deduped: list[SearchHit] = []
        seen: set[tuple[str, str]] = set()
        for item in sorted(hits, key=lambda hit: hit.score, reverse=True):
            key = (item.source, item.excerpt)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:top_k]

    def _bm25_scores(
        self,
        *,
        question: str,
    ) -> dict[str, float]:
        query_tokens = self._tokenize(question)
        if not query_tokens:
            return {}

        scores: dict[str, float] = {}
        query_counts = Counter(query_tokens)
        total_docs = max(self._total_leaf_docs, 1)
        for chunk in self._cached_leaf_chunks:
            score = 0.0
            doc_len = max(chunk.doc_length, 1)
            for token, query_tf in query_counts.items():
                if chunk.token_counts[token] == 0:
                    continue
                doc_freq = self._bm25_doc_freq.get(token, 0)
                idf = math.log(1 + (total_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                tf = chunk.token_counts[token]
                denom = tf + 1.5 * (1 - 0.75 + 0.75 * doc_len / max(self._bm25_avgdl, 1))
                score += idf * ((tf * (1.5 + 1)) / max(denom, 1e-9)) * query_tf
            if score > 0:
                scores[chunk.chunk_id] = score
        return scores

    def _dense_scores(
        self,
        *,
        question: str,
    ) -> dict[str, float]:
        query_vector = self._embed_query(question)
        if not query_vector:
            return {}

        scores: dict[str, float] = {}
        for chunk in self._cached_leaf_chunks:
            if not chunk.embedding:
                continue
            scores[chunk.chunk_id] = self._cosine_similarity(query_vector, chunk.embedding)
        return scores

    def _load_leaf_index(self, *, dataset_scope: str = "") -> bool:
        signature = db.get_leaf_index_signature(dataset_scope or None)
        if (
            self._cache_signature == signature
            and self._cache_dataset_scope == dataset_scope
            and self._cached_leaf_chunks
        ):
            return False

        raw_chunks = db.list_leaf_chunks(dataset_id=dataset_scope or None)
        cached_chunks: list[CachedLeafChunk] = []
        doc_freq: Counter[str] = Counter()
        total_doc_length = 0
        chunk_map: dict[str, CachedLeafChunk] = {}
        for item in raw_chunks:
            tokens = self._tokenize(str(item["text"]))
            token_counts = Counter(tokens)
            doc_freq.update(set(tokens))
            total_doc_length += len(tokens)
            cached = CachedLeafChunk(
                chunk_id=str(item["chunk_id"]),
                source=str(item["filename"]),
                title=str(item["title"]),
                excerpt=str(item["excerpt"]),
                text=str(item["text"]),
                level=int(item["level"]),
                parent_chunk_id=str(item["parent_chunk_id"]) if item.get("parent_chunk_id") else None,
                tokens=tokens,
                token_counts=token_counts,
                doc_length=len(tokens),
                embedding=self._parse_embedding(item.get("embedding", "")),
            )
            cached_chunks.append(cached)
            chunk_map[cached.chunk_id] = cached

        self._cached_leaf_chunks = cached_chunks
        self._chunk_map = chunk_map
        self._bm25_doc_freq = doc_freq
        self._total_leaf_docs = len(cached_chunks)
        self._bm25_avgdl = total_doc_length / max(len(cached_chunks), 1)
        self._cache_signature = signature
        self._cache_dataset_scope = dataset_scope
        return True

    def _generate_step_back_query(self, *, question: str, model: Any | None) -> str:
        if model is None:
            return ""
        # Step-Back rewrite: turn a concrete user question into a more abstract
        # legal issue so the retriever can match the governing rule instead of
        # overfitting to colloquial wording.
        prompt = """
你是法律检索改写助手。
请把用户的口语化问题改写成一条更适合检索法条的中文查询。
要求：
1. 保留用户原问题中的字面关键词。
2. 补足更正式的法律术语。
3. 突出法律关系、争议行为、权利义务或效力判断。
4. 只输出一条查询，不要解释。
        """.strip()
        return self._invoke_text(
            model,
            [
                SystemMessage(content=prompt),
                HumanMessage(content=question),
            ],
        )

    def _generate_keyword_query(self, *, question: str, model: Any | None) -> str:
        if model is not None:
            prompt = """
你是法律检索关键词助手。
请基于用户问题，输出一条适合法条搜索的中文关键词查询。
要求：
1. 必须保留用户原问题里的核心字面词。
2. 可以补充最多 3 个更正式的法律术语。
3. 优先加入能直接命中法条的短语，如“不动产物权”“登记效力”“所有权归属”。
4. 只输出查询本身，不要解释。
            """.strip()
            generated = self._invoke_text(
                model,
                [
                    SystemMessage(content=prompt),
                    HumanMessage(content=question),
                ],
            )
            if generated:
                return generated

        keywords = self._tokenize(question)
        priority_terms = [
            term
            for term in ("房屋", "登记", "所有权", "不动产", "物权", "效力", "归属")
            if term in question
        ]
        merged_terms = priority_terms + [token for token in keywords if len(token) > 1]
        deduped: list[str] = []
        for term in merged_terms:
            if term not in deduped:
                deduped.append(term)
        return " ".join(deduped[:8])

    def _generate_hyde_passage(self, *, question: str, model: Any | None) -> str:
        if model is None:
            return ""
        # HyDE (Hypothetical Document Embeddings): ask the model to draft a
        # short legal-style hypothetical answer, then use that generated text
        # as a semantic retrieval query.
        prompt = """
你正在帮助一个法律检索系统。
请生成一段简短的中文“假设性法律分析”，用于帮助召回更相关的法条。
要求：
1. 使用正式法律表述。
2. 可以提到权利义务、登记效力、责任承担、救济路径等。
3. 不要说明这是“假设”。
        """.strip()
        return self._invoke_text(
            model,
            [
                SystemMessage(content=prompt),
                HumanMessage(content=question),
            ],
        )

    def _grade_retrieval(
        self,
        *,
        question: str,
        hits: list[SearchHit],
        model: Any | None,
    ) -> tuple[bool, str]:
        if not hits:
            return False, "no chunks were retrieved"

        if model is None:
            top_score = hits[0].score
            return top_score >= 0.03, f"fallback score threshold={top_score:.3f}"

        context = "\n\n".join(
            f"[{index + 1}] {item.source} | {item.title}\n{item.excerpt}"
            for index, item in enumerate(hits[:3])
        )
        prompt = """
你是法律检索相关性评估器。
请判断给定片段能否直接或高度相关地支撑用户问题。
严格只输出一行：
YES | 简短理由
或
NO | 简短理由
""".strip()
        result = self._invoke_text(
            model,
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=f"Question:\n{question}\n\nRetrieved context:\n{context}",
                ),
            ],
        )
        normalized = result.strip().upper()
        if normalized.startswith("YES"):
            return True, result.split("|", 1)[-1].strip() if "|" in result else "retrieved context is relevant"
        return False, result.split("|", 1)[-1].strip() if "|" in result else "retrieved context is weak or off-topic"

    def _refuse_irrelevant_hits(
        self,
        *,
        question: str,
        hits: list[SearchHit],
        model: Any | None,
    ) -> list[SearchHit]:
        if not hits:
            return []
        accepted, _ = self._grade_retrieval(question=question, hits=hits[:4], model=model)
        if accepted:
            return hits
        if self._looks_non_legal(question):
            return []
        # Do not truncate by append order here. When the base query is weak but
        # keyword/step-back rewrites retrieve the right statute, early slicing
        # can accidentally drop the corrected hits before reranking.
        filtered = [item for item in hits if item.lexical_score > 0 or item.dense_score > 0.35]
        if not filtered:
            return []

        deduped = self._dedupe_remote_hits(
            filtered,
            top_k=max(16, settings.hybrid_candidate_pool * 2),
        )
        reranked = self._heuristic_rerank_remote_hits(question=question, hits=deduped)

        preserved: list[SearchHit] = []
        seen_pairs: set[tuple[str, str]] = set()
        for path in ("keyword", "step-back", "hyde", "base"):
            for item in reranked:
                key = (item.source, item.excerpt)
                if item.retrieval_path != path or key in seen_pairs:
                    continue
                preserved.append(item)
                seen_pairs.add(key)
                break

        for item in reranked:
            key = (item.source, item.excerpt)
            if key in seen_pairs:
                continue
            preserved.append(item)
            seen_pairs.add(key)

        return preserved[: max(8, settings.hybrid_candidate_pool)]

    def _rerank_remote_hits(
        self,
        *,
        question: str,
        hits: list[SearchHit],
        model: Any | None,
    ) -> list[SearchHit]:
        if not hits:
            return []

        deduped = self._dedupe_remote_hits(hits, top_k=max(8, settings.hybrid_candidate_pool))
        if len(deduped) <= 1:
            return deduped

        ranked_by_heuristic = self._heuristic_rerank_remote_hits(question=question, hits=deduped)
        if model is None:
            return ranked_by_heuristic

        candidate_lines = []
        for index, item in enumerate(ranked_by_heuristic[:8], start=1):
            candidate_lines.append(
                f"[{index}] {item.source}\n{self._truncate_text(item.excerpt, 220)}"
            )

        prompt = """
你是法律检索重排助手。
下面给你若干候选法条片段，请选出最能直接回答用户问题的片段编号。
优先级：
1. 能直接回答结论。
2. 能直接支撑结论的法条原文。
3. 与用户问题中的核心词、法律关系、登记效力或责任构成最贴近。

请只输出一个编号列表，例如：
2,1,4
不要解释。
        """.strip()
        response = self._invoke_text(
            model,
            [
                SystemMessage(content=prompt),
                HumanMessage(
                    content=f"用户问题：\n{question}\n\n候选片段：\n" + "\n\n".join(candidate_lines),
                ),
            ],
        )
        indices = [int(item) for item in re.findall(r"\d+", response)]
        if not indices:
            return ranked_by_heuristic

        picked: list[SearchHit] = []
        used: set[int] = set()
        for index in indices:
            if 1 <= index <= len(ranked_by_heuristic) and index not in used:
                used.add(index)
                picked.append(ranked_by_heuristic[index - 1])

        for index, item in enumerate(ranked_by_heuristic, start=1):
            if index not in used:
                picked.append(item)
        return picked

    def _heuristic_rerank_remote_hits(
        self,
        *,
        question: str,
        hits: list[SearchHit],
    ) -> list[SearchHit]:
        question_tokens = set(self._tokenize(question))
        anchor_terms = [
            term
            for term in ("房屋", "登记", "所有权", "不动产", "物权", "效力", "归属", "转让")
            if term in question
        ]
        answer_clues = ("未经登记", "不发生效力", "经依法登记", "不动产物权", "所有权")

        def score(hit: SearchHit) -> tuple[float, float]:
            excerpt_tokens = set(self._tokenize(hit.excerpt))
            overlap = len(question_tokens & excerpt_tokens)
            anchor_bonus = sum(2 for term in anchor_terms if term and term in hit.excerpt)
            clue_bonus = sum(3 for term in answer_clues if term in hit.excerpt)
            path_bonus = 0.03 if hit.retrieval_path in {"keyword", "step-back"} else 0.0

            return (
                hit.score
                + overlap * 0.02
                + anchor_bonus * 0.03
                + clue_bonus * 0.04
                + path_bonus
                ,
                hit.score,
            )

        return sorted(hits, key=score, reverse=True)

    def _auto_merge_hits(self, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        if not hits:
            return []

        # Auto-merging promotes small leaf hits back to their parent chunk when
        # the child text is too fragmentary (for example "前款规定的除外").
        # This keeps retrieval precise at the leaf level while giving the model
        # enough parent context to answer safely.
        by_parent: dict[str, list[SearchHit]] = defaultdict(list)
        singles: list[SearchHit] = []
        for hit in hits[: max(8, top_k * 3)]:
            if hit.parent_chunk_id:
                by_parent[hit.parent_chunk_id].append(hit)
            else:
                singles.append(hit)

        merged: list[SearchHit] = []
        consumed_parents: set[str] = set()
        for parent_id, children in by_parent.items():
            promote = len(children) >= 2 or any(self._needs_parent_context(item.excerpt) for item in children)
            if not promote:
                merged.extend(children[:1])
                continue

            parent = db.get_chunk_by_id(parent_id)
            if not parent:
                merged.extend(children[:1])
                continue

            consumed_parents.add(parent_id)
            merged.append(
                SearchHit(
                    source=str(parent["filename"]),
                    excerpt=self._truncate_text(str(parent["text"]), 800),
                    score=round(max(item.score for item in children) + 0.01, 4),
                    chunk_id=str(parent["chunk_id"]),
                    title=str(parent["title"]),
                    level=int(parent["level"]),
                    dense_score=max(item.dense_score for item in children),
                    lexical_score=max(item.lexical_score for item in children),
                    parent_chunk_id=str(parent["parent_chunk_id"]) if parent.get("parent_chunk_id") else None,
                    retrieval_path="auto-merged",
                )
            )

        merged.extend(item for item in singles if item.parent_chunk_id not in consumed_parents)
        merged.sort(key=lambda item: item.score, reverse=True)

        deduped: list[SearchHit] = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in merged:
            key = (item.source, item.title)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            deduped.append(item)
        return deduped[:top_k]

    def _build_chunk_hierarchy(
        self,
        *,
        document_id: int,
        filename: str,
        text: str,
    ) -> list[dict[str, Any]]:
        normalized = text.strip()
        if not normalized:
            return []

        # For legal text we build an explicit hierarchy instead of a flat chunk
        # list. In practice this becomes:
        # level 1: article group / section
        # level 2: article
        # level 3: leaf unit (clause / sentence)
        article_chunks = self._split_legal_articles(normalized)
        if article_chunks:
            return self._build_legal_chunks(document_id=document_id, filename=filename, articles=article_chunks)
        return self._build_generic_chunks(document_id=document_id, filename=filename, text=normalized)

    def _build_legal_chunks(
        self,
        *,
        document_id: int,
        filename: str,
        articles: list[str],
    ) -> list[dict[str, Any]]:
        # This is the "three-level chunking" used by the local legal fallback:
        # - L1 section/group
        # - L2 article
        # - L3 fine-grained leaf clause
        chunks: list[dict[str, Any]] = []
        groups = [articles[index : index + 4] for index in range(0, len(articles), 4)]
        ordinal = 0
        for group_index, group in enumerate(groups, start=1):
            section_id = f"chunk_{uuid4().hex}"
            section_title = f"{filename} / 法条分组 {group_index}"
            section_text = "\n\n".join(group)
            chunks.append(
                self._chunk_record(
                    chunk_id=section_id,
                    document_id=document_id,
                    filename=filename,
                    level=1,
                    parent_chunk_id=None,
                    root_chunk_id=section_id,
                    ordinal=ordinal,
                    title=section_title,
                    text=section_text,
                    is_leaf=False,
                )
            )
            ordinal += 1

            for article_index, article in enumerate(group, start=1):
                article_id = f"chunk_{uuid4().hex}"
                article_title = self._extract_article_title(article) or f"法条 {group_index}.{article_index}"
                chunks.append(
                    self._chunk_record(
                        chunk_id=article_id,
                        document_id=document_id,
                        filename=filename,
                        level=2,
                        parent_chunk_id=section_id,
                        root_chunk_id=section_id,
                        ordinal=ordinal,
                        title=article_title,
                        text=article,
                        is_leaf=False,
                    )
                )
                ordinal += 1

                leaf_units = self._split_article_into_leaf_units(article)
                for leaf_text in leaf_units:
                    leaf_id = f"chunk_{uuid4().hex}"
                    leaf_title = article_title
                    chunks.append(
                        self._chunk_record(
                            chunk_id=leaf_id,
                            document_id=document_id,
                            filename=filename,
                            level=3,
                            parent_chunk_id=article_id,
                            root_chunk_id=section_id,
                            ordinal=ordinal,
                            title=leaf_title,
                            text=leaf_text,
                            is_leaf=True,
                        )
                    )
                    ordinal += 1
        return chunks

    def _build_generic_chunks(
        self,
        *,
        document_id: int,
        filename: str,
        text: str,
    ) -> list[dict[str, Any]]:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", text) if item.strip()]
        if not paragraphs:
            paragraphs = [text]
        groups = [paragraphs[index : index + 4] for index in range(0, len(paragraphs), 4)]

        chunks: list[dict[str, Any]] = []
        ordinal = 0
        for group_index, group in enumerate(groups, start=1):
            section_id = f"chunk_{uuid4().hex}"
            section_text = "\n\n".join(group)
            chunks.append(
                self._chunk_record(
                    chunk_id=section_id,
                    document_id=document_id,
                    filename=filename,
                    level=1,
                    parent_chunk_id=None,
                    root_chunk_id=section_id,
                    ordinal=ordinal,
                    title=f"{filename} / section {group_index}",
                    text=section_text,
                    is_leaf=False,
                )
            )
            ordinal += 1

            for paragraph_index, paragraph in enumerate(group, start=1):
                paragraph_id = f"chunk_{uuid4().hex}"
                paragraph_title = f"Paragraph {group_index}.{paragraph_index}"
                chunks.append(
                    self._chunk_record(
                        chunk_id=paragraph_id,
                        document_id=document_id,
                        filename=filename,
                        level=2,
                        parent_chunk_id=section_id,
                        root_chunk_id=section_id,
                        ordinal=ordinal,
                        title=paragraph_title,
                        text=paragraph,
                        is_leaf=False,
                    )
                )
                ordinal += 1

                for sentence in self._split_generic_leaf_units(paragraph):
                    leaf_id = f"chunk_{uuid4().hex}"
                    chunks.append(
                        self._chunk_record(
                            chunk_id=leaf_id,
                            document_id=document_id,
                            filename=filename,
                            level=3,
                            parent_chunk_id=paragraph_id,
                            root_chunk_id=section_id,
                            ordinal=ordinal,
                            title=paragraph_title,
                            text=sentence,
                            is_leaf=True,
                        )
                    )
                    ordinal += 1
        return chunks

    @staticmethod
    def _chunk_record(
        *,
        chunk_id: str,
        document_id: int,
        filename: str,
        level: int,
        parent_chunk_id: str | None,
        root_chunk_id: str,
        ordinal: int,
        title: str,
        text: str,
        is_leaf: bool,
    ) -> dict[str, Any]:
        clean_text = " ".join(text.split())
        return {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "filename": filename,
            "level": level,
            "parent_chunk_id": parent_chunk_id,
            "root_chunk_id": root_chunk_id,
            "ordinal": ordinal,
            "title": title,
            "text": clean_text,
            "excerpt": HybridRetrievalPipeline._truncate_text(clean_text, 260),
            "embedding": "",
            "is_leaf": is_leaf,
        }

    @staticmethod
    def _split_legal_articles(text: str) -> list[str]:
        parts = re.split(r"(?=第[一二三四五六七八九十百千零〇0-9]+条)", text)
        articles = [item.strip() for item in parts if item.strip()]
        if len(articles) < 2:
            return []
        return articles

    @staticmethod
    def _extract_article_title(article: str) -> str:
        match = re.match(r"(第[一二三四五六七八九十百千零〇0-9]+条)", article)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _split_article_into_leaf_units(article: str) -> list[str]:
        article = article.strip()
        clauses = re.split(r"(?=(?:[（(][一二三四五六七八九十0-9]+[）)]))", article)
        clauses = [item.strip() for item in clauses if item.strip()]
        if len(clauses) > 1:
            return clauses

        sentences = re.split(r"(?<=[。；;])", article)
        units: list[str] = []
        buffer = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(buffer) + len(sentence) < 140:
                buffer = f"{buffer}{sentence}".strip()
                continue
            if buffer:
                units.append(buffer)
            buffer = sentence
        if buffer:
            units.append(buffer)
        return units or [article]

    @staticmethod
    def _split_generic_leaf_units(paragraph: str) -> list[str]:
        sentences = [item.strip() for item in re.split(r"(?<=[。！？.!?])", paragraph) if item.strip()]
        if not sentences:
            return [paragraph]
        units: list[str] = []
        buffer = ""
        for sentence in sentences:
            if len(buffer) + len(sentence) < 180:
                buffer = f"{buffer} {sentence}".strip()
                continue
            if buffer:
                units.append(buffer)
            buffer = sentence
        if buffer:
            units.append(buffer)
        return units

    def _get_embedding_client(self) -> Any | None:
        if self._embedding_client is not None:
            return self._embedding_client

        provider = settings.embedding_provider
        if provider == "gemini" and settings.google_api_key:
            try:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings

                self._embedding_client = GoogleGenerativeAIEmbeddings(
                    model=settings.embedding_model,
                    google_api_key=settings.google_api_key,
                )
                return self._embedding_client
            except Exception:
                return None

        if provider == "openai" and settings.api_key:
            try:
                from langchain_openai import OpenAIEmbeddings

                self._embedding_client = OpenAIEmbeddings(
                    model=settings.embedding_model,
                    api_key=settings.api_key,
                    base_url=settings.base_url or None,
                )
                return self._embedding_client
            except Exception:
                return None

        return None

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        client = self._get_embedding_client()
        if client is None or not texts:
            return [[] for _ in texts]

        try:
            return client.embed_documents(texts)
        except Exception:
            return [[] for _ in texts]

    def _embed_query(self, text: str) -> list[float]:
        client = self._get_embedding_client()
        if client is None or not text.strip():
            return []
        try:
            return client.embed_query(text)
        except Exception:
            return []

    @staticmethod
    def _parse_embedding(raw: Any) -> list[float]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [float(item) for item in raw]
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [float(item) for item in parsed]
        return []

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        lowered = text.lower()
        alpha = re.findall(r"[a-z0-9]{2,}", lowered)
        chinese_sequences = re.findall(r"[\u4e00-\u9fff]{1,}", lowered)
        chinese_terms: list[str] = []
        for sequence in chinese_sequences:
            if len(sequence) == 1:
                chinese_terms.append(sequence)
                continue
            chinese_terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
        return alpha + chinese_terms

    @staticmethod
    def _sorted_ranking(scores: dict[str, float]) -> list[str]:
        return [key for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]

    @staticmethod
    def _rrf_fuse(rankings: Iterable[list[str]], rank_constant: int = 60) -> dict[str, float]:
        fused: dict[str, float] = defaultdict(float)
        for ranking in rankings:
            for rank, item_id in enumerate(ranking, start=1):
                fused[item_id] += 1 / (rank_constant + rank)
        return dict(fused)

    @staticmethod
    def _needs_parent_context(text: str) -> bool:
        clues = ("前款", "本条", "本章", "前项", "该款", "下列", "前述")
        if len(text) < 90:
            return True
        return any(keyword in text for keyword in clues)

    @staticmethod
    def _looks_non_legal(question: str) -> bool:
        keywords = ("天气", "股票", "NBA", "足球", "代码报错", "餐厅", "旅游", "电影")
        return any(keyword in question for keyword in keywords)

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        return text if len(text) <= max_chars else f"{text[:max_chars]}..."

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
                    parts.append(str(item.get("text", "")))
                else:
                    text = getattr(item, "text", None)
                    parts.append(str(text if text is not None else item))
            return "".join(parts).strip()
        if isinstance(content, dict):
            if "text" in content:
                return str(content["text"]).strip()
        return str(content).strip()

    @classmethod
    def _invoke_text(cls, model: Any, messages: list[Any]) -> str:
        response = model.invoke(messages)
        content = getattr(response, "content", response)
        return cls._normalize_content(content)

    @staticmethod
    def _emit(callback: TraceCallback, step: str, detail: str) -> None:
        if callback:
            callback(step, detail)
