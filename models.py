"""数据模型定义。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List


@dataclass
class VideoMetrics:
    bvid: str
    title: str
    author: str = ""
    cover: str = ""
    mid: int = 0
    view: int = 0
    like: int = 0
    coin: int = 0
    favorite: int = 0
    reply: int = 0
    share: int = 0
    duration: int = 0
    avg_view_duration: float = 0.0
    like_rate: float = 0.0
    completion_rate: float = 0.0
    competition_score: float = 0.0
    source: str = ""
    pubdate: int = 0
    url: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopicIdea:
    topic: str
    reason: str
    video_type: str
    keywords: List[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class CopywritingResult:
    topic: str
    style: str
    titles: List[str]
    script: List[Dict[str, str]]
    description: str
    tags: List[str]
    pinned_comment: str
    raw_text: str = ""


@dataclass
class InteractionAction:
    action: str
    target: str
    message: str
    dry_run: bool = True


@dataclass
class OperationResult:
    bv_id: str
    replies: List[InteractionAction]
    deletions: List[InteractionAction]
    likes: List[InteractionAction]
    follows: List[InteractionAction]
    summary: str


@dataclass
class OptimizationSuggestion:
    bv_id: str
    diagnosis: str
    optimized_titles: List[str]
    cover_suggestion: str
    content_suggestions: List[str]
    benchmark_summary: str
    raw_text: str = ""


def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_plain_data(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain_data(val) for key, val in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value
