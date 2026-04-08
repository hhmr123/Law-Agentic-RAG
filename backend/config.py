from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    root_dir: Path = Path(__file__).resolve().parents[1]
    data_dir: Path = root_dir / "data"
    upload_dir: Path = data_dir / "uploads"

    model_provider: str = os.getenv("MODEL_PROVIDER", "openai").strip().lower()
    api_key: str = os.getenv("ARK_API_KEY", "")
    google_api_key: str = os.getenv(
        "GOOGLE_API_KEY",
        os.getenv("GEMINI_API_KEY", os.getenv("ARK_API_KEY", "")),
    ).strip()
    model: str = os.getenv("MODEL", "gpt-4o-mini")
    base_url: str = os.getenv("BASE_URL", "").strip().strip("'").strip('"')
    embedding_provider: str = os.getenv("EMBEDDING_PROVIDER", "").strip().lower() or model_provider
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004").strip()

    ragflow_enabled: bool = _as_bool(os.getenv("RAGFLOW_ENABLED"), False)
    ragflow_base_url: str = os.getenv("RAGFLOW_BASE_URL", "").rstrip("/")
    ragflow_api_key: str = os.getenv("RAGFLOW_API_KEY", "")
    ragflow_search_path: str = os.getenv("RAGFLOW_SEARCH_PATH", "/api/v1/retrieval")
    ragflow_ingest_path: str = os.getenv("RAGFLOW_INGEST_PATH", "/api/v1/documents")
    ragflow_dataset_id: str = os.getenv("RAGFLOW_DATASET_ID", "")
    ragflow_dataset_name: str = os.getenv("RAGFLOW_DATASET_NAME", "MY_agent")
    ragflow_dataset_chunk_method: str = os.getenv(
        "RAGFLOW_DATASET_CHUNK_METHOD",
        "laws",
    ).strip().lower()
    ragflow_dataset_description: str = os.getenv(
        "RAGFLOW_DATASET_DESCRIPTION",
        "Dataset auto-created by MY_agent for legal documents.",
    ).strip()
    ragflow_auto_create_dataset: bool = _as_bool(
        os.getenv("RAGFLOW_AUTO_CREATE_DATASET"),
        True,
    )
    agent_tool_mode: str = os.getenv("AGENT_TOOL_MODE", "auto").strip().lower()
    hybrid_rrf_k: int = int(os.getenv("HYBRID_RRF_K", "60"))
    hybrid_candidate_pool: int = int(os.getenv("HYBRID_CANDIDATE_POOL", "8"))
    summary_trigger_messages: int = int(os.getenv("SUMMARY_TRIGGER_MESSAGES", "10"))
    summary_keep_recent: int = int(os.getenv("SUMMARY_KEEP_RECENT", "6"))

    langsmith_api_key: str = os.getenv("LANGSMITH_API_KEY", "")
    langsmith_project: str = os.getenv("LANGSMITH_PROJECT", "MY_agent")
    langsmith_tracing: bool = _as_bool(os.getenv("LANGSMITH_TRACING"), True)

    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:5173")
    sqlite_path: Path = root_dir / os.getenv("SQLITE_PATH", "data/app.db")


settings = Settings()


def prepare_environment() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        os.environ.setdefault(
            "LANGSMITH_TRACING",
            "true" if settings.langsmith_tracing else "false",
        )
