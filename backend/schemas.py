from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    id: int
    filename: str
    preview: str
    indexed_with: str
    created_at: str


class DocumentUploadResponse(BaseModel):
    message: str
    document: DocumentOut


class MessageResponse(BaseModel):
    message: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None


class SourceItem(BaseModel):
    source: str
    excerpt: str
    score: float
    title: str | None = None
    retrieval_path: str | None = None


class TraceStep(BaseModel):
    step: str
    detail: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    sources: list[SourceItem]
    trace: list[TraceStep]


class AppStatus(BaseModel):
    ok: bool
    ragflow_enabled: bool
    langsmith_enabled: bool


class SessionListItem(BaseModel):
    session_id: str
    title: str
    last_message: str
    last_role: str | None = None
    message_count: int
    updated_at: str
    summary: str = ""
    has_summary: bool = False


class SessionMessage(BaseModel):
    id: int
    role: str
    content: str
    created_at: str


class SessionDetailResponse(BaseModel):
    session_id: str
    title: str
    message_count: int
    updated_at: str
    summary: str = ""
    messages: list[SessionMessage]
