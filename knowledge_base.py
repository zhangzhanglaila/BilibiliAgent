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
        if Chroma is not None:
            try:
                self.vectorstore = Chroma(
                    collection_name=self.collection_name,
                    persist_directory=str(self.persist_directory),
                    embedding_function=self.embeddings,
                )
            except Exception:
                self.vectorstore = None

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

    def retrieve(self, query: str, limit: int = 4, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"query": clean_query, "matches": []}

        results: List[Dict[str, Any]] = []
        if self.vectorstore is not None:
            try:
                docs = self.vectorstore.similarity_search_with_score(clean_query, k=limit, filter=metadata_filter)
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
                return {"query": clean_query, "matches": results}
            except Exception:
                pass

        filtered = []
        for record in self._fallback_records:
            metadata = dict(record.get("metadata") or {})
            if metadata_filter and any(metadata.get(key) != value for key, value in metadata_filter.items()):
                continue
            filtered.append(
                {
                    "id": record.get("id", ""),
                    "text": record.get("text", ""),
                    "metadata": metadata,
                    "score": self._fallback_score(clean_query, record.get("text", "")),
                }
            )
        filtered.sort(key=lambda item: item["score"], reverse=True)
        return {"query": clean_query, "matches": filtered[:limit]}


DEFAULT_KNOWLEDGE_BASE = KnowledgeBase()


def keyword_tokens(text: str) -> List[str]:
    clean = str(text or "").lower()
    tokens = re.findall(r"[\u4e00-\u9fff]{1,6}|[a-z0-9]{2,24}", clean)
    return [token for token in tokens if token.strip()]


def add_document(document: Document) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.add_document(document)


def retrieve(query: str, limit: int = 4, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return DEFAULT_KNOWLEDGE_BASE.retrieve(query, limit=limit, metadata_filter=metadata_filter)
