from __future__ import annotations

from pathlib import Path

from backend import db
from backend.ragflow_gateway import RAGFlowGateway


def main() -> None:
    gateway = RAGFlowGateway()
    if not gateway.enabled:
        raise SystemExit("RAGFlow 未启用，请先检查 .env 里的 RAGFLOW_* 配置。")

    documents = db.list_documents_with_text(limit=500)
    if not documents:
        print("没有可同步的本地文档。")
        return

    synced = 0
    failed = 0
    for doc in documents:
        stored_path = Path(str(doc["stored_path"]))
        if not stored_path.exists():
            fallback_path = gateway_path_fallback(stored_path.name)
            if fallback_path:
                stored_path = fallback_path

        if not stored_path.exists():
            failed += 1
            print(f"[missing] {doc['filename']}")
            continue

        result = gateway.index_document(
            filename=str(doc["filename"]),
            stored_path=stored_path,
            extracted_text=str(doc["extracted_text"]),
        )
        if result.mode == "ragflow-http":
            db.update_document_ragflow_binding(
                int(doc["id"]),
                ragflow_document_id=result.primary_document_id,
                ragflow_dataset_id=result.dataset_id,
            )
            db.update_document_indexed_with(
                int(doc["id"]),
                "ragflow-http + synced",
            )
            synced += 1
            print(f"[synced] {doc['filename']}")
        else:
            failed += 1
            print(f"[failed] {doc['filename']}")

    print(f"同步完成: success={synced}, failed={failed}")


def gateway_path_fallback(filename: str) -> Path | None:
    normalized_name = filename.replace("\\", "/").split("/")[-1]
    candidate = Path("/app/data/uploads") / normalized_name
    if candidate.exists():
        return candidate
    local_candidate = Path("data/uploads") / normalized_name
    if local_candidate.exists():
        return local_candidate
    return None


if __name__ == "__main__":
    main()
