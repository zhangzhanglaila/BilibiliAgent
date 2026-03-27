"""项目默认配置。"""
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


@dataclass
class AppConfig:
    request_interval: float = float(os.getenv("REQUEST_INTERVAL", "1.2"))
    db_path: str = os.getenv("DB_PATH", "bilibili_agents.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    bili_sessdata: str = os.getenv("BILI_SESSDATA", "")
    bili_csrf: str = os.getenv("BILI_BILI_JCT", "")
    default_partition: str = os.getenv("DEFAULT_PARTITION", "knowledge")
    default_peer_ups: List[int] = field(
        default_factory=lambda: [
            int(x) for x in os.getenv("DEFAULT_PEER_UPS", "546195,15263701,777536").split(",") if x.strip()
        ]
    )

    def partition_tid(self, partition_name: str | None = None) -> int:
        name = (partition_name or self.default_partition).lower()
        return PARTITION_TIDS.get(name, PARTITION_TIDS["knowledge"])


CONFIG = AppConfig()
