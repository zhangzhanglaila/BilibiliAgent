"""Simple TTL-based in-memory cache for LLM responses.

Cached keys:
  - video analyze : by BV ID (or URL hash)
  - creator brief  : by topic hash
  - chat frequent  : by message hash (short TTL)
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, max_size: int = 256, default_ttl: float = 300.0):
        self._store: Dict[str, Tuple[float, Any]] = {}  # key -> (expires_at, value)
        self._lock = threading.Lock()
        self._max_size = max_size
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        expires_at = time.time() + ttl
        with self._lock:
            # Evict oldest if at capacity
            if len(self._store) >= self._max_size:
                oldest_key = min(self._store.keys(), key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (expires_at, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            now = time.time()
            active = sum(1 for _, (exp, _) in self._store.items() if now <= exp)
            expired = len(self._store) - active
            return {
                "total_entries": len(self._store),
                "active": active,
                "expired": expired,
                "max_size": self._max_size,
            }


# Global cache instances with different TTLs
video_cache = TTLCache(max_size=128, default_ttl=600.0)  # 10 min for video analysis
creator_cache = TTLCache(max_size=128, default_ttl=300.0)  # 5 min for creative
chat_cache = TTLCache(max_size=256, default_ttl=60.0)   # 1 min for chat


def _hash(*parts: str) -> str:
    raw = "|".join(part.strip().lower() for part in parts if part.strip())
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def video_cache_key(url: str) -> str:
    # Extract BV ID or use URL hash
    import re
    bv_match = re.search(r"BV[a-zA-Z0-9]{10}", url)
    if bv_match:
        return f"video:{bv_match.group(0)}"
    return f"video:{_hash(url)}"


def creator_cache_key(field: str, direction: str, idea: str, partition: str) -> str:
    return f"creator:{_hash(field, direction, idea, partition)}"


def chat_cache_key(message: str, creator_context: dict) -> str:
    ctx_hash = _hash(
        creator_context.get("direction", ""),
        creator_context.get("partition", ""),
    )
    return f"chat:{_hash(message)}:{ctx_hash}"
