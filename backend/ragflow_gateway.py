from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlencode

import requests

from backend import db
from backend.config import settings


@dataclass
class RetrievedChunk:
    source: str
    excerpt: str
    score: float


@dataclass
class RetrievalResponse:
    chunks: list[RetrievedChunk]
    mode: str
    engine: str
    elapsed_ms: float
    used_fallback: bool = False
    dataset_id: str = ""


@dataclass
class DocumentIndexResult:
    mode: str
    dataset_id: str = ""
    document_ids: list[str] | None = None

    @property
    def primary_document_id(self) -> str:
        if not self.document_ids:
            return ""
        return self.document_ids[0]


class RAGFlowGateway:
    def __init__(self) -> None:
        self.enabled = bool(
            settings.ragflow_enabled
            and settings.ragflow_base_url
            and settings.ragflow_api_key
        )
        self._dataset_id_cache = settings.ragflow_dataset_id or ""
        self.last_search_mode = "local-demo"

    @property
    def mode_label(self) -> str:
        return "ragflow-http" if self.enabled else "local-demo"

    def index_document(self, filename: str, stored_path: Path, extracted_text: str) -> DocumentIndexResult:
        if not self.enabled:
            return DocumentIndexResult(mode=self.mode_label)

        dataset_id = self._resolve_dataset_id()
        if not dataset_id:
            return DocumentIndexResult(mode="local-demo")

        ingest_url = f"{settings.ragflow_base_url}/api/v1/datasets/{dataset_id}/documents"
        headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}

        try:
            with stored_path.open("rb") as file_handle:
                response = requests.post(
                    ingest_url,
                    headers=headers,
                    files={"file": (filename, file_handle)},
                    timeout=30,
                )
            payload = self._parse_json_response(response)
            if not self._is_success(payload):
                return DocumentIndexResult(mode="local-demo")
            document_ids = self._extract_document_ids(payload)
            if document_ids:
                self._trigger_parse(dataset_id=dataset_id, document_ids=document_ids)
            return DocumentIndexResult(
                mode=self.mode_label,
                dataset_id=dataset_id,
                document_ids=document_ids,
            )
        except Exception:
            return DocumentIndexResult(mode="local-demo")

    def delete_document(
        self,
        *,
        remote_document_id: str = "",
        filename: str = "",
        dataset_id: str = "",
    ) -> bool:
        if not self.enabled:
            return False

        resolved_dataset_id = dataset_id or self._resolve_dataset_id()
        if not resolved_dataset_id:
            return False

        document_ids: list[str] = []
        if remote_document_id:
            document_ids = [remote_document_id]
        elif filename:
            matched = self._find_remote_documents_by_name(
                dataset_id=resolved_dataset_id,
                filename=filename,
            )
            if len(matched) != 1:
                return False
            document_ids = [str(matched[0].get("id", ""))]

        if not document_ids or not document_ids[0]:
            return False

        url = f"{settings.ragflow_base_url}/api/v1/datasets/{resolved_dataset_id}/documents"
        headers = {
            "Authorization": f"Bearer {settings.ragflow_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"ids": document_ids}

        try:
            response = requests.delete(url, headers=headers, json=payload, timeout=30)
            parsed = self._parse_json_response(response)
        except Exception:
            return False
        return self._is_success(parsed)

    def search(self, question: str, top_k: int = 3) -> list[RetrievedChunk]:
        response = self.search_with_meta(question=question, top_k=top_k, allow_local_fallback=True)
        return response.chunks

    def search_with_meta(
        self,
        *,
        question: str,
        top_k: int = 3,
        allow_local_fallback: bool = True,
    ) -> RetrievalResponse:
        started_at = perf_counter()
        dataset_id = self._resolve_dataset_id() if self.enabled else ""
        if self.enabled:
            remote_results = self._search_remote(
                question=question,
                top_k=top_k,
                dataset_id=dataset_id,
            )
            if remote_results:
                elapsed_ms = (perf_counter() - started_at) * 1000
                self.last_search_mode = "ragflow-http"
                return RetrievalResponse(
                    chunks=remote_results,
                    mode="ragflow-http",
                    engine="ragflow-doc-engine",
                    elapsed_ms=elapsed_ms,
                    used_fallback=False,
                    dataset_id=dataset_id,
                )

        if allow_local_fallback:
            local_results = self._search_local(question=question, top_k=top_k)
            elapsed_ms = (perf_counter() - started_at) * 1000
            self.last_search_mode = "local-demo"
            return RetrievalResponse(
                chunks=local_results,
                mode="local-demo",
                engine="sqlite-fallback",
                elapsed_ms=elapsed_ms,
                used_fallback=True,
                dataset_id=dataset_id,
            )

        elapsed_ms = (perf_counter() - started_at) * 1000
        self.last_search_mode = "ragflow-http-empty"
        return RetrievalResponse(
            chunks=[],
            mode="ragflow-http-empty",
            engine="ragflow-doc-engine",
            elapsed_ms=elapsed_ms,
            used_fallback=False,
            dataset_id=dataset_id,
        )

    def _search_remote(
        self,
        *,
        question: str,
        top_k: int,
        dataset_id: str | None = None,
    ) -> list[RetrievedChunk]:
        dataset_id = dataset_id or self._resolve_dataset_id()
        if not dataset_id:
            return []

        search_url = f"{settings.ragflow_base_url}{settings.ragflow_search_path}"
        headers = {
            "Authorization": f"Bearer {settings.ragflow_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "question": question,
            "dataset_ids": [dataset_id],
            "page": 1,
            "page_size": top_k,
            "similarity_threshold": 0.2,
            # This tells RAGFlow to use a hybrid-style remote retrieval path:
            # keyword/full-text matching is enabled, and vector similarity still
            # participates with a configurable weight.
            "vector_similarity_weight": 0.3,
            "top_k": max(32, top_k),
            "highlight": False,
            "keyword": True,
        }

        try:
            response = requests.post(search_url, headers=headers, json=payload, timeout=30)
            data = self._parse_json_response(response)
        except Exception:
            return []

        if not self._is_success(data):
            return []

        raw_items = self._extract_chunk_items(data)
        results: list[RetrievedChunk] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            source = str(
                item.get("document_keyword")
                or item.get("doc_name")
                or item.get("source")
                or item.get("document_name")
                or "RAGFlow"
            )
            excerpt = str(
                item.get("content")
                or item.get("highlight")
                or item.get("text")
                or item.get("chunk")
                or item.get("snippet")
                or ""
            ).strip()
            score = float(
                item.get("similarity")
                or item.get("score")
                or item.get("vector_similarity")
                or 0.0
            )
            if excerpt:
                results.append(RetrievedChunk(source=source, excerpt=excerpt, score=score))
        return results[:top_k]

    def _resolve_dataset_id(self) -> str:
        if self._dataset_id_cache:
            return self._dataset_id_cache

        if settings.ragflow_dataset_id:
            self._dataset_id_cache = settings.ragflow_dataset_id
            return self._dataset_id_cache

        dataset = self._find_dataset_by_name(settings.ragflow_dataset_name)
        if dataset:
            self._dataset_id_cache = str(dataset.get("id", ""))
            return self._dataset_id_cache

        if settings.ragflow_auto_create_dataset and settings.ragflow_dataset_name:
            dataset = self._create_dataset(settings.ragflow_dataset_name)
            if dataset:
                self._dataset_id_cache = str(dataset.get("id", ""))
                return self._dataset_id_cache

        datasets = self._list_datasets(page_size=1)
        if datasets:
            self._dataset_id_cache = str(datasets[0].get("id", ""))
        return self._dataset_id_cache

    def _find_dataset_by_name(self, name: str) -> dict[str, Any] | None:
        if not name:
            return None

        datasets = self._list_datasets(name=name, page_size=30)
        lowered = name.strip().lower()
        for dataset in datasets:
            dataset_name = str(dataset.get("name", "")).strip().lower()
            if dataset_name == lowered:
                return dataset
        return datasets[0] if len(datasets) == 1 else None

    def _list_datasets(self, *, name: str | None = None, page_size: int = 30) -> list[dict[str, Any]]:
        params = {
            "page": 1,
            "page_size": page_size,
            "orderby": "create_time",
            "desc": "true",
        }
        if name:
            params["name"] = name
        query = urlencode(params)
        url = f"{settings.ragflow_base_url}/api/v1/datasets?{query}"
        headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}

        try:
            response = requests.get(url, headers=headers, timeout=30)
            payload = self._parse_json_response(response)
        except Exception:
            return []

        if not self._is_success(payload):
            return []

        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    def _create_dataset(self, name: str) -> dict[str, Any] | None:
        url = f"{settings.ragflow_base_url}/api/v1/datasets"
        headers = {
            "Authorization": f"Bearer {settings.ragflow_api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_dataset_payload(name)

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            payload = self._parse_json_response(response)
        except Exception:
            return None

        if not self._is_success(payload):
            return None

        data = payload.get("data", {})
        return data if isinstance(data, dict) else None

    @staticmethod
    def _build_dataset_payload(name: str) -> dict[str, Any]:
        chunk_method = settings.ragflow_dataset_chunk_method or "naive"
        payload: dict[str, Any] = {
            "name": name,
            "description": settings.ragflow_dataset_description,
            "chunk_method": chunk_method,
            "permission": "me",
        }

        if chunk_method in {"laws", "book", "manual", "paper", "presentation", "qa"}:
            payload["parser_config"] = {
                "raptor": {"use_raptor": False},
            }
        elif chunk_method == "naive":
            payload["parser_config"] = {
                "chunk_token_num": 512,
                "delimiter": "\n",
                "raptor": {"use_raptor": False},
                "graphrag": {"use_graphrag": False},
            }

        return payload

    def _find_remote_documents_by_name(
        self,
        *,
        dataset_id: str,
        filename: str,
    ) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "page": 1,
                "page_size": 20,
                "orderby": "create_time",
                "desc": "true",
                "name": filename,
            }
        )
        url = f"{settings.ragflow_base_url}/api/v1/datasets/{dataset_id}/documents?{query}"
        headers = {"Authorization": f"Bearer {settings.ragflow_api_key}"}
        try:
            response = requests.get(url, headers=headers, timeout=30)
            payload = self._parse_json_response(response)
        except Exception:
            return []

        if not self._is_success(payload):
            return []

        data = payload.get("data", [])
        if not isinstance(data, list):
            return []
        exact_name = filename.strip().lower()
        return [
            item
            for item in data
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == exact_name
        ]

    def _trigger_parse(self, *, dataset_id: str, document_ids: list[str]) -> bool:
        url = f"{settings.ragflow_base_url}/api/v1/datasets/{dataset_id}/chunks"
        headers = {
            "Authorization": f"Bearer {settings.ragflow_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"document_ids": document_ids}
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            parsed = self._parse_json_response(response)
        except Exception:
            return False
        return self._is_success(parsed)

    @staticmethod
    def _parse_json_response(response: requests.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _is_success(payload: dict[str, Any]) -> bool:
        return payload.get("code", 0) == 0

    @staticmethod
    def _extract_chunk_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data", {})
        if isinstance(data, dict):
            chunks = data.get("chunks", [])
            if isinstance(chunks, list):
                return chunks
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        for key in ("chunks", "results", "items"):
            items = payload.get(key, [])
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_document_ids(payload: dict[str, Any]) -> list[str]:
        data = payload.get("data", [])
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        document_ids: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            document_id = item.get("id")
            if document_id:
                document_ids.append(str(document_id))
        return document_ids

    def _search_local(self, *, question: str, top_k: int) -> list[RetrievedChunk]:
        query_parts = self._tokenize_parts(question)
        if not query_parts["all"]:
            return []

        scored: list[RetrievedChunk] = []
        for doc in db.list_documents_with_text(limit=50):
            haystack = f"{doc['filename']} {doc['preview']} {doc['extracted_text']}"
            doc_parts = self._tokenize_parts(haystack)
            score = self._score_overlap(query_parts, doc_parts)
            if score <= 0:
                continue
            score = self._apply_document_type_bias(
                question=question,
                filename=str(doc["filename"]),
                score=score,
            )
            excerpt = doc["preview"] or doc["extracted_text"][:200]
            scored.append(
                RetrievedChunk(
                    source=str(doc["filename"]),
                    excerpt=excerpt,
                    score=round(score, 3),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _tokenize_parts(text: str) -> dict[str, set[str]]:
        normalized = text.lower()
        alpha_tokens = set(re.findall(r"[a-z0-9]+", normalized))

        chinese_sequences = re.findall(r"[\u4e00-\u9fff]+", normalized)
        chinese_bigrams: set[str] = set()
        chinese_chars: set[str] = set()
        for seq in chinese_sequences:
            chinese_chars.update(seq)
            if len(seq) == 1:
                chinese_bigrams.add(seq)
                continue
            chinese_bigrams.update(seq[i : i + 2] for i in range(len(seq) - 1))

        all_tokens = alpha_tokens | chinese_bigrams | chinese_chars
        return {
            "alpha": alpha_tokens,
            "bigrams": chinese_bigrams,
            "chars": chinese_chars,
            "all": all_tokens,
        }

    @staticmethod
    def _score_overlap(query_parts: dict[str, set[str]], doc_parts: dict[str, set[str]]) -> float:
        alpha_overlap = len(query_parts["alpha"] & doc_parts["alpha"])
        bigram_overlap = len(query_parts["bigrams"] & doc_parts["bigrams"])
        char_overlap = len(query_parts["chars"] & doc_parts["chars"])

        weighted_overlap = alpha_overlap * 10 + bigram_overlap * 3 + char_overlap
        weighted_query_total = (
            len(query_parts["alpha"]) * 10
            + len(query_parts["bigrams"]) * 3
            + len(query_parts["chars"])
        )
        if weighted_query_total == 0:
            return 0.0

        if query_parts["alpha"] and alpha_overlap == 0:
            return 0.0

        return weighted_overlap / weighted_query_total

    @staticmethod
    def _apply_document_type_bias(*, question: str, filename: str, score: float) -> float:
        question_lower = question.lower()
        filename_lower = filename.lower()

        asks_for_paper = any(keyword in question for keyword in ("论文", "paper", "文章"))
        asks_for_resume = any(keyword in question for keyword in ("简历", "resume", "cv"))

        if asks_for_paper:
            if ".pdf" in filename_lower:
                score *= 1.8
            elif ".docx" in filename_lower:
                score *= 0.65

        if asks_for_resume:
            if ".docx" in filename_lower or ".pdf" in filename_lower:
                score *= 1.4

        return score
