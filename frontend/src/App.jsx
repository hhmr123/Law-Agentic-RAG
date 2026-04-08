import { useEffect, useMemo, useState } from "react";
import {
  askAgentStream,
  deleteDocument,
  deleteSession,
  fetchDocuments,
  fetchHealth,
  fetchSessionDetail,
  fetchSessions,
  uploadDocument,
} from "./api";

function createSessionId() {
  return crypto.randomUUID();
}

function getLastAssistantMessage(messages) {
  const reversed = [...messages].reverse();
  return reversed.find((item) => item.role === "assistant")?.content || "";
}

function App() {
  const [health, setHealth] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState(() => createSessionId());
  const [chatHistory, setChatHistory] = useState([]);
  const [sessionSummary, setSessionSummary] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [trace, setTrace] = useState([]);
  const [busy, setBusy] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);
  const [error, setError] = useState("");
  const [initialLoading, setInitialLoading] = useState(true);

  useEffect(() => {
    loadInitialData();
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      loadInitialData({ silent: true, keepSessionId: sessionId });
    }, 8000);
    return () => window.clearInterval(intervalId);
  }, [sessionId]);

  const visibleSessions = useMemo(() => {
    const alreadyListed = sessions.some((item) => item.session_id === sessionId);
    if (alreadyListed) {
      return sessions;
    }
    return [
      {
        session_id: sessionId,
        title: "新会话",
        last_message: "等待你的第一条问题",
        last_role: "user",
        message_count: chatHistory.length,
        updated_at: new Date().toISOString(),
        summary: "",
        has_summary: false,
        isDraft: true,
      },
      ...sessions,
    ];
  }, [chatHistory.length, sessionId, sessions]);

  async function loadInitialData(options = {}) {
    const { silent = false, keepSessionId = "" } = options;
    try {
      if (!silent) {
        setInitialLoading(true);
      }
      const [healthData, documentData, sessionData] = await Promise.all([
        fetchHealth(),
        fetchDocuments(),
        fetchSessions(),
      ]);
      setHealth(healthData);
      setDocuments(documentData);
      setSessions(sessionData);
      setError("");

      const targetSessionId = keepSessionId || sessionId;
      const sessionStillExists = sessionData.some(
        (item) => item.session_id === targetSessionId,
      );

      if (sessionStillExists) {
        return;
      }

      const isUnsavedDraft =
        targetSessionId === sessionId &&
        chatHistory.length === 0 &&
        !answer &&
        trace.length === 0;
      if (silent && isUnsavedDraft) {
        return;
      }

      if (sessionData.length > 0) {
        await handleSelectSession(sessionData[0].session_id, { suppressErrors: true });
      }
    } catch (err) {
      if (!silent) {
        setError(err.message);
      }
    } finally {
      if (!silent) {
        setInitialLoading(false);
      }
    }
  }

  async function handleSelectSession(targetSessionId, options = {}) {
    const { preserveOutputs = false, suppressErrors = false } = options;
    const targetSession = sessions.find((item) => item.session_id === targetSessionId);
    const isCurrentDraft =
      !targetSession &&
      targetSessionId === sessionId &&
      chatHistory.length === 0 &&
      !answer &&
      trace.length === 0;

    if (isCurrentDraft) {
      setSessionId(targetSessionId);
      setChatHistory([]);
      setSessionSummary("");
      if (!preserveOutputs) {
        setAnswer("");
        setSources([]);
        setTrace([]);
      }
      return;
    }

    try {
      setSessionLoading(true);
      const detail = await fetchSessionDetail(targetSessionId);
      setSessionId(detail.session_id);
      setChatHistory(detail.messages || []);
      setSessionSummary(detail.summary || "");
      if (!preserveOutputs) {
        setAnswer(getLastAssistantMessage(detail.messages || []));
        setSources([]);
        setTrace([]);
      }
      setError("");
    } catch (err) {
      if (!suppressErrors) {
        setError(err.message);
      }
    } finally {
      setSessionLoading(false);
    }
  }

  function handleCreateSession() {
    setSessionId(createSessionId());
    setChatHistory([]);
    setSessionSummary("");
    setQuestion("");
    setAnswer("");
    setSources([]);
    setTrace([]);
    setError("");
  }

  async function handleDeleteSession(item) {
    if (item.isDraft) {
      handleCreateSession();
      return;
    }

    const shouldDelete = window.confirm(`确认删除会话“${item.title}”吗？`);
    if (!shouldDelete) {
      return;
    }

    try {
      setBusy(true);
      setError("");
      await deleteSession(item.session_id);
      const remaining = sessions.filter((session) => session.session_id !== item.session_id);
      setSessions(remaining);

      if (item.session_id === sessionId) {
        if (remaining.length > 0) {
          await handleSelectSession(remaining[0].session_id, { suppressErrors: true });
        } else {
          handleCreateSession();
        }
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(event) {
    event.preventDefault();
    if (!selectedFile) {
      setError("请先选择一个文件。");
      return;
    }

    try {
      setBusy(true);
      setError("");
      await uploadDocument(selectedFile);
      setSelectedFile(null);
      await loadInitialData();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteDocument(document) {
    const shouldDelete = window.confirm(`确认删除文档“${document.filename}”吗？`);
    if (!shouldDelete) {
      return;
    }

    try {
      setBusy(true);
      setError("");
      await deleteDocument(document.id);
      await loadInitialData();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAsk(event) {
    event.preventDefault();
    if (!question.trim()) {
      setError("请输入问题。");
      return;
    }

    let resolvedSessionId = sessionId;

    try {
      setBusy(true);
      setError("");
      setAnswer("");
      setSources([]);
      setTrace([]);

      await askAgentStream({
        message: question.trim(),
        sessionId,
        onEvent: (eventPayload) => {
          if (eventPayload.type === "trace") {
            setTrace((current) => [...current, eventPayload.data]);
            return;
          }
          if (eventPayload.type === "result") {
            const result = eventPayload.data;
            resolvedSessionId = result.session_id || resolvedSessionId;
            setAnswer(result.answer || "");
            setSources(result.sources || []);
            setTrace(result.trace || []);
            setSessionId(resolvedSessionId);
            return;
          }
          if (eventPayload.type === "error") {
            setError(eventPayload.data?.detail || "请求失败");
          }
        },
      });

      await loadInitialData({ silent: true, keepSessionId: resolvedSessionId });
      await handleSelectSession(resolvedSessionId, {
        preserveOutputs: true,
        suppressErrors: true,
      });
      setQuestion("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-shell">
      <aside className="sidebar">
        <div className="brand-card">
          <p className="eyebrow">Legal Agentic RAG</p>
          <h1>MY_agent</h1>
          <p className="brand-copy">
            这版前端已经支持法律场景的 Agentic RAG 演示，包括混合检索、查询重写、
            相关性门控、执行轨迹展示，以及本地与 RAGFlow 同步的文档管理。
          </p>
        </div>

        <section className="panel">
          <div className="panel-header">
            <h2>会话管理</h2>
            <button type="button" onClick={handleCreateSession} disabled={busy}>
              新建会话
            </button>
          </div>
          <div className="session-list">
            {visibleSessions.map((item) => (
              <article
                key={item.session_id}
                className={`session-card ${item.session_id === sessionId ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="session-select-button"
                  disabled={busy || sessionLoading}
                  onClick={() => handleSelectSession(item.session_id)}
                >
                  <strong>{item.title}</strong>
                  <p>{item.last_message || "还没有消息"}</p>
                  <div className="session-meta">
                    <span>{item.message_count} 条消息</span>
                    <span>{item.has_summary ? "有摘要" : "原始会话"}</span>
                  </div>
                </button>
                <button
                  type="button"
                  className="ghost-danger-button"
                  disabled={busy}
                  onClick={() => handleDeleteSession(item)}
                >
                  删除
                </button>
              </article>
            ))}
          </div>
        </section>

        <section className="panel">
          <h2>系统状态</h2>
          <div className="status-grid">
            <div className="status-item">
              <span>API</span>
              <strong>
                {initialLoading ? "加载中" : health?.ok ? "在线" : "暂时不可用"}
              </strong>
            </div>
            <div className="status-item">
              <span>RAGFlow</span>
              <strong>
                {initialLoading ? "加载中" : health?.ragflow_enabled ? "已接入" : "未启用"}
              </strong>
            </div>
            <div className="status-item">
              <span>LangSmith</span>
              <strong>
                {initialLoading ? "加载中" : health?.langsmith_enabled ? "已追踪" : "未配置"}
              </strong>
            </div>
          </div>
          <div className="status-actions">
            <button
              type="button"
              onClick={() => loadInitialData()}
              disabled={busy || initialLoading}
            >
              {initialLoading ? "刷新中..." : "刷新状态"}
            </button>
          </div>
        </section>

        <section className="panel">
          <h2>上传文档</h2>
          <form onSubmit={handleUpload} className="stack">
            <input
              type="file"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
            <button type="submit" disabled={busy}>
              {busy ? "处理中..." : "上传并索引"}
            </button>
          </form>
        </section>

        <section className="panel">
          <h2>已上传文档</h2>
          <div className="document-list">
            {documents.length === 0 ? (
              <p className="empty">
                {initialLoading
                  ? "正在加载文档列表..."
                  : "还没有文档，先上传法律文本、简历或论文试试。"}
              </p>
            ) : (
              documents.map((doc) => (
                <article key={doc.id} className="document-card">
                  <div className="document-header">
                    <strong>{doc.filename}</strong>
                    <span>{doc.indexed_with}</span>
                  </div>
                  <p>{doc.preview}</p>
                  <div className="document-actions">
                    <button
                      type="button"
                      className="danger-button"
                      disabled={busy}
                      onClick={() => handleDeleteDocument(doc)}
                    >
                      删除
                    </button>
                  </div>
                </article>
              ))
            )}
          </div>
        </section>
      </aside>

      <main className="main-column">
        <section className="hero">
          <p className="eyebrow">Interview-ready architecture</p>
          <h2>把检索准确性、上下文完整性和实时反馈一起做好</h2>
          <p>
            现在这版不是简单的“上传后问一句”，而是一条完整的法律 RAG
            链路：检索、重写、门控、合并、生成和追踪都能在页面上看见。
          </p>
        </section>

        <section className="panel">
          <div className="panel-header">
            <h2>当前会话</h2>
            <span className="session-text">{sessionId}</span>
          </div>
          {sessionSummary ? (
            <div className="summary-box">
              <strong>会话摘要</strong>
              <p>{sessionSummary}</p>
            </div>
          ) : null}
          <div className="history-list">
            {sessionLoading ? (
              <p className="empty">正在加载会话历史...</p>
            ) : chatHistory.length === 0 ? (
              <p className="empty">当前会话还没有历史消息。</p>
            ) : (
              chatHistory.map((item) => (
                <div
                  key={item.id}
                  className={`message-bubble ${item.role === "user" ? "user" : "assistant"}`}
                >
                  <span className="message-role">
                    {item.role === "user" ? "用户" : "助手"}
                  </span>
                  <p>{item.content}</p>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="panel">
          <h2>向 Agent 提问</h2>
          <form onSubmit={handleAsk} className="stack">
            <textarea
              rows="5"
              placeholder="例如：关于定金罚则有什么规定？或者：我买的二手车被调表了，我能要求退一赔三吗？"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <div className="toolbar">
              <span className="session-text">Session: {sessionId}</span>
              <button type="submit" disabled={busy}>
                {busy ? "推理中..." : "发送问题"}
              </button>
            </div>
          </form>
          {error ? <p className="error-text">{error}</p> : null}
        </section>

        <section className="panel result-panel">
          <div className="result-grid">
            <div>
              <h2>回答</h2>
              <div className="answer-box">{answer || "答案会显示在这里。"}</div>
            </div>

            <div>
              <h2>来源</h2>
              <div className="source-list">
                {sources.length === 0 ? (
                  <p className="empty">当前回答还没有检索到可展示的来源。</p>
                ) : (
                  sources.map((item, index) => (
                    <article
                      key={`${item.source}-${item.title}-${index}`}
                      className="source-card"
                    >
                      <div className="source-header">
                        <strong>{item.source}</strong>
                        <span>score {item.score}</span>
                      </div>
                      {item.title ? (
                        <p>
                          <strong>{item.title}</strong>
                        </p>
                      ) : null}
                      <p>{item.excerpt}</p>
                    </article>
                  ))
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="panel">
          <h2>执行轨迹</h2>
          <div className="trace-list">
            {trace.length === 0 ? (
              <p className="empty">
                发起一次提问后，这里会实时展示 Agent 的执行过程。
              </p>
            ) : (
              trace.map((item, index) => (
                <div key={`${item.step}-${index}`} className="trace-item">
                  <strong>{item.step}</strong>
                  <p>{item.detail}</p>
                </div>
              ))
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
