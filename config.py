"""项目配置模块，定义全局参数、环境变量解析和应用配置类。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


# B站分区名称到对应 tid 的映射表，用于将分区名转换为B站接口所需的分区ID。
PARTITION_TIDS = {
    "knowledge": 36,
    "tech": 124,
    "life": 160,
    "game": 4,
    "ent": 5,
}

# 分区别名映射，将用户可能使用的多种分区名统一归类到主分区。
# 例如 "business"、"career"、"study" 都映射到 "knowledge"（知识分区）。
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


# 从环境变量读取布尔值，支持 "1"/"true"/"yes"/"on" 等多种写法。
def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 从环境变量读取整数值，解析失败时返回默认值。
def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default


# 应用配置数据类，集中管理所有从环境变量读取的配置项。
@dataclass
class AppConfig:
    request_interval: float = float(os.getenv("REQUEST_INTERVAL", "1.2"))
    topic_cache_ttl_seconds: int = int(os.getenv("TOPIC_CACHE_TTL_SECONDS", "180"))
    db_path: str = os.getenv("DB_PATH", "bilibili_agents.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://zapi.aicc0.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-5.4")
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
    llm_disable_response_storage: bool = env_bool("LLM_DISABLE_RESPONSE_STORAGE", False)
    langsmith_tracing: bool = env_bool("LANGSMITH_TRACING", env_bool("LANGCHAIN_TRACING_V2", False))
    langsmith_api_key: str = os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))
    langsmith_project: str = os.getenv("LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "bilibili-hot-rag"))
    langsmith_endpoint: str = os.getenv("LANGSMITH_ENDPOINT", os.getenv("LANGCHAIN_ENDPOINT", "")).strip()
    langchain_callbacks_background: bool = env_bool("LANGCHAIN_CALLBACKS_BACKGROUND", True)
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "75"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_retry_backoff_seconds: float = float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "1.6"))
    serpapi_api_key: str = os.getenv("SERPAPI_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    vector_db_path: str = os.getenv("VECTOR_DB_PATH", "./vector_db")
    embedding_model_name: str = os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "")
    embedding_cache_dir: str = os.getenv("EMBEDDING_CACHE_DIR", "./model_cache")
    llm_agent_default_max_steps: int = env_int("LLM_AGENT_DEFAULT_MAX_STEPS", 5)
    llm_agent_default_max_tool_calls: int = env_int("LLM_AGENT_DEFAULT_MAX_TOOL_CALLS", 4)
    llm_agent_default_repeat_action_limit: int = env_int("LLM_AGENT_DEFAULT_REPEAT_ACTION_LIMIT", 2)
    llm_agent_create_max_steps: int = env_int("LLM_AGENT_CREATE_MAX_STEPS", 4)
    llm_agent_create_max_tool_calls: int = env_int("LLM_AGENT_CREATE_MAX_TOOL_CALLS", 3)
    llm_agent_create_repeat_action_limit: int = env_int("LLM_AGENT_CREATE_REPEAT_ACTION_LIMIT", 2)
    llm_agent_analyze_max_steps: int = env_int("LLM_AGENT_ANALYZE_MAX_STEPS", 5)
    llm_agent_analyze_max_tool_calls: int = env_int("LLM_AGENT_ANALYZE_MAX_TOOL_CALLS", 5)
    llm_agent_analyze_repeat_action_limit: int = env_int("LLM_AGENT_ANALYZE_REPEAT_ACTION_LIMIT", 2)
    llm_agent_chat_max_steps: int = env_int("LLM_AGENT_CHAT_MAX_STEPS", 4)
    llm_agent_chat_max_tool_calls: int = env_int("LLM_AGENT_CHAT_MAX_TOOL_CALLS", 4)
    llm_agent_chat_repeat_action_limit: int = env_int("LLM_AGENT_CHAT_REPEAT_ACTION_LIMIT", 2)
    redis_url: str = os.getenv("REDIS_URL", "").strip()
    chat_session_ttl_seconds: int = env_int("CHAT_SESSION_TTL_SECONDS", 1800)
    chat_session_history_limit: int = env_int("CHAT_SESSION_HISTORY_LIMIT", 10)
    chat_session_memory_max_entries: int = env_int("CHAT_SESSION_MEMORY_MAX_ENTRIES", 512)
    retrieval_tool_timeout_seconds: int = env_int("RETRIEVAL_TOOL_TIMEOUT_SECONDS", 5)
    web_search_tool_timeout_seconds: int = env_int("WEB_SEARCH_TOOL_TIMEOUT_SECONDS", 15)
    video_briefing_tool_timeout_seconds: int = env_int("VIDEO_BRIEFING_TOOL_TIMEOUT_SECONDS", 10)
    hot_board_snapshot_tool_timeout_seconds: int = env_int("HOT_BOARD_SNAPSHOT_TOOL_TIMEOUT_SECONDS", 12)
    creator_briefing_cache_ttl_seconds: int = env_int("CREATOR_BRIEFING_CACHE_TTL_SECONDS", 900)
    hot_board_snapshot_cache_ttl_seconds: int = env_int("HOT_BOARD_SNAPSHOT_CACHE_TTL_SECONDS", 600)
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

    # 把外部输入的分区名或别名归一化成项目内部使用的主分区标识。
    def normalize_partition(self, partition_name: str | None = None) -> str:
        name = (partition_name or self.default_partition).strip().lower()
        name = PARTITION_ALIASES.get(name, name)
        return name if name in PARTITION_TIDS else "knowledge"

    # 判断当前是否已经配置 LLM Key，从而决定能否启用 LLM 能力。
    def llm_enabled(self) -> bool:
        return bool((self.llm_api_key or "").strip())

    # 根据是否启用 LLM 返回当前运行模式标识。
    def runtime_mode(self) -> str:
        return "llm_agent" if self.llm_enabled() else "rules"

    # 根据归一化后的分区名取出对应的 B 站 tid。
    def partition_tid(self, partition_name: str | None = None) -> int:
        return PARTITION_TIDS[self.normalize_partition(partition_name)]

    # 根据任务名称返回对应的 LLM Agent 预算配置（最大步数、工具调用次数、重复限制等）。
    def llm_agent_budget(self, task_name: str) -> dict:
        default_budget = {
            "max_steps": self.llm_agent_default_max_steps,
            "max_tool_calls": self.llm_agent_default_max_tool_calls,
            "repeat_action_limit": self.llm_agent_default_repeat_action_limit,
            "tool_limits": {
                "retrieval": 2,
                "web_search": 2,
            },
        }
        budgets = {
            "module_create": {
                "max_steps": self.llm_agent_create_max_steps,
                "max_tool_calls": self.llm_agent_create_max_tool_calls,
                "repeat_action_limit": self.llm_agent_create_repeat_action_limit,
                "tool_limits": {
                    "retrieval": 2,
                    "web_search": 2,
                },
            },
            "module_analyze": {
                "max_steps": self.llm_agent_analyze_max_steps,
                "max_tool_calls": self.llm_agent_analyze_max_tool_calls,
                "repeat_action_limit": self.llm_agent_analyze_repeat_action_limit,
                "tool_limits": {
                    "retrieval": 2,
                    "web_search": 2,
                    "video_briefing": 1,
                    "hot_board_snapshot": 1,
                },
            },
            "workspace_chat": {
                "max_steps": self.llm_agent_chat_max_steps,
                "max_tool_calls": self.llm_agent_chat_max_tool_calls,
                "repeat_action_limit": self.llm_agent_chat_repeat_action_limit,
                "tool_limits": {
                    "retrieval": 2,
                    "web_search": 2,
                    "video_briefing": 1,
                    "hot_board_snapshot": 1,
                },
            },
        }
        selected = budgets.get(task_name, default_budget)
        return {
            "max_steps": max(1, int(selected.get("max_steps", default_budget["max_steps"]))),
            "max_tool_calls": max(1, int(selected.get("max_tool_calls", default_budget["max_tool_calls"]))),
            "repeat_action_limit": max(
                1,
                int(selected.get("repeat_action_limit", default_budget["repeat_action_limit"])),
            ),
            "tool_limits": {
                str(name): max(1, int(limit))
                for name, limit in dict(selected.get("tool_limits") or default_budget["tool_limits"]).items()
            },
        }


# 全局配置单例，整个项目通过导入此对象获取配置。
CONFIG = AppConfig()
