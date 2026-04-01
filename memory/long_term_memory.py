"""Long-term memory store backed by Chroma when available."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from config import CONFIG
from knowledge_base import DeterministicEmbeddings, keyword_tokens

try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None


class LongTermMemory:
    def __init__(self, persist_directory: str | None = None, collection_name: str = "user_long_term_memory") -> None:
        self.persist_directory = Path(persist_directory or CONFIG.vector_db_path)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.embeddings = DeterministicEmbeddings()
        self._fallback_path = self.persist_directory / f"{collection_name}.json"
        self._fallback_records = self._load_records()
        self.vectorstore = None
        if Chroma is not None:
            try:
                self.vectorstore = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=str(self.persist_directory),
                    embedding_function=self.embeddings,
                )
            except Exception:
                self.vectorstore = None

    def _load_records(self) -> List[Dict[str, Any]]:
        if not self._fallback_path.exists():
            return []
        try:
            payload = json.loads(self._fallback_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except Exception:
            return []

    def _flush_records(self) -> None:
        self._fallback_path.write_text(
            json.dumps(self._fallback_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def save_user_data(self, user_id: str, data: Dict[str, Any], memory_type: str = "workspace_record") -> Dict[str, Any]:
        clean_user_id = (user_id or "").strip() or "anonymous"
        text = json.dumps(data, ensure_ascii=False)
        record_id = hashlib.sha1(f"{clean_user_id}:{memory_type}:{time.time()}".encode("utf-8")).hexdigest()[:16]
        metadata = {
            "user_id": clean_user_id,
            "memory_type": memory_type,
            "created_at": int(time.time()),
        }
        if self.vectorstore is not None:
            self.vectorstore.add_texts([text], metadatas=[metadata], ids=[record_id])
        else:
            self._fallback_records.append({"id": record_id, "text": text, "metadata": metadata})
            self._flush_records()
        return {"status": "ok", "user_id": clean_user_id, "record_id": record_id}

    def retrieve_user_history(self, user_id: str, query: str, limit: int = 4) -> Dict[str, Any]:
        clean_user_id = (user_id or "").strip() or "anonymous"
        clean_query = (query or "").strip()
        if not clean_query:
            return {"user_id": clean_user_id, "history": []}

        history: List[Dict[str, Any]] = []
        if self.vectorstore is not None:
            try:
                docs = self.vectorstore.similarity_search_with_score(clean_query, k=limit, filter={"user_id": clean_user_id})
                for doc, score in docs:
                    history.append(
                        {
                            "text": getattr(doc, "page_content", ""),
                            "metadata": dict(getattr(doc, "metadata", {}) or {}),
                            "score": float(score),
                        }
                    )
                return {"user_id": clean_user_id, "history": history}
            except Exception:
                pass

        query_tokens = set(keyword_tokens(clean_query))
        scored = []
        for item in self._fallback_records:
            metadata = dict(item.get("metadata") or {})
            if metadata.get("user_id") != clean_user_id:
                continue
            text = str(item.get("text") or "")
            text_tokens = set(keyword_tokens(text))
            overlap = len(query_tokens & text_tokens)
            score = overlap / max(len(query_tokens), 1)
            scored.append({"text": text, "metadata": metadata, "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return {"user_id": clean_user_id, "history": scored[:limit]}
