"""数据模型定义。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List


# 视频指标数据类，存储单个视频的各类统计数据和评分。
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

    # 把视频指标 dataclass 展开成普通字典，便于序列化和持久化。
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# 选题方向数据类，存储选题主题、推荐理由和视频类型。
@dataclass
class TopicIdea:
    topic: str
    reason: str
    video_type: str
    keywords: List[str] = field(default_factory=list)
    score: float = 0.0


# 文案结果数据类，存储生成的标题、脚本、描述、标签等完整文案内容。
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


# 互动动作数据类，描述对视频的单个操作（如评论、删除、点赞、关注）。
@dataclass
class InteractionAction:
    action: str
    target: str
    message: str
    dry_run: bool = True


# 操作结果数据类，汇总对视频的多种互动操作结果。
@dataclass
class OperationResult:
    bv_id: str
    replies: List[InteractionAction]
    deletions: List[InteractionAction]
    likes: List[InteractionAction]
    follows: List[InteractionAction]
    summary: str


# 优化建议数据类，存储视频标题、封面、内容等方面的优化建议和对标视频摘要。
@dataclass
class OptimizationSuggestion:
    bv_id: str
    diagnosis: str
    optimized_titles: List[str]
    cover_suggestion: str
    content_suggestions: List[str]
    benchmark_summary: str
    raw_text: str = ""


# 递归把 dataclass、字典和列表里的对象都转换成可直接返回 JSON 的普通结构。
def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_plain_data(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain_data(val) for key, val in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value
