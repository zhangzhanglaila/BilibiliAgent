"""Three-layer Router for LLM requests.

Layer 1 — Direct LLM : simple chat, no tools needed           (target: 2~4s)
Layer 2 — Fast Path  : structured task with preloaded context  (target: 5~10s)
Layer 3 — Agent      : complex task needing ReAct + tool calls (target: 15~30s)

The router classifies incoming requests and routes them to the appropriate layer,
recording the chosen path for metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from metrics import record, TimedBlock

# Keywords that indicate the request needs tool calling (Agent)
AGENT_TRIGGER_KEYWORDS = (
    "分析视频", "http", "bilibili.com", "BV", "av",
    "热点", "趋势", "排行榜", "对标", "选题", "文案",
    "搜索", "检索", "知识库", "帮我查", "帮我找",
    "历史对话", "回顾", "复盘", "之前聊",
)

# Keywords that indicate a structured analysis task (Fast Path)
STRUCTURED_TASK_KEYWORDS = (
    "分析视频", "BV", "选题", "文案", "创作", "对标",
    "优化", "标题", "封面", "脚本", "标签",
)


@dataclass
class RouteDecision:
    """Result of routing a request."""
    module: str       # "chat" | "create" | "analyze"
    path: str         # "direct" | "fast" | "agent"
    reason: str       # human-readable reason for this routing


def classify_chat(message: str, video_url: str = "") -> RouteDecision:
    """Classify a chat message into direct / fast / agent."""
    if not message.strip():
        return RouteDecision("chat", "direct", "empty message")

    has_video = bool(video_url.strip())
    needs_agent = any(kw in message for kw in AGENT_TRIGGER_KEYWORDS)

    if not needs_agent and not has_video:
        return RouteDecision("chat", "direct", "simple chat, no tools needed")

    if has_video and any(kw in message for kw in STRUCTURED_TASK_KEYWORDS):
        return RouteDecision("chat", "fast", "video analysis requested")

    return RouteDecision("chat", "agent", "tools required")


def classify_create(data: dict) -> RouteDecision:
    """Content creation always tries fast path first, falls back to agent."""
    idea = (data.get("idea") or "").strip()
    direction = (data.get("direction") or "").strip()
    field = (data.get("field") or "").strip()

    # Simple cases go direct
    if not idea and not direction and not field:
        return RouteDecision("create", "direct", "no creative input, use fallback")

    return RouteDecision("create", "fast", "structured creative task, fast path")


def classify_analyze(data: dict) -> RouteDecision:
    """Video analysis always uses fast path first."""
    return RouteDecision("analyze", "fast", "video analysis with preloaded context")


def route_request(
    module: str,
    data: dict,
    *,
    direct_fn: Optional[Callable] = None,
    fast_fn: Optional[Callable] = None,
    agent_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Unified routing: classify → execute → record metrics.

    Args:
        module: "chat" | "create" | "analyze"
        data: request payload
        direct_fn: handler for direct LLM path
        fast_fn: handler for fast path
        agent_fn: handler for agent path

    Returns the result dict from whichever path succeeded.
    """
    # Classify
    if module == "chat":
        decision = classify_chat(
            (data.get("message") or "").strip(),
            (data.get("context") or {}).get("videoLink", ""),
        )
    elif module == "create":
        decision = classify_create(data)
    elif module == "analyze":
        decision = classify_analyze(data)
    else:
        decision = RouteDecision(module, "direct", "unknown module, default direct")

    # Try paths in order: direct → fast → agent (cascading fallback)
    # The path in the decision is the PREFERRED path, but if it fails,
    # we cascade to the next heavier path.

    path_order = _cascade_paths(decision.path)
    last_error: Optional[Exception] = None

    for path in path_order:
        handler = {"direct": direct_fn, "fast": fast_fn, "agent": agent_fn}.get(path)
        if handler is None:
            continue

        with TimedBlock(module, path) as block:
            try:
                result = handler(data)
                if isinstance(result, dict):
                    result.setdefault("_route_path", path)
                return result
            except Exception as exc:
                last_error = exc
                block.set_error(type(exc).__name__)
                # Cascade to next path
                continue

    # All paths failed
    raise RuntimeError(
        f"All paths failed for {module} (tried: {', '.join(path_order)}). "
        f"Last error: {last_error}"
    )


def _cascade_paths(preferred: str) -> list[str]:
    """Return ordered list of paths to try, from lightest to heaviest."""
    order = ["direct", "fast", "agent"]
    if preferred not in order:
        return [preferred]
    idx = order.index(preferred)
    return order[idx:]  # Try preferred, then fall back to heavier paths
