"""Lightweight RAG knowledge base backed by Chroma when available."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
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
        self._fallback_path = self.persist_directory / f"{collection_name}.json"
        self._fallback_records: List[Dict[str, Any]] = self._load_fallback_records()
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

    def _load_fallback_records(self) -> List[Dict[str, Any]]:
        if not self._fallback_path.exists():
            return []
        try:
            payload = json.loads(self._fallback_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, list) else []
        except Exception:
            return []

    def _flush_fallback_records(self) -> None:
        self._fallback_path.write_text(
            json.dumps(self._fallback_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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

    def add_document(self, document: Document) -> Dict[str, Any]:
        chunks = self._split_text(document.text)
        if not chunks:
            return {"status": "skipped", "document_id": document.id, "chunk_count": 0}

        metadatas = []
        ids = []
        for index, chunk in enumerate(chunks):
            metadata = dict(document.metadata)
            metadata["document_id"] = document.id
            metadata["chunk_index"] = index
            metadata["source"] = metadata.get("source", "knowledge_base")
            ids.append(f"{document.id}:{index}")
            metadatas.append(metadata)

        if self.vectorstore is not None:
            self.vectorstore.add_texts(chunks, metadatas=metadatas, ids=ids)
        elif self.collection is not None:
            self.collection.upsert(
                ids=ids,
                documents=chunks,
                metadatas=metadatas,
                embeddings=self.embeddings.embed_documents(chunks),
            )
        else:
            self._fallback_records = [
                record for record in self._fallback_records if record.get("metadata", {}).get("document_id") != document.id
            ]
            for item_id, chunk, metadata in zip(ids, chunks, metadatas):
                self._fallback_records.append({"id": item_id, "text": chunk, "metadata": metadata})
            self._flush_fallback_records()

        return {"status": "ok", "document_id": document.id, "chunk_count": len(chunks)}

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
            where=metadata_filter or None,
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

        if self.vectorstore is not None:
            try:
                return {"query": clean_query, "matches": self._vector_matches_from_langchain(clean_query, limit, metadata_filter)}
            except Exception:
                return {
                    "query": clean_query,
                    "matches": [],
                    "error": "chroma_vector_search_failed",
                    "backend": self.backend,
                }

        if self.collection is not None:
            try:
                return {"query": clean_query, "matches": self._vector_matches_from_chromadb(clean_query, limit, metadata_filter)}
            except Exception:
                return {
                    "query": clean_query,
                    "matches": [],
                    "error": "chroma_vector_search_failed",
                    "backend": self.backend,
                }

        return {
            "query": clean_query,
            "matches": [],
            "error": "chroma_backend_unavailable",
            "backend": self.backend,
            "detail": self.init_error or "Chroma backend not initialized",
        }


DEFAULT_KNOWLEDGE_BASE = KnowledgeBase()


def keyword_tokens(text: str) -> List[str]:
    clean = str(text or "").lower()
    tokens = re.findall(r"[\u4e00-\u9fff]{1,6}|[a-z0-9]{2,24}", clean)
    return [token for token in tokens if token.strip()]


def add_document(document: Document) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.add_document(document)


def retrieve(query: str, limit: int = 4, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.retrieve(query, limit=limit, metadata_filter=metadata_filter)
