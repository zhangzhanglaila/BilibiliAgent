from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from config import CONFIG

try:
    import redis
except Exception:  # pragma: no cover
    redis = None


LOGGER = logging.getLogger(__name__)
SESSION_KEY_PREFIX = "session:"
CACHE_KEY_PREFIX = "cache:"
VALID_CHAT_ROLES = {"user", "assistant", "system"}
SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:-]{0,127}$")


# 校验或生成会话 ID，确保格式合法，无效时自动生成新的 UUID。
def ensure_session_id(value: object) -> tuple[str, bool]:
    raw = str(value or "").strip()
    if raw and SESSION_ID_PATTERN.match(raw):
        return raw, False
    session_id = uuid4().hex
    LOGGER.info("session_id.generated session_id=%s reason=%s", session_id, "missing" if not raw else "invalid")
    return session_id, True


# 规范化聊天历史记录，过滤无效角色和空内容，保留 actions 和 references 字段。
def normalize_chat_history(value: object, limit: int | None = None) -> list[dict[str, Any]]:
    max_items = max(1, int(limit or CONFIG.chat_session_history_limit))
    history: list[dict[str, Any]] = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in VALID_CHAT_ROLES or not content:
            continue
        normalized: dict[str, Any] = {"role": role, "content": content}
        # 保留 actions 和 references（用于智能会话的视频卡片和推荐操作）
        if isinstance(item.get("actions"), list):
            normalized["actions"] = item["actions"]
        if isinstance(item.get("references"), list):
            normalized["references"] = item["references"]
        history.append(normalized)
    return history[-max_items:]


# 根据命名空间和负载内容生成缓存唯一标识（SHA1 哈希）。
def build_cache_identity(namespace: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


@dataclass
class SessionMemoryMetrics:
    redis_hits: int = 0
    redis_misses: int = 0
    redis_write_failures: int = 0
    context_loads: int = 0
    context_load_total_ms: float = 0.0
    degraded_loads: int = 0
    memory_evictions: int = 0


# 基于内存和 Redis 的会话存储管理器，支持读写分离和 LRU 淘汰。
class ChatSessionMemoryStore:
    def __init__(self, memory_cache_max_entries: int | None = None) -> None:
        self._lock = threading.Lock()
        configured_max_entries = memory_cache_max_entries
        if configured_max_entries is None:
            configured_max_entries = CONFIG.chat_session_memory_max_entries
        self._memory_cache_max_entries = max(1, int(configured_max_entries))
        self._memory_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._redis_client = None
        self._redis_initialized = False
        self._metrics = SessionMemoryMetrics()

    # 返回当前时间戳（秒）。
    def _now(self) -> float:
        return time.time()

    # 获取或初始化 Redis 客户端，超时控制避免阻塞。
    def _get_redis_client(self):
        if self._redis_initialized:
            return self._redis_client
        self._redis_initialized = True
        redis_url = str(CONFIG.redis_url or "").strip()
        if not redis_url or redis is None:
            if redis_url and redis is None:
                LOGGER.warning("session_memory.redis_unavailable reason=missing_dependency")
            return None
        try:
            client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            client.ping()
            self._redis_client = client
            LOGGER.info("session_memory.redis_ready")
        except Exception as exc:
            LOGGER.warning("session_memory.redis_init_failed error=%s", exc)
            self._redis_client = None
        return self._redis_client

    # 从内存缓存读取数据，自动清理过期条目。
    def _memory_get(self, key: str) -> Any | None:
        with self._lock:
            self._prune_memory_locked(now=self._now())
            item = self._memory_cache.get(key)
            if not item:
                return None
            if float(item.get("expires_at") or 0.0) <= self._now():
                self._memory_cache.pop(key, None)
                return None
            self._memory_cache.move_to_end(key)
            return item.get("value")

    # 向内存缓存写入数据，自动淘汰超出容量限制的旧条目。
    def _memory_set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            now = self._now()
            self._memory_cache[key] = {
                "value": value,
                "expires_at": now + max(1, int(ttl_seconds)),
            }
            self._memory_cache.move_to_end(key)
            self._prune_memory_locked(now=now)

    # 清理内存缓存中的过期条目和超出容量限制的旧数据。
    def _prune_memory_locked(self, now: float | None = None) -> None:
        current_time = float(now if now is not None else self._now())
        expired_keys = [
            key
            for key, item in self._memory_cache.items()
            if float((item or {}).get("expires_at") or 0.0) <= current_time
        ]
        for key in expired_keys:
            self._memory_cache.pop(key, None)

        while len(self._memory_cache) > self._memory_cache_max_entries:
            self._memory_cache.popitem(last=False)
            self._metrics.memory_evictions += 1

    # 从 Redis 或内存缓存读取 JSON 数据，优先 Redis。
    def _read_json(self, key: str, *, track_redis_miss: bool = False) -> tuple[Any | None, str]:
        client = self._get_redis_client()
        if client is not None:
            try:
                raw = client.get(key)
            except Exception as exc:
                LOGGER.warning("session_memory.redis_read_failed key=%s error=%s", key, exc)
            else:
                if raw:
                    try:
                        return json.loads(raw), "redis"
                    except Exception:
                        LOGGER.warning("session_memory.redis_read_invalid_json key=%s", key)
                if track_redis_miss:
                    with self._lock:
                        self._metrics.redis_misses += 1
        payload = self._memory_get(key)
        return payload, "memory" if payload is not None else ""

    # 向 Redis 和内存缓存双写 JSON 数据，Redis 失败时只写内存。
    def _write_json(self, key: str, value: Any, ttl_seconds: int) -> str:
        payload_text = json.dumps(value, ensure_ascii=False)
        client = self._get_redis_client()
        source = ""
        if client is not None:
            try:
                client.setex(key, max(1, int(ttl_seconds)), payload_text)
                source = "redis"
            except Exception as exc:
                with self._lock:
                    self._metrics.redis_write_failures += 1
                LOGGER.warning("session_memory.redis_write_failed key=%s error=%s", key, exc)
        self._memory_set(key, value, ttl_seconds)
        return source or "memory"

    # 加载指定会话的历史记录，优先从缓存读取，缓存不存在时使用前端传来的历史。
    def load_session_history(self, session_id: str, frontend_history: object = None) -> dict[str, Any]:
        started_at = time.perf_counter()
        key = f"{SESSION_KEY_PREFIX}{session_id}"
        cached_payload, source = self._read_json(key, track_redis_miss=True)
        history = []
        if isinstance(cached_payload, dict):
            history = normalize_chat_history(cached_payload.get("history"), limit=CONFIG.chat_session_history_limit)
        elif isinstance(cached_payload, list):
            history = normalize_chat_history(cached_payload, limit=CONFIG.chat_session_history_limit)

        if history:
            with self._lock:
                if source == "redis":
                    self._metrics.redis_hits += 1
            selected_source = source or "memory"
        else:
            history = normalize_chat_history(frontend_history, limit=CONFIG.chat_session_history_limit)
            selected_source = "frontend_session" if history else "empty"
            if selected_source == "empty":
                with self._lock:
                    self._metrics.degraded_loads += 1

        load_ms = round((time.perf_counter() - started_at) * 1000, 3)
        with self._lock:
            self._metrics.context_loads += 1
            self._metrics.context_load_total_ms += load_ms
        LOGGER.info(
            "session_memory.load session_id=%s source=%s history_count=%s load_ms=%s",
            session_id,
            selected_source,
            len(history),
            load_ms,
        )
        return {
            "history": history,
            "source": selected_source,
            "load_ms": load_ms,
        }

    # 保存会话历史到 Redis 和内存缓存。
    def save_session_history(self, session_id: str, history: object) -> None:
        key = f"{SESSION_KEY_PREFIX}{session_id}"
        normalized_history = normalize_chat_history(history, limit=CONFIG.chat_session_history_limit)
        payload = {
            "session_id": session_id,
            "history": normalized_history,
            "updated_at": int(self._now()),
        }
        source = self._write_json(key, payload, CONFIG.chat_session_ttl_seconds)
        LOGGER.info(
            "session_memory.save session_id=%s source=%s history_count=%s",
            session_id,
            source,
            len(normalized_history),
        )

    # 异步保存会话历史，避免阻塞主响应。
    def save_session_history_async(self, session_id: str, history: object) -> None:
        def worker() -> None:
            try:
                self.save_session_history(session_id, history)
            except Exception as exc:
                LOGGER.warning("session_memory.async_save_failed session_id=%s error=%s", session_id, exc)

        threading.Thread(
            target=worker,
            daemon=True,
            name=f"chat-session-save-{session_id[:8]}",
        ).start()

    # 获取缓存的有效载荷（用于 LLM 调用结果缓存）。
    def get_cached_payload(self, cache_identity: str) -> Any | None:
        payload, _ = self._read_json(f"{CACHE_KEY_PREFIX}{cache_identity}")
        return payload

    # 设置缓存的有效载荷。
    def set_cached_payload(self, cache_identity: str, payload: Any, ttl_seconds: int) -> None:
        self._write_json(f"{CACHE_KEY_PREFIX}{cache_identity}", payload, ttl_seconds)

    # 返回当前缓存和会话存储的统计指标快照。
    def metrics_snapshot(self) -> dict[str, Any]:
        with self._lock:
            redis_reads = self._metrics.redis_hits + self._metrics.redis_misses
            hit_rate = (self._metrics.redis_hits / redis_reads) if redis_reads else 0.0
            avg_context_load_ms = (
                self._metrics.context_load_total_ms / self._metrics.context_loads
                if self._metrics.context_loads
                else 0.0
            )
            return {
                "redis_hits": self._metrics.redis_hits,
                "redis_misses": self._metrics.redis_misses,
                "redis_hit_rate": round(hit_rate, 4),
                "redis_write_failures": self._metrics.redis_write_failures,
                "avg_context_load_ms": round(avg_context_load_ms, 3),
                "degraded_loads": self._metrics.degraded_loads,
                "memory_evictions": self._metrics.memory_evictions,
                "memory_cache_entries": len(self._memory_cache),
                "memory_cache_max_entries": self._memory_cache_max_entries,
                "ttl_seconds": int(CONFIG.chat_session_ttl_seconds),
                "history_limit": int(CONFIG.chat_session_history_limit),
            }


_CHAT_SESSION_MEMORY_STORE: ChatSessionMemoryStore | None = None


# 获取全局会话存储单例。
def get_chat_session_memory_store() -> ChatSessionMemoryStore:
    global _CHAT_SESSION_MEMORY_STORE
    if _CHAT_SESSION_MEMORY_STORE is None:
        _CHAT_SESSION_MEMORY_STORE = ChatSessionMemoryStore()
    return _CHAT_SESSION_MEMORY_STORE


# ---------------------------------------------------------------------------
# 历史会话持久化（基于本地文件，刷新页面后不丢失）
# ---------------------------------------------------------------------------

SESSIONS_DIR_NAME = "chat_sessions"
SESSIONS_INDEX_FILE = "sessions_index.json"
SESSION_META_SUFFIX = "_meta.json"
SESSION_HISTORY_SUFFIX = "_history.json"


# 基于本地 JSON 文件的历史会话管理器，支持跨刷新持久化。
class ChatSessionMetadataStore:
    """基于本地 JSON 文件的历史会话管理器，刷新页面后不丢失。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions_dir: Path | None = None

    @property
    def sessions_dir(self) -> Path:
        if self._sessions_dir is None:
            self._sessions_dir = Path(CONFIG.vector_db_path).resolve() / SESSIONS_DIR_NAME
        return self._sessions_dir

    # 确保会话存储目录已创建。
    def _ensure_dir(self) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # 返回当前时间戳（秒）。
    def _now_ts(self) -> int:
        return int(time.time())

    # 获取会话元数据文件路径。
    def _meta_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}{SESSION_META_SUFFIX}"

    # 获取会话历史文件路径。
    def _history_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}{SESSION_HISTORY_SUFFIX}"

    # 获取会话索引文件路径。
    def _index_path(self) -> Path:
        return self.sessions_dir / SESSIONS_INDEX_FILE

    # 从文件加载会话索引列表。
    def _load_index(self) -> list[dict[str, Any]]:
        index_file = self._index_path()
        if not index_file.exists():
            return []
        try:
            with open(index_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    # 将会话索引列表保存到文件。
    def _save_index(self, index: list[dict[str, Any]]) -> None:
        self._ensure_dir()
        index_file = self._index_path()
        try:
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)
        except Exception as exc:
            LOGGER.warning("chat_sessions.index_save_failed error=%s", exc)

    # 更新索引中指定会话的元数据，将新数据插入最前。
    def _upsert_index(self, session_id: str, meta: dict[str, Any]) -> None:
        """将 session 更新到 index 列表（按 updated_at 倒序）。"""
        with self._lock:
            index = self._load_index()
            index = [item for item in index if item.get("session_id") != session_id]
            index.insert(0, meta)  # 最新更新的放最前
            self._save_index(index)

    # 持久化保存会话元数据和历史记录到本地文件。
    def save_session(
        self,
        session_id: str,
        first_question: str,
        history: list[dict[str, str]],
        created_at: int | None = None,
    ) -> None:
        """持久化保存会话元数据+历史。"""
        now = self._now_ts()
        if created_at is None:
            created_at = now

        # 取历史第一条 user 消息作为 first_question（如果没传）
        if not first_question:
            for item in history:
                if item.get("role") == "user" and item.get("content"):
                    first_question = item["content"]
                    break

        meta: dict[str, Any] = {
            "session_id": session_id,
            "created_at": created_at,
            "created_at_display": time.strftime("%Y/%m/%d %H:%M", time.localtime(created_at)),
            "first_question": (first_question or "")[:200],
            "updated_at": now,
            "updated_at_display": time.strftime("%Y/%m/%d %H:%M", time.localtime(now)),
            "message_count": len(history),
        }

        history_data = {
            "session_id": session_id,
            "created_at": created_at,
            "history": history,
        }

        self._ensure_dir()

        # 写元数据和历史文件
        try:
            with open(self._meta_path(session_id), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
        except Exception as exc:
            LOGGER.warning("chat_sessions.meta_save_failed session_id=%s error=%s", session_id, exc)
            return

        try:
            with open(self._history_path(session_id), "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False)
        except Exception as exc:
            LOGGER.warning("chat_sessions.history_save_failed session_id=%s error=%s", session_id, exc)

        # 更新 index
        self._upsert_index(session_id, meta)

    # 异步保存会话数据，不阻塞主线程。
    def save_session_async(
        self,
        session_id: str,
        first_question: str,
        history: list[dict[str, str]],
        created_at: int | None = None,
    ) -> None:
        def worker() -> None:
            try:
                self.save_session(session_id, first_question, history, created_at)
            except Exception as exc:
                LOGGER.warning("chat_sessions.async_save_failed session_id=%s error=%s", session_id, exc)

        threading.Thread(target=worker, daemon=True, name=f"chat-session-meta-{session_id[:8]}").start()

    # 返回所有会话列表，按更新时间倒序。
    def list_sessions(self) -> list[dict[str, Any]]:
        """返回所有会话列表（按 updated_at 倒序）。"""
        with self._lock:
            index = self._load_index()
        # 返回完整 meta 信息（不含 history）
        result = []
        for item in index:
            result.append({
                "session_id": item.get("session_id", ""),
                "created_at": item.get("created_at", 0),
                "created_at_display": item.get("created_at_display", ""),
                "first_question": item.get("first_question", ""),
                "updated_at": item.get("updated_at", 0),
                "updated_at_display": item.get("updated_at_display", ""),
                "message_count": item.get("message_count", 0),
            })
        return result

    # 读取指定会话的完整元数据和历史记录。
    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """读取指定会话的元数据+历史。"""
        meta_file = self._meta_path(session_id)
        history_file = self._history_path(session_id)

        if not meta_file.exists():
            return None

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return None

        history: list[dict[str, Any]] = []
        if history_file.exists():
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    hist_data = json.load(f)
                    history = hist_data.get("history", []) if isinstance(hist_data, dict) else []
            except Exception:
                history = []

        return {
            "session_id": meta.get("session_id", session_id),
            "created_at": meta.get("created_at", 0),
            "created_at_display": meta.get("created_at_display", ""),
            "first_question": meta.get("first_question", ""),
            "updated_at": meta.get("updated_at", 0),
            "updated_at_display": meta.get("updated_at_display", ""),
            "history": history,
        }

    # 删除指定会话的元数据、历史文件和索引记录。
    def delete_session(self, session_id: str) -> bool:
        """删除指定会话的元数据和历史文件，并从索引中移除。"""
        meta_file = self._meta_path(session_id)
        history_file = self._history_path(session_id)
        deleted = False
        try:
            if meta_file.exists():
                meta_file.unlink()
                deleted = True
        except Exception:
            pass
        try:
            if history_file.exists():
                history_file.unlink()
        except Exception:
            pass
        # 从索引中移除
        with self._lock:
            index = self._load_index()
            index = [item for item in index if item.get("session_id") != session_id]
            self._save_index(index)
        return deleted


_CHAT_SESSION_METADATA_STORE: ChatSessionMetadataStore | None = None


# 获取全局会话元数据存储单例。
def get_chat_session_metadata_store() -> ChatSessionMetadataStore:
    global _CHAT_SESSION_METADATA_STORE
    if _CHAT_SESSION_METADATA_STORE is None:
        _CHAT_SESSION_METADATA_STORE = ChatSessionMetadataStore()
    return _CHAT_SESSION_METADATA_STORE
