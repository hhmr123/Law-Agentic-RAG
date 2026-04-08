from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend import db
from backend.agent_service import AgentService
from backend.config import prepare_environment, settings
from backend.document_utils import build_preview, extract_text
from backend.ragflow_gateway import RAGFlowGateway
from backend.retrieval_pipeline import HybridRetrievalPipeline
from backend.schemas import (
    AppStatus,
    ChatRequest,
    ChatResponse,
    DocumentOut,
    DocumentUploadResponse,
    MessageResponse,
    SessionDetailResponse,
    SessionListItem,
)

prepare_environment()
db.init_db()

gateway = RAGFlowGateway()
retriever = HybridRetrievalPipeline(gateway=gateway)
agent_service = AgentService(gateway=gateway, retriever=retriever)

app = FastAPI(title="MY_agent API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def warm_local_retrieval_cache() -> None:
    if not gateway.enabled:
        threading.Thread(target=retriever.prewarm, daemon=True).start()


@app.get("/api/health", response_model=AppStatus)
def health() -> AppStatus:
    return AppStatus(
        ok=True,
        ragflow_enabled=gateway.enabled,
        langsmith_enabled=bool(settings.langsmith_api_key and settings.langsmith_tracing),
    )


@app.get("/api/documents", response_model=list[DocumentOut])
def list_documents() -> list[DocumentOut]:
    return [DocumentOut(**item) for item in db.list_documents()]


@app.get("/api/sessions", response_model=list[SessionListItem])
def list_sessions() -> list[SessionListItem]:
    return [SessionListItem(**item) for item in db.list_sessions()]


@app.get("/api/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session(session_id: str) -> SessionDetailResponse:
    session = db.get_session_detail(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return SessionDetailResponse(**session)


@app.delete("/api/sessions/{session_id}", response_model=MessageResponse)
def delete_session(session_id: str) -> MessageResponse:
    deleted = db.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return MessageResponse(message=f"已删除会话 {session_id}")


@app.post("/api/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空。")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空。")

    safe_name = Path(file.filename).name
    stored_name = f"{uuid4().hex}_{safe_name}"
    stored_path = settings.upload_dir / stored_name
    stored_path.write_bytes(content)

    extracted_text = extract_text(safe_name, content)
    preview = build_preview(extracted_text, safe_name)
    index_result = gateway.index_document(safe_name, stored_path, extracted_text)
    document = db.insert_document(
        filename=safe_name,
        stored_path=str(stored_path),
        extracted_text=extracted_text,
        preview=preview,
        indexed_with=index_result.mode,
        ragflow_document_id=index_result.primary_document_id,
        ragflow_dataset_id=index_result.dataset_id,
    )
    local_index_mode = retriever.index_document(
        document_id=int(document["id"]),
        filename=safe_name,
        text=extracted_text,
    )
    db.update_document_indexed_with(
        int(document["id"]),
        f"{index_result.mode} + {local_index_mode}",
    )
    refreshed = db.get_document(int(document["id"])) or document

    return DocumentUploadResponse(
        message="文档上传成功。",
        document=DocumentOut(**refreshed),
    )


@app.delete("/api/documents/{document_id}", response_model=MessageResponse)
def delete_document(document_id: int) -> MessageResponse:
    existing = db.get_document(document_id)
    if not existing:
        raise HTTPException(status_code=404, detail="文档不存在。")

    remote_deleted = False
    if gateway.enabled:
        remote_deleted = gateway.delete_document(
            remote_document_id=str(existing.get("ragflow_document_id", "") or ""),
            filename=str(existing.get("filename", "") or ""),
            dataset_id=str(existing.get("ragflow_dataset_id", "") or ""),
        )

    document = db.delete_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在。")
    retriever.invalidate_cache()

    stored_path = Path(str(document.get("stored_path", "")))
    if stored_path.exists() and stored_path.is_file():
        stored_path.unlink(missing_ok=True)

    if gateway.enabled:
        if remote_deleted:
            return MessageResponse(message=f"已删除文档，并同步删除 RAGFlow 远程文档: {document['filename']}")
        return MessageResponse(message=f"已删除本地文档，但未能确认删除 RAGFlow 远程文档: {document['filename']}")
    return MessageResponse(message=f"已删除文档: {document['filename']}")


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id or f"session_{uuid4().hex}"
    try:
        result = agent_service.answer(message=request.message, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"聊天接口执行失败: {exc}") from exc

    return ChatResponse(**result)


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    session_id = request.session_id or f"session_{uuid4().hex}"
    events: queue.Queue[dict[str, object]] = queue.Queue()

    def emit(item: dict[str, str]) -> None:
        events.put({"type": "trace", "data": item})

    def worker() -> None:
        try:
            result = agent_service.answer(
                message=request.message,
                session_id=session_id,
                event_callback=emit,
            )
            events.put({"type": "result", "data": result})
        except Exception as exc:
            events.put({"type": "error", "data": {"detail": str(exc)}})
        finally:
            events.put({"type": "done"})

    def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        while True:
            event = events.get()
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event.get("type") == "done":
                break

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
