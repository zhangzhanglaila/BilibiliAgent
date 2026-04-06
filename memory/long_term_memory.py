"""基于 Chroma 向量数据库的长期记忆存储模块

本模块提供用户长期记忆的持久化存储功能，支持：
    - 基于向量相似度的语义检索
    - 多后端支持（langchain_chroma、chromadb）
    - 自动维度检查和集合迁移
    - 用户级别隔离的历史记录查询

主要类:
    LongTermMemory: 核心记忆存储类，提供 save_user_data 和 retrieve_user_history 方法
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from config import CONFIG
from knowledge_base import build_default_embeddings

try:
    from langchain_chroma import Chroma
except Exception:  # pragma: no cover
    Chroma = None

try:
    import chromadb
except Exception:  # pragma: no cover
    chromadb = None


class LongTermMemory:
    """长期记忆存储类

    基于 Chroma 向量数据库实现用户记忆的持久化存储，支持语义相似度检索。
    自动检测并初始化可用的向量后端（优先 langchain_chroma，其次 chromadb）。
    """
    def __init__(self, persist_directory: str | None = None, collection_name: str = "user_long_term_memory") -> None:
        """初始化长期记忆存储

        自动检测并初始化可用的向量数据库后端。优先使用 langchain_chroma，
        若失败则尝试 chromadb，最后都不行则标记为 disabled 状态。

        Args:
            persist_directory: 持久化存储路径，默认为 CONFIG.vector_db_path
            collection_name: Chroma collection 名称，默认为 "user_long_term_memory"
        """
        self.persist_directory = Path(persist_directory or CONFIG.vector_db_path)
        self.persist_directory.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.embeddings = build_default_embeddings()
        self.vectorstore = None
        self.collection = None
        self.backend = "disabled"
        self.init_error = ""
        # 优先尝试 langchain_chroma 后端
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
        # 若 langchain_chroma 不可用，尝试原生 chromadb 后端
        if self.vectorstore is None and chromadb is not None:
            try:
                client = chromadb.PersistentClient(path=str(self.persist_directory))
                self.collection = client.get_or_create_collection(name=self.collection_name)
                self.backend = "chromadb"
                self.init_error = ""
            except Exception as exc:
                self.collection = None
                self.init_error = str(exc)
        # 初始化成功后，检查向量维度是否匹配
        if self.vectorstore is not None or self.collection is not None:
            try:
                self._ensure_embedding_dimension()
            except Exception as exc:
                self.init_error = str(exc)
                raise

    def _require_vector_backend(self) -> None:
        """确保向量后端可用

        检查是否至少有一个向量后端（vectorstore 或 collection）可用。
        若都不可用，抛出 RuntimeError 异常。

        Raises:
            RuntimeError: 当向量后端都未初始化时抛出
        """
        if self.vectorstore is not None or self.collection is not None:
            return
        detail = self.init_error or "Chroma backend not initialized"
        raise RuntimeError(f"长期记忆当前不可用：未检测到可用的 Chroma 向量库。{detail}")

    def _where_clause(self, metadata_filter: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        """构建 Chroma 查询的 where 子句

        将元数据过滤器字典转换为 Chroma 的 where 查询格式。

        Args:
            metadata_filter: 元数据过滤器，如 {"user_id": "xxx"}

        Returns:
            Chroma 格式的 where 子句字典，或 None（无过滤条件）
                  - 单个过滤条件: {key: {"$eq": value}}
                  - 多个过滤条件: {"$and": [{key: {"$eq": value}}, ...]}
        """
        filters = dict(metadata_filter or {})
        if not filters:
            return None
        if len(filters) == 1:
            key, value = next(iter(filters.items()))
            return {key: {"$eq": value}}
        return {"$and": [{key: {"$eq": value}} for key, value in filters.items()]}

    def _active_collection(self):
        """获取当前活跃的 Chroma collection 对象

        兼容 langchain_chroma 和原生 chromadb 两种后端，
        从 vectorstore 或 collection 属性中提取实际的 collection 对象。

        Returns:
            Chroma collection 对象，或 None
        """
        if self.vectorstore is not None:
            collection = getattr(self.vectorstore, "_collection", None)
            if collection is None:
                client = getattr(self.vectorstore, "_client", None)
                if client is not None:
                    collection = client.get_collection(name=self.collection_name)
            return collection
        return self.collection

    def _embedding_dimension(self) -> int:
        """获取当前嵌入模型的向量维度

        通过向嵌入模型发送一个测试查询来获取向量的实际维度。

        Returns:
            int: 嵌入向量的维度
        """
        return len(self.embeddings.embed_query("长期记忆向量维度检查"))

    def _collection_embedding_dimension(self) -> int:
        """获取 collection 中已有向量的维度

        从 collection 中取出一条记录，获取其嵌入向量的实际维度。

        Returns:
            int: collection 中向量的维度，0 表示无法获取或为空
        """
        collection = self._active_collection()
        payload = collection.get(limit=1, include=["embeddings"])
        embeddings = payload.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return 0
        first = embeddings[0]
        if first is None:
            return 0
        return len(first)

    def _ensure_embedding_dimension(self) -> None:
        """确保 collection 向量维度与嵌入模型匹配

        当 embedding 模型改变时（如维度变化），需要重建 collection 并重新嵌入所有文档。
        如果维度一致或 collection 为空，则不做任何操作。

        迁移过程：
            1. 获取当前 collection 中的所有数据（ids、documents、metadatas）
            2. 删除旧的 collection
            3. 用新的嵌入模型创建新的 collection
            4. 将旧数据重新嵌入并存储
        """
        collection = self._active_collection()
        if collection is None:
            return
        expected_dimension = self._embedding_dimension()
        existing_dimension = self._collection_embedding_dimension()
        # 维度匹配或 collection 为空，无需迁移
        if existing_dimension == 0 or existing_dimension == expected_dimension:
            return

        # 步骤1：导出所有数据
        payload = collection.get(include=["documents", "metadatas"])
        ids = [str(item) for item in payload.get("ids") or []]
        documents = [str(item or "") for item in payload.get("documents") or []]
        metadatas = [dict(item or {}) for item in payload.get("metadatas") or []]

        # 步骤2：获取 client 并删除旧 collection
        client = getattr(self.vectorstore, "_client", None) if self.vectorstore is not None else None
        if client is None and chromadb is not None:
            client = chromadb.PersistentClient(path=str(self.persist_directory))
        if client is None:
            raise RuntimeError("长期记忆 collection 迁移失败：未找到可用 client。")
        try:
            client.delete_collection(name=self.collection_name)
        except Exception:
            pass

        # 步骤3：用新嵌入模型重建 collection
        if Chroma is not None:
            self.vectorstore = Chroma(
                collection_name=self.collection_name,
                persist_directory=str(self.persist_directory),
                embedding_function=self.embeddings,
            )
            self.collection = None
            target = getattr(self.vectorstore, "_collection", None)
        else:
            self.vectorstore = None
            self.collection = client.get_or_create_collection(name=self.collection_name)
            target = self.collection
        if target is None:
            raise RuntimeError("长期记忆 collection 迁移失败：重建后 collection 为空。")

        # 步骤4：重新嵌入并存储旧数据
        if ids:
            target.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=self.embeddings.embed_documents(documents),
            )

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
                where=self._where_clause({"user_id": clean_user_id}),
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
