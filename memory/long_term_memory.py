"""Long-term memory store backed exclusively by Chroma."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from config import CONFIG
from knowledge_base import DeterministicEmbeddings

try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None


class LongTermMemory:
    def __init__(self, persist_directory: str | None = None, collection_name: str = "user_long_term_memory") -> None:
        self.persist_directory = Path(persist_directory or CONFIG.vector_db_path)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.embeddings = DeterministicEmbeddings()
        self.vectorstore = None
        self.collection = None
        self.backend = "disabled"
        self.init_error = ""
        if Chroma is not None:
            try:
                self.vectorstore = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=str(self.persist_directory),
                    embedding_function=self.embeddings,
                )
                self.backend = "langchain_chroma"
            except Exception as exc:
                self.vectorstore = None
                self.init_error = str(exc)
        if self.vectorstore is None and chromadb is not None:
            try:
                client = chromadb.PersistentClient(path=str(self.persist_directory))
                self.collection = client.get_or_create_collection(name=self.collection_name)
                self.backend = "chromadb"
                self.init_error = ""
            except Exception as exc:
                self.collection = None
                self.init_error = str(exc)

    def _require_vector_backend(self) -> None:
        if self.vectorstore is not None or self.collection is not None:
            return
        detail = self.init_error or "Chroma backend not initialized"
        raise RuntimeError(f"长期记忆当前不可用：未检测到可用的 Chroma 向量库。{detail}")

    def save_user_data(self, user_id: str, data: Dict[str, Any], memory_type: str = "workspace_record") -> Dict[str, Any]:
        self._require_vector_backend()
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
        elif self.collection is not None:
            self.collection.upsert(
                ids=[record_id],
                documents=[text],
                metadatas=[metadata],
                embeddings=self.embeddings.embed_documents([text]),
            )
        else:  # pragma: no cover
            self._require_vector_backend()
        return {"status": "ok", "user_id": clean_user_id, "record_id": record_id}

    def retrieve_user_history(self, user_id: str, query: str, limit: int = 4) -> Dict[str, Any]:
        self._require_vector_backend()
        clean_user_id = (user_id or "").strip() or "anonymous"
        clean_query = (query or "").strip()
        if not clean_query:
            return {"user_id": clean_user_id, "history": []}

        history: List[Dict[str, Any]] = []
        if self.vectorstore is not None:
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

        if self.collection is not None:
            payload = self.collection.query(
                query_embeddings=[self.embeddings.embed_query(clean_query)],
                n_results=limit,
                where={"user_id": clean_user_id},
                include=["documents", "metadatas", "distances"],
            )
            documents = (payload.get("documents") or [[]])[0]
            metadatas = (payload.get("metadatas") or [[]])[0]
            distances = (payload.get("distances") or [[]])[0]
            for text, metadata, score in zip(documents, metadatas, distances):
                history.append({"text": str(text or ""), "metadata": dict(metadata or {}), "score": float(score or 0.0)})
            return {"user_id": clean_user_id, "history": history}

        self._require_vector_backend()
        return {"user_id": clean_user_id, "history": []}
