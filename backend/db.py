from __future__ import annotations

import sqlite3
from typing import Any

from backend.config import settings


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.sqlite_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                extracted_text TEXT NOT NULL,
                preview TEXT NOT NULL,
                indexed_with TEXT NOT NULL,
                ragflow_document_id TEXT,
                ragflow_dataset_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS retrieval_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chunk_id TEXT NOT NULL UNIQUE,
                document_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                level INTEGER NOT NULL,
                parent_chunk_id TEXT,
                root_chunk_id TEXT,
                ordinal INTEGER NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                embedding TEXT NOT NULL DEFAULT '',
                is_leaf INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS session_summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id
                ON chat_messages(session_id, id);

            CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_document
                ON retrieval_chunks(document_id, level, ordinal);

            CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_parent
                ON retrieval_chunks(parent_chunk_id);

            CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_leaf
                ON retrieval_chunks(is_leaf, filename);
            """
        )
        _ensure_column(connection, "documents", "ragflow_document_id", "TEXT")
        _ensure_column(connection, "documents", "ragflow_dataset_id", "TEXT")


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in existing_columns:
        return
    connection.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
    )


def insert_document(
    *,
    filename: str,
    stored_path: str,
    extracted_text: str,
    preview: str,
    indexed_with: str,
    ragflow_document_id: str = "",
    ragflow_dataset_id: str = "",
) -> dict[str, Any]:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (
                filename,
                stored_path,
                extracted_text,
                preview,
                indexed_with,
                ragflow_document_id,
                ragflow_dataset_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                stored_path,
                extracted_text,
                preview,
                indexed_with,
                ragflow_document_id,
                ragflow_dataset_id,
            ),
        )
        row_id = cursor.lastrowid
        row = connection.execute(
            """
            SELECT id, filename, preview, indexed_with, ragflow_document_id, ragflow_dataset_id, created_at
            FROM documents
            WHERE id = ?
            """,
            (row_id,),
        ).fetchone()
    return dict(row)


def update_document_indexed_with(document_id: int, indexed_with: str) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE documents SET indexed_with = ? WHERE id = ?",
            (indexed_with, document_id),
        )


def update_document_ragflow_binding(
    document_id: int,
    *,
    ragflow_document_id: str,
    ragflow_dataset_id: str,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE documents
            SET ragflow_document_id = ?, ragflow_dataset_id = ?
            WHERE id = ?
            """,
            (ragflow_document_id, ragflow_dataset_id, document_id),
        )


def get_document(document_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, filename, stored_path, extracted_text, preview, indexed_with,
                   ragflow_document_id, ragflow_dataset_id, created_at
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_document(document_id: int) -> dict[str, Any] | None:
    existing = get_document(document_id)
    if not existing:
        return None

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM retrieval_chunks WHERE document_id = ?",
            (document_id,),
        )
        connection.execute(
            "DELETE FROM documents WHERE id = ?",
            (document_id,),
        )
    return existing


def list_documents(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, filename, preview, indexed_with, ragflow_document_id, ragflow_dataset_id, created_at
            FROM documents
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_documents_with_text(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, filename, preview, extracted_text, stored_path, indexed_with,
                   ragflow_document_id, ragflow_dataset_id, created_at
            FROM documents
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_document_chunks(document_id: int, chunks: list[dict[str, Any]]) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM retrieval_chunks WHERE document_id = ?",
            (document_id,),
        )
        connection.executemany(
            """
            INSERT INTO retrieval_chunks (
                chunk_id,
                document_id,
                filename,
                level,
                parent_chunk_id,
                root_chunk_id,
                ordinal,
                title,
                text,
                excerpt,
                embedding,
                is_leaf
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["chunk_id"],
                    item["document_id"],
                    item["filename"],
                    item["level"],
                    item.get("parent_chunk_id"),
                    item.get("root_chunk_id"),
                    item["ordinal"],
                    item["title"],
                    item["text"],
                    item["excerpt"],
                    item.get("embedding", ""),
                    1 if item.get("is_leaf") else 0,
                )
                for item in chunks
            ],
        )


def get_document_chunks(document_id: int) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT chunk_id, document_id, filename, level, parent_chunk_id, root_chunk_id,
                   ordinal, title, text, excerpt, embedding, is_leaf
            FROM retrieval_chunks
            WHERE document_id = ?
            ORDER BY level ASC, ordinal ASC
            """,
            (document_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def document_has_chunks(document_id: int) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM retrieval_chunks WHERE document_id = ? LIMIT 1",
            (document_id,),
        ).fetchone()
    return bool(row)


def list_leaf_chunks(limit: int | None = None, dataset_id: str | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT rc.chunk_id, rc.document_id, rc.filename, rc.level, rc.parent_chunk_id, rc.root_chunk_id,
               rc.ordinal, rc.title, rc.text, rc.excerpt, rc.embedding, rc.is_leaf
        FROM retrieval_chunks rc
        JOIN documents d ON d.id = rc.document_id
        WHERE rc.is_leaf = 1
    """
    params: list[Any] = []
    if dataset_id:
        query += " AND COALESCE(d.ragflow_dataset_id, '') = ?"
        params.append(dataset_id)
    query += " ORDER BY rc.id ASC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection() as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def get_leaf_index_signature(dataset_id: str | None = None) -> tuple[int, int]:
    query = """
        SELECT COUNT(*) AS count, COALESCE(MAX(rc.id), 0) AS max_id
        FROM retrieval_chunks rc
        JOIN documents d ON d.id = rc.document_id
        WHERE rc.is_leaf = 1
    """
    params: tuple[Any, ...] = ()
    if dataset_id:
        query += " AND COALESCE(d.ragflow_dataset_id, '') = ?"
        params = (dataset_id,)
    with get_connection() as connection:
        row = connection.execute(
            query,
            params,
        ).fetchone()
    if not row:
        return (0, 0)
    return (int(row["count"]), int(row["max_id"]))


def get_chunk_by_id(chunk_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT chunk_id, document_id, filename, level, parent_chunk_id, root_chunk_id,
                   ordinal, title, text, excerpt, embedding, is_leaf
            FROM retrieval_chunks
            WHERE chunk_id = ?
            """,
            (chunk_id,),
        ).fetchone()
    return dict(row) if row else None


def get_parent_chain(chunk_id: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    current = get_chunk_by_id(chunk_id)
    while current and current.get("parent_chunk_id"):
        parent = get_chunk_by_id(str(current["parent_chunk_id"]))
        if not parent:
            break
        chain.append(parent)
        current = parent
    return chain


def save_chat_message(session_id: str, role: str, content: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (?, ?, ?)
            """,
            (session_id, role, content),
        )


def count_session_messages(session_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["count"]) if row else 0


def get_session_messages(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, role, content, created_at
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    items = [dict(row) for row in rows]
    items.reverse()
    return items


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                cm.session_id,
                COUNT(*) AS message_count,
                MAX(cm.id) AS last_message_id,
                MAX(cm.created_at) AS updated_at,
                (
                    SELECT content
                    FROM chat_messages first_user
                    WHERE first_user.session_id = cm.session_id
                      AND first_user.role = 'user'
                    ORDER BY first_user.id ASC
                    LIMIT 1
                ) AS title,
                (
                    SELECT content
                    FROM chat_messages last_msg
                    WHERE last_msg.session_id = cm.session_id
                    ORDER BY last_msg.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT role
                    FROM chat_messages last_role
                    WHERE last_role.session_id = cm.session_id
                    ORDER BY last_role.id DESC
                    LIMIT 1
                ) AS last_role,
                ss.summary,
                ss.updated_at AS summary_updated_at
            FROM chat_messages cm
            LEFT JOIN session_summaries ss ON ss.session_id = cm.session_id
            GROUP BY cm.session_id
            ORDER BY last_message_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            **dict(row),
            "title": _preview_text(str(row["title"] or "新会话"), 36),
            "last_message": _preview_text(str(row["last_message"] or ""), 80),
            "summary": str(row["summary"] or ""),
            "has_summary": bool(row["summary"]),
        }
        for row in rows
    ]


def get_session_detail(session_id: str) -> dict[str, Any] | None:
    messages = get_session_messages(session_id, limit=200)
    if not messages:
        return None

    summary = get_session_summary(session_id)
    title = next((item["content"] for item in messages if item["role"] == "user"), "新会话")
    return {
        "session_id": session_id,
        "title": _preview_text(str(title), 36),
        "message_count": len(messages),
        "summary": str(summary["summary"]) if summary else "",
        "updated_at": str(messages[-1]["created_at"]),
        "messages": messages,
    }


def delete_session(session_id: str) -> bool:
    if not get_session_detail(session_id):
        return False

    with get_connection() as connection:
        connection.execute(
            "DELETE FROM chat_messages WHERE session_id = ?",
            (session_id,),
        )
        connection.execute(
            "DELETE FROM session_summaries WHERE session_id = ?",
            (session_id,),
        )
    return True


def get_session_summary(session_id: str) -> dict[str, Any] | None:
    # Session summaries are persisted separately from raw chat messages so the
    # backend can keep long conversations compact without losing the recent
    # dialogue turns.
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT session_id, summary, message_count, updated_at
            FROM session_summaries
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def upsert_session_summary(session_id: str, summary: str, message_count: int) -> None:
    # The summary stores "older context compressed into one note" plus the
    # message count it corresponds to, so we know whether the summary is stale.
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO session_summaries (session_id, summary, message_count, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                summary = excluded.summary,
                message_count = excluded.message_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, summary, message_count),
        )


def _preview_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars]}..."
