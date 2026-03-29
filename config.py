"""Project configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


PARTITION_TIDS = {
    "knowledge": 36,
    "tech": 124,
    "life": 160,
    "game": 4,
    "ent": 5,
}

PARTITION_ALIASES = {
    "business": "knowledge",
    "career": "knowledge",
    "study": "knowledge",
    "ai": "tech",
    "digital": "tech",
    "auto": "tech",
    "food": "life",
    "vlog": "life",
    "emotion": "life",
    "fashion": "life",
    "pet": "life",
    "sports": "life",
    "beauty": "ent",
    "dance": "ent",
    "music": "ent",
    "film": "ent",
    "anime": "ent",
}


@dataclass
class AppConfig:
    request_interval: float = float(os.getenv("REQUEST_INTERVAL", "1.2"))
    db_path: str = os.getenv("DB_PATH", "bilibili_agents.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://zapi.aicc0.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-5.4")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "75"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_retry_backoff_seconds: float = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "1.6"))
    bili_sessdata: str = os.getenv("BILI_SESSDATA", "")
    bili_csrf: str = os.getenv("BILI_BILI_JCT", "")
    default_partition: str = os.getenv("DEFAULT_PARTITION", "knowledge")
    default_peer_ups: List[int] = field(
        default_factory=lambda: [
            int(item)
            for item in os.getenv("DEFAULT_PEER_UPS", "546195,15263701,777536").split(",")
            if item.strip()
        ]
    )

    def normalize_partition(self, partition_name: str | None = None) -> str:
        name = (partition_name or self.default_partition).strip().lower()
        name = PARTITION_ALIASES.get(name, name)
        return name if name in PARTITION_TIDS else "knowledge"

    def llm_enabled(self) -> bool:
        return bool((self.llm_api_key or "").strip())

    def runtime_mode(self) -> str:
        return "llm_agent" if self.llm_enabled() else "rules"

    def partition_tid(self, partition_name: str | None = None) -> int:
        return PARTITION_TIDS[self.normalize_partition(partition_name)]


CONFIG = AppConfig()
