"""Knowledge base ingestion and synchronization helpers."""
from __future__ import annotations

import io
import json
import re
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from bilibili_api import comment, hot, sync, video, video_zone

from config import CONFIG
from knowledge_base import Document, add_document

try:
    from docx import Document as DocxDocument
except Exception:  # pragma: no cover
    DocxDocument = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None


COMMENT_STOPWORDS = {
    "这个",
    "真的",
    "就是",
    "感觉",
    "视频",
    "评论",
    "他们",
    "你们",
    "我们",
    "一个",
    "不是",
    "没有",
    "还是",
    "因为",
    "看到",
    "什么",
    "怎么",
    "真的好",
    "哈哈",
    "哈哈哈",
    "可以",
    "一下",
}
PRIMARY_PARTITIONS = ("knowledge", "tech", "life", "game", "ent")


def normalize_kb_text(text: str) -> str:
    clean = str(text or "").replace("\r", "\n")
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip()


def keyword_tokens(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z0-9]{2,20}", str(text or "").lower())


def detect_file_suffix(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def read_uploaded_file_content(filename: str, raw_bytes: bytes) -> str:
    suffix = detect_file_suffix(filename)
    if suffix in {".txt", ".md"}:
        return normalize_kb_text(raw_bytes.decode("utf-8", errors="ignore"))
    if suffix == ".docx":
        if DocxDocument is None:
            raise RuntimeError("未安装 python-docx，无法读取 docx 文件。")
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as temp_file:
            temp_file.write(raw_bytes)
            temp_path = Path(temp_file.name)
        try:
            document = DocxDocument(str(temp_path))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
            return normalize_kb_text(text)
        finally:
            temp_path.unlink(missing_ok=True)
    if suffix == ".pdf":
        if PdfReader is None:
            raise RuntimeError("未安装 pypdf，无法读取 pdf 文件。")
        reader = PdfReader(io.BytesIO(raw_bytes))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return normalize_kb_text(text)
    raise ValueError("暂不支持该文件格式，仅支持 txt / md / docx / pdf。")


def ingest_uploaded_file(filename: str, raw_bytes: bytes, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    text = read_uploaded_file_content(filename, raw_bytes)
    if not text:
        raise ValueError("文件内容为空，无法写入知识库。")
    document_id = f"file:{Path(filename).stem}:{int(time.time())}"
    result = add_document(
        Document(
            id=document_id,
            text=text,
            metadata={
                "source": "uploaded_file",
                "filename": Path(filename).name,
                **(metadata or {}),
            },
        )
    )
    result["filename"] = Path(filename).name
    return result


def _safe_sync(coro, default):
    try:
        return sync(coro)
    except Exception:
        return default


def _comment_hotwords(aid: int, limit: int = 8) -> List[str]:
    payload = _safe_sync(
        comment.get_comments(aid, comment.CommentResourceType.VIDEO, page_index=1, order=comment.OrderType.LIKE),
        {},
    )
    replies = payload.get("replies") or []
    words: Counter[str] = Counter()
    for item in replies[:15]:
        message = ((item.get("content") or {}).get("message") or "").strip()
        for token in keyword_tokens(message):
            if token in COMMENT_STOPWORDS or len(token) < 2:
                continue
            words[token] += 1
    return [word for word, _ in words.most_common(limit)]


def _format_pub_slot(pub_ts: int) -> str:
    if pub_ts <= 0:
        return "未知"
    dt = datetime.fromtimestamp(pub_ts)
    if 6 <= dt.hour < 10:
        period = "早间"
    elif 10 <= dt.hour < 14:
        period = "午间"
    elif 14 <= dt.hour < 18:
        period = "下午"
    elif 18 <= dt.hour < 23:
        period = "晚高峰"
    else:
        period = "夜间"
    return f"{dt.strftime('%Y-%m-%d %H:%M')}（{period}）"


def _title_advantage(title: str) -> str:
    text = title or ""
    traits = []
    if any(token in text for token in ["？", "?", "为什么", "怎么", "有何", "到底"]):
        traits.append("标题自带提问或冲突，容易激发点击欲")
    if re.search(r"\d+", text):
        traits.append("标题里有数字或期数，记忆点明确")
    if any(token in text for token in ["反差", "坦白局", "实测", "对比", "第一次"]):
        traits.append("标题带有强反差或真实体验信号，进入动机更强")
    if not traits:
        traits.append("标题信息密度高，能快速说明题材和看点")
    return "；".join(traits[:2])


def _script_advantage(duration: int, desc: str) -> str:
    if duration and duration <= 90:
        return "内容长度偏短，通常适合快速抛钩子后直接进入核心信息。"
    if duration and duration <= 240:
        return "中短视频时长，适合按开场钩子 + 两段核心内容 + 互动结尾组织脚本。"
    if desc:
        return "简介里有明确主题延展空间，适合拆成多层信息递进来讲。"
    return "视频适合先立主题，再用中段补充案例或体验，最后用互动问题收束。"


def _rhythm_advantage(duration: int, view: int, like: int) -> str:
    if duration and duration < 60:
        return "整体节奏偏快，更适合连续高信息密度输出。"
    if like and view and like / max(view, 1) > 0.05:
        return "点赞率较高，说明情绪点或节奏点命中较准。"
    return "节奏大概率靠主题推进和镜头切换维持，不依赖单一爆点。"


def _interaction_advantage(title: str, reply: int, hotwords: List[str]) -> str:
    if reply > 500:
        return "评论量较高，说明话题天然适合引发站队、补充经历或观点讨论。"
    if hotwords:
        return f"评论热词集中在「{'、'.join(hotwords[:4])}」，说明互动点已经比较明确。"
    if any(token in title for token in ["你会", "你们", "有没有", "会不会"]):
        return "标题本身自带代入式提问，评论区更容易被调动起来。"
    return "题材具备讨论空间，适合在结尾主动抛出选择题或经历型提问。"


def _tag_advantage(tags: Iterable[str], partition_label: str) -> str:
    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    if clean_tags:
        return f"标签围绕「{'、'.join(clean_tags[:5])}」展开，利于平台理解内容语义和同类召回。"
    return f"分区语义集中在「{partition_label}」，建议继续围绕同赛道关键词做标签补强。"


def _structured_video_text(board_type: str, item: dict, detail: dict, tags: List[str], hotwords: List[str]) -> str:
    stat = detail.get("stat") or item.get("stat") or {}
    title = str(detail.get("title") or item.get("title") or "").strip()
    owner = (detail.get("owner") or item.get("owner") or {})
    partition = str(detail.get("tname") or item.get("tname") or "").strip()
    view = int(stat.get("view") or item.get("view") or 0)
    like = int(stat.get("like") or item.get("like") or 0)
    reply = int(stat.get("reply") or item.get("reply") or 0)
    pub_ts = int(detail.get("pubdate") or item.get("pubdate") or 0)
    advantages = {
        "标题亮点": _title_advantage(title),
        "脚本结构": _script_advantage(int(detail.get("duration") or item.get("duration") or 0), str(detail.get("desc") or "")),
        "节奏把控": _rhythm_advantage(int(detail.get("duration") or item.get("duration") or 0), view, like),
        "互动设计": _interaction_advantage(title, reply, hotwords),
        "标签策略": _tag_advantage(tags, partition or "综合"),
    }
    payload = {
        "榜单来源": board_type,
        "视频标题": title,
        "UP主": str(owner.get("name") or item.get("author") or "").strip(),
        "分区": partition,
        "播放量": view,
        "点赞量": like,
        "评论热词": hotwords,
        "核心优点": advantages,
        "发布时间点": _format_pub_slot(pub_ts),
        "BVID": str(detail.get("bvid") or item.get("bvid") or "").strip(),
        "链接": f"https://www.bilibili.com/video/{detail.get('bvid') or item.get('bvid')}",
    }
    return normalize_kb_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _video_detail(bvid: str) -> dict:
    if not bvid:
        return {}
    return _safe_sync(video.Video(bvid=bvid).get_info(), {})


def _video_tags(video_obj: dict) -> List[str]:
    bvid = str(video_obj.get("bvid") or "").strip()
    if not bvid:
        return []
    data = _safe_sync(video.Video(bvid=bvid).get_tags(), [])
    if not isinstance(data, list):
        return []
    return [str(item.get("tag_name") or "").strip() for item in data if str(item.get("tag_name") or "").strip()]


def _ingest_hot_items(board_type: str, items: Iterable[dict], limit: int = 10) -> Dict[str, Any]:
    saved = 0
    failed: List[str] = []
    for index, item in enumerate(list(items)[:limit], start=1):
        bvid = str(item.get("bvid") or "").strip()
        if not bvid:
            continue
        detail = _video_detail(bvid) or dict(item)
        aid = int(detail.get("aid") or item.get("aid") or 0)
        tags = _video_tags(detail or item)
        hotwords = _comment_hotwords(aid) if aid > 0 else []
        text = _structured_video_text(board_type, item, detail, tags, hotwords)
        try:
            add_document(
                Document(
                    id=f"{board_type}:{bvid}:{int(time.time())}:{index}",
                    text=text,
                    metadata={
                        "source": "bilibili_hot_sync",
                        "board_type": board_type,
                        "bvid": bvid,
                        "partition": str(detail.get("tname") or item.get("tname") or "").strip(),
                    },
                )
            )
            saved += 1
        except Exception as exc:
            failed.append(f"{board_type}:{bvid}:{exc}")
    return {"board_type": board_type, "saved_count": saved, "failed": failed}


def crawl_and_store_bilibili_hot_videos(per_board_limit: int = 10) -> Dict[str, Any]:
    summary: List[Dict[str, Any]] = []

    hot_payload = _safe_sync(hot.get_hot_videos(ps=per_board_limit, pn=1), {})
    hot_items = hot_payload if isinstance(hot_payload, list) else hot_payload.get("list", []) or []
    summary.append(_ingest_hot_items("全站热门榜", hot_items, limit=per_board_limit))

    weekly_series = _safe_sync(hot.get_weekly_hot_videos_list(), {})
    weekly_list = (weekly_series.get("list") or weekly_series.get("data") or []) if isinstance(weekly_series, dict) else []
    latest_week = int((weekly_list[0] or {}).get("number") or 1) if weekly_list else 1
    weekly_payload = _safe_sync(hot.get_weekly_hot_videos(latest_week), {})
    weekly_items = weekly_payload.get("list", []) if isinstance(weekly_payload, dict) else []
    summary.append(_ingest_hot_items("每周必看", weekly_items, limit=per_board_limit))

    history_payload = _safe_sync(hot.get_history_popular_videos(), {})
    history_items = history_payload.get("list", []) if isinstance(history_payload, dict) else []
    summary.append(_ingest_hot_items("入站必刷", history_items, limit=per_board_limit))

    for partition_name in PRIMARY_PARTITIONS:
        tid = CONFIG.partition_tid(partition_name)
        top_payload = _safe_sync(video_zone.get_zone_top10(tid), {})
        top_items = top_payload if isinstance(top_payload, list) else top_payload.get("list", []) or top_payload.get("top", []) or []
        summary.append(_ingest_hot_items(f"分区热门榜:{partition_name}", top_items, limit=min(per_board_limit, 10)))

    return {
        "status": "ok",
        "boards": summary,
        "total_saved": sum(item.get("saved_count", 0) for item in summary),
        "total_failed": sum(len(item.get("failed", [])) for item in summary),
    }


def update_chroma_knowledge_base(per_board_limit: int = 10) -> Dict[str, Any]:
    return crawl_and_store_bilibili_hot_videos(per_board_limit=per_board_limit)
