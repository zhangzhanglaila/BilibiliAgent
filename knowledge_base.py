"""RAG knowledge base backed exclusively by Chroma."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from config import CONFIG

try:
    import tiktoken
except Exception:  # pragma: no cover
    tiktoken = None

try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None

try:
    from langchain_core.embeddings import Embeddings
except Exception:  # pragma: no cover
    class Embeddings:  # type: ignore[no-redef]
        pass


@dataclass
class Document:
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class DeterministicEmbeddings(Embeddings):
    """Offline-safe embeddings so the vector layer can work without external APIs."""

    def __init__(self, dimension: int = 192) -> None:
        self.dimension = dimension

    def _tokenize(self, text: str) -> List[str]:
        return keyword_tokens(text)

    def _embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimension
        for token in self._tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for index in range(0, min(len(digest), self.dimension // 8)):
                slot = (digest[index] + index * 17) % self.dimension
                vector[slot] += ((digest[index] % 13) + 1) / 13.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


class KnowledgeBase:
    def __init__(self, persist_directory: str | None = None, collection_name: str = "bilibili_knowledge") -> None:
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

    def _split_text(self, text: str, chunk_size: int = 320, overlap: int = 60) -> List[str]:
        clean = (text or "").strip()
        if not clean:
            return []
        if tiktoken is not None:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                tokens = encoding.encode(clean)
                chunks: List[str] = []
                start = 0
                while start < len(tokens):
                    end = min(len(tokens), start + chunk_size)
                    chunks.append(encoding.decode(tokens[start:end]).strip())
                    if end >= len(tokens):
                        break
                    start = max(end - overlap, start + 1)
                return [chunk for chunk in chunks if chunk]
            except Exception:
                pass

        words = clean.split()
        if len(words) <= chunk_size:
            return [clean]
        chunks = []
        start = 0
        while start < len(words):
            end = min(len(words), start + chunk_size)
            chunks.append(" ".join(words[start:end]).strip())
            if end >= len(words):
                break
            start = max(end - overlap, start + 1)
        return [chunk for chunk in chunks if chunk]

    def _require_vector_backend(self) -> None:
        if self.vectorstore is not None or self.collection is not None:
            return
        detail = self.init_error or "Chroma backend not initialized"
        raise RuntimeError(f"知识库当前不可用：未检测到可用的 Chroma 向量库。{detail}")

    def available(self) -> bool:
        return self.vectorstore is not None or self.collection is not None

    def count(self) -> int:
        self._require_vector_backend()
        if self.vectorstore is not None:
            collection = getattr(self.vectorstore, "_collection", None)
            if collection is None:
                client = getattr(self.vectorstore, "_client", None)
                if client is not None:
                    try:
                        collection = client.get_collection(name=self.collection_name)
                    except Exception:
                        collection = None
            if collection is None:
                return 0
            return int(collection.count())
        if self.collection is not None:
            return int(self.collection.count())
        self._require_vector_backend()
        return 0

    def backend_status(self) -> Dict[str, Any]:
        return {
            "available": self.available(),
            "backend": self.backend,
            "persist_directory": str(self.persist_directory),
            "collection_name": self.collection_name,
            "document_count": self.count() if self.available() else 0,
            "init_error": self.init_error,
        }

    def _active_collection(self):
        self._require_vector_backend()
        if self.vectorstore is not None:
            collection = getattr(self.vectorstore, "_collection", None)
            if collection is None:
                client = getattr(self.vectorstore, "_client", None)
                if client is not None:
                    collection = client.get_collection(name=self.collection_name)
            return collection
        if self.collection is not None:
            return self.collection
        self._require_vector_backend()
        return None

    def _where_clause(self, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        filters = dict(metadata_filter or {})
        if not filters:
            return None
        if len(filters) == 1:
            key, value = next(iter(filters.items()))
            return {key: {"$eq": value}}
        return {"$and": [{key: {"$eq": value}} for key, value in filters.items()]}

    def sample(self, limit: int = 10, offset: int = 0, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
        collection = self._active_collection()
        payload = collection.get(
            limit=max(1, min(int(limit or 10), 50)),
            offset=max(0, int(offset or 0)),
            where=self._where_clause(metadata_filter),
            include=["documents", "metadatas"],
        )
        ids = payload.get("ids") or []
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        items: List[Dict[str, Any]] = []
        for item_id, text, metadata in zip(ids, documents, metadatas):
            items.append(
                {
                    "id": str((metadata or {}).get("document_id") or item_id or ""),
                    "text": str(text or ""),
                    "metadata": dict(metadata or {}),
                }
            )
        return {"items": items, "limit": limit, "offset": offset}

    def exists(self, document_id: str | None = None, metadata_filter: Dict[str, Any] | None = None) -> bool:
        collection = self._active_collection()
        where = dict(metadata_filter or {})
        if document_id:
            where["document_id"] = document_id
        payload = collection.get(limit=1, where=self._where_clause(where), include=["metadatas"])
        return bool(payload.get("ids"))

    def delete(self, document_id: str | None = None, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
        collection = self._active_collection()
        where = dict(metadata_filter or {})
        if document_id:
            where["document_id"] = document_id
        if not where:
            raise ValueError("删除知识库文档时必须提供 document_id 或 metadata_filter。")
        clause = self._where_clause(where)
        payload = collection.get(where=clause, include=["metadatas"])
        ids = payload.get("ids") or []
        if ids:
            collection.delete(where=clause)
        return {"deleted_count": len(ids), "where": where}

    def add_document(self, document: Document) -> Dict[str, Any]:
        self._require_vector_backend()
        chunks = self._split_text(document.text)
        if not chunks:
            return {"status": "skipped", "document_id": document.id, "chunk_count": 0}
        existed = self.exists(document_id=document.id)
        self.delete(document_id=document.id)

        metadatas = []
        ids = []
        for index, chunk in enumerate(chunks):
            metadata = dict(document.metadata)
            metadata["document_id"] = document.id
            metadata["chunk_index"] = index
            metadata["source"] = metadata.get("source", "knowledge_base")
            ids.append(f"{document.id}:{index}")
            metadatas.append(metadata)

        collection = self._active_collection()
        collection.upsert(
            ids=ids,
            documents=chunks,
            metadatas=metadatas,
            embeddings=self.embeddings.embed_documents(chunks),
        )

        return {"status": "updated" if existed else "ok", "document_id": document.id, "chunk_count": len(chunks)}

    def _fallback_score(self, query: str, text: str) -> float:
        query_tokens = set(keyword_tokens(query))
        text_tokens = set(keyword_tokens(text))
        if not query_tokens or not text_tokens:
            return 0.0
        overlap = len(query_tokens & text_tokens)
        return overlap / max(len(query_tokens), 1)

    def _vector_matches_from_langchain(
        self,
        query: str,
        limit: int,
        metadata_filter: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        docs = self.vectorstore.similarity_search_with_score(query, k=limit, filter=metadata_filter)
        for doc, score in docs:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            results.append(
                {
                    "id": metadata.get("document_id") or metadata.get("id") or "",
                    "text": getattr(doc, "page_content", ""),
                    "metadata": metadata,
                    "score": float(score),
                }
            )
        return results

    def _vector_matches_from_chromadb(
        self,
        query: str,
        limit: int,
        metadata_filter: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        if self.collection is None:
            return []
        payload = self.collection.query(
            query_embeddings=[self.embeddings.embed_query(query)],
            n_results=limit,
            where=self._where_clause(metadata_filter),
            include=["documents", "metadatas", "distances"],
        )
        ids = (payload.get("ids") or [[]])[0]
        documents = (payload.get("documents") or [[]])[0]
        metadatas = (payload.get("metadatas") or [[]])[0]
        distances = (payload.get("distances") or [[]])[0]
        results: List[Dict[str, Any]] = []
        for item_id, text, metadata, score in zip(ids, documents, metadatas, distances):
            results.append(
                {
                    "id": str((metadata or {}).get("document_id") or item_id or ""),
                    "text": str(text or ""),
                    "metadata": dict(metadata or {}),
                    "score": float(score or 0.0),
                }
            )
        return results

    def retrieve(self, query: str, limit: int = 4, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"query": clean_query, "matches": []}
        self._require_vector_backend()

        if self.vectorstore is not None:
            try:
                return {"query": clean_query, "matches": self._vector_matches_from_langchain(clean_query, limit, metadata_filter)}
            except Exception as exc:
                raise RuntimeError(f"Chroma 向量检索失败（{self.backend}）：{exc}") from exc

        if self.collection is not None:
            try:
                return {"query": clean_query, "matches": self._vector_matches_from_chromadb(clean_query, limit, metadata_filter)}
            except Exception as exc:
                raise RuntimeError(f"Chroma 向量检索失败（{self.backend}）：{exc}") from exc

        self._require_vector_backend()
        return {"query": clean_query, "matches": []}


DEFAULT_KNOWLEDGE_BASE = KnowledgeBase()


def keyword_tokens(text: str) -> List[str]:
    clean = str(text or "").lower()
    tokens = re.findall(r"[\u4e00-\u9fff]{1,6}|[a-z0-9]{2,24}", clean)
    return [token for token in tokens if token.strip()]


def add_document(document: Document) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.add_document(document)


def retrieve(query: str, limit: int = 4, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.retrieve(query, limit=limit, metadata_filter=metadata_filter)


def sample(limit: int = 10, offset: int = 0, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.sample(limit=limit, offset=offset, metadata_filter=metadata_filter)


def document_exists(document_id: str | None = None, metadata_filter: Dict[str, Any] | None = None) -> bool:
    return DEFAULT_KNOWLEDGE_BASE.exists(document_id=document_id, metadata_filter=metadata_filter)


def delete_documents(document_id: str | None = None, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.delete(document_id=document_id, metadata_filter=metadata_filter)
