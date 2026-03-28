"""Flask web entry for the Bilibili content ideation and analysis workspace."""
from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bilibili_api import sync, video

from agents.llm_workspace_agent import AgentTool, LLMWorkspaceAgent
from agents.topic_agent import TopicAgent
from config import CONFIG
from llm_client import LLMClient
from main import run_copy, run_operate, run_optimize, run_pipeline, run_topic

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)

SHORT_LINK_HOSTS = ("b23.tv", "bili2233.cn")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
PARTITION_LABELS = {
    "knowledge": "知识",
    "tech": "科技",
    "life": "生活",
    "game": "游戏",
    "ent": "娱乐",
}
CREATOR_PARTITION_ANGLES = {
    "knowledge": ["问题拆解", "保姆级步骤", "避坑清单", "实测对比"],
    "tech": ["结果对比", "真实实测", "省钱替代", "新手避坑"],
    "life": ["低成本切口", "真实体验", "前后对比", "情绪共鸣"],
    "game": ["版本答案", "新手路线", "实战复盘", "高光片段"],
    "ent": ["3秒反差开场", "热门动作切口", "评论区互动点", "系列人设"],
}
CREATOR_STOPWORDS = {
    "视频",
    "内容",
    "教程",
    "方法",
    "什么",
    "怎么",
    "如何",
    "应该",
    "一个",
    "我们",
    "你们",
    "自己",
    "账号",
}
QUESTION_TOKENS = ("怎么", "如何", "为什么", "应该", "什么", "哪种", "哪类", "能不能")
RUNTIME_MODE_LABELS = {
    "rules": "无 Key 规则模式",
    "llm_agent": "LLM Agent 模式",
}

RAW_TOPIC_AGENT = TopicAgent()
LLM_WORKSPACE_AGENT: LLMWorkspaceAgent | None = None


def safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def fetch_text(url: str, timeout: int = 10) -> str:
    request_obj = Request(url, headers=DEFAULT_HEADERS)
    with urlopen(request_obj, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def fetch_json(url: str) -> dict:
    payload = json.loads(fetch_text(url))
    if not isinstance(payload, dict):
        raise ValueError("B站接口返回了无效数据")
    return payload


def resolve_short_link(url: str) -> str:
    if not url or not any(host in url for host in SHORT_LINK_HOSTS):
        return url

    request_obj = Request(url, headers=DEFAULT_HEADERS)
    try:
        with urlopen(request_obj, timeout=8) as response:
            return response.geturl()
    except Exception:
        return url


def extract_bvid(url: str) -> str:
    raw_url = (url or "").strip()
    candidate = resolve_short_link(raw_url)
    match = re.search(r"(BV[0-9A-Za-z]{10})", candidate, flags=re.IGNORECASE)
    if not match:
        if any(host in raw_url for host in SHORT_LINK_HOSTS):
            raise ValueError("短链接展开失败，请改用包含 BV 号的完整视频链接重试")
        raise ValueError("未识别到有效的 B 站视频 BV 号")
    value = match.group(1)
    return "BV" + value[2:]


def map_partition(tname: str, tid: int) -> str:
    text = (tname or "").lower()
    if any(keyword in text for keyword in ["知识", "科普", "学习", "校园", "职业"]):
        return "knowledge"
    if any(keyword in text for keyword in ["科技", "数码", "软件", "计算机", "程序"]):
        return "tech"
    if any(keyword in text for keyword in ["游戏", "电竞"]):
        return "game"
    if any(keyword in text for keyword in ["生活", "美食", "日常", "家居"]):
        return "life"
    if any(keyword in text for keyword in ["娱乐", "影视", "综艺", "明星", "音乐"]):
        return "ent"

    if tid in {36, 201, 208, 209, 229}:
        return "knowledge"
    if tid in {95, 122, 124}:
        return "tech"
    if tid in {4, 17, 65, 136, 172}:
        return "game"
    if tid in {21, 76, 138, 160}:
        return "life"
    if tid in {5, 71, 137, 181}:
        return "ent"
    return "knowledge"


def guess_style(title: str, partition: str, tname: str) -> str:
    text = f"{title} {tname}".lower()
    if any(keyword in text for keyword in ["教程", "教学", "保姆级", "入门", "攻略", "怎么", "如何"]):
        return "教学"
    if any(keyword in text for keyword in ["搞笑", "整活", "沙雕", "鬼畜", "吐槽", "抽象"]):
        return "搞笑"
    if any(keyword in text for keyword in ["混剪", "高燃", "踩点", "mad", "剪辑", "卡点"]):
        return "混剪"
    if partition == "game" and "攻略" in text:
        return "教学"
    return "干货"


def build_topic(title: str) -> str:
    cleaned = re.sub(r"[【\[].*?[】\]]", "", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    return (cleaned or title or "B站内容选题拆解").strip()


def build_seed_topic(field_name: str, direction: str, idea: str) -> str:
    field_name = normalize_creator_text(field_name)
    direction = normalize_creator_direction(direction, idea)
    idea = normalize_creator_text(idea)

    profile = refine_creator_profile(field_name, direction, idea)
    if not idea:
        return profile

    idea_tail = strip_leading_context(idea, [field_name, direction, profile])
    if not idea_tail:
        idea_tail = idea

    if any(token in idea_tail for token in QUESTION_TOKENS):
        if profile:
            account_profile = profile if profile.endswith("账号") else f"{profile}账号"
            return f"{account_profile}{idea_tail}"
        return idea_tail

    if profile and idea_tail and profile not in idea_tail:
        return f"{profile}{idea_tail}"
    return idea_tail or profile


def normalize_creator_text(text: str) -> str:
    value = re.sub(r"[/|｜]+", " ", text or "")
    value = re.sub(r"\s+", " ", value).strip(" -_|，,。.;；:")
    return value


def normalize_creator_direction(direction: str, idea: str) -> str:
    value = normalize_creator_text(direction)
    combined = f"{value} {idea}"
    if "擦边" in value and any(token in combined for token in ["跳", "舞", "舞蹈"]):
        value = value.replace("美女擦边", "颜值舞蹈").replace("擦边", "颜值向")
    elif "擦边" in value:
        value = value.replace("美女擦边", "颜值向内容").replace("擦边", "高点击表达")
    return normalize_creator_text(value)


def merge_creator_profile(field_name: str, direction: str) -> str:
    if field_name and direction:
        if field_name in direction:
            return direction
        if direction in field_name:
            return field_name
        return f"{field_name}{direction}"
    return field_name or direction


def refine_creator_profile(field_name: str, direction: str, idea: str) -> str:
    profile = merge_creator_profile(field_name, direction)
    combined = normalize_creator_text(f"{field_name} {direction} {idea}")

    if "颜值舞蹈" in profile:
        return "颜值向舞蹈账号"
    if "颜值向内容" in profile:
        return "颜值向内容账号"
    if any(token in combined for token in ["美女", "女生", "小姐姐", "颜值"]) and any(
        token in combined for token in ["跳", "跳舞", "舞", "舞蹈"]
    ):
        return "颜值向舞蹈账号"
    return profile


def strip_leading_context(text: str, contexts: list[str]) -> str:
    result = text
    for context in contexts:
        if not context:
            continue
        if result.startswith(context):
            result = result[len(context):].strip(" ，,。.;；:")
    return result


def extract_creator_keywords(text: str) -> list[str]:
    clean = normalize_creator_text(text).lower()
    words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", clean)
    keywords: list[str] = []
    for word in words:
        if word in CREATOR_STOPWORDS:
            continue
        if word not in keywords:
            keywords.append(word)
    return keywords


def collect_creator_trending_keywords(videos: list[dict], partition_name: str) -> list[str]:
    counts: dict[str, int] = {}
    for item in videos[:12]:
        title = item.get("title", "")
        for keyword in extract_creator_keywords(title):
            counts[keyword] = counts.get(keyword, 0) + 1

    if counts:
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [keyword for keyword, _ in ranked[:4]]
    return CREATOR_PARTITION_ANGLES.get(partition_name, CREATOR_PARTITION_ANGLES["knowledge"])[:3]


def build_creator_reason(
    question_topic: str,
    partition_name: str,
    source_count: int,
    trending_keywords: list[str],
    angle_label: str,
) -> str:
    partition_label = PARTITION_LABELS.get(partition_name, partition_name)
    keyword_text = "、".join(trending_keywords[:3]) if trending_keywords else "开场反差、结果感、互动点"
    return (
        f"结合当前{partition_label}分区的 {source_count} 条热点/样本数据，近期更容易起量的结构集中在「{keyword_text}」。"
        f"这条选题先解决“{question_topic}”这个具体问题，更适合拿来做第一轮测试；"
        f"表达重点建议放在 {angle_label}。"
    )


def build_creator_topic_result(
    field_name: str,
    direction: str,
    idea: str,
    partition_name: str,
    style: str,
    base_topic_result: dict,
) -> dict:
    normalized_partition = CONFIG.normalize_partition(partition_name)
    seed_topic = build_seed_topic(field_name, direction, idea)
    profile = refine_creator_profile(
        normalize_creator_text(field_name),
        normalize_creator_direction(direction, idea),
        normalize_creator_text(idea),
    )
    videos = base_topic_result.get("videos", []) or []
    source_count = int(base_topic_result.get("source_count") or 0)
    trending_keywords = collect_creator_trending_keywords(videos, normalized_partition)
    angle_labels = CREATOR_PARTITION_ANGLES.get(normalized_partition, CREATOR_PARTITION_ANGLES["knowledge"])

    question_topic = seed_topic or profile or normalize_creator_text(idea) or "这类内容第一条该怎么做"
    is_dance_case = any(token in f"{profile} {idea}" for token in ["跳", "舞", "舞蹈"])

    if is_dance_case:
        topics = [
            f"{profile or '这类账号'}第一条视频跳什么更容易起量",
            f"{profile or '这类账号'}别一上来就硬跳：先做哪种开场动作更容易进推荐",
            f"{profile or '这类账号'}做系列内容时，第1条、第2条、第3条分别跳什么",
        ]
    else:
        topics = [
            f"{profile or question_topic}第一条视频先做什么更容易起量",
            f"别直接硬拍 {profile or question_topic}：先做哪种切口更容易进推荐",
            f"{profile or question_topic}做成系列内容时，第1条、第2条、第3条分别拍什么",
        ]

    ideas = []
    base_keywords = extract_creator_keywords(profile or question_topic)[:3]
    for index, topic in enumerate(topics):
        idea_keywords = list(dict.fromkeys(base_keywords + trending_keywords[:3] + [angle_labels[index % len(angle_labels)]]))[:6]
        ideas.append(
            {
                "topic": topic,
                "reason": build_creator_reason(question_topic, normalized_partition, source_count, trending_keywords, angle_labels[index % len(angle_labels)]),
                "video_type": style or "干货",
                "keywords": idea_keywords,
                "score": 100 - index * 3,
            }
        )

    return {
        "ideas": ideas,
        "source_count": source_count,
        "videos": videos,
        "seed_topic": seed_topic,
        "normalized_profile": profile,
        "trending_keywords": trending_keywords,
    }


def find_json_object(text: str, marker: str) -> str | None:
    index = text.find(marker)
    if index < 0:
        return None

    start = index + len(marker)
    while start < len(text) and text[start].isspace():
        start += 1

    if start >= len(text) or text[start] != "{":
        return None

    depth = 0
    in_string = False
    escaped = False
    for cursor in range(start, len(text)):
        char = text[cursor]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : cursor + 1]
    return None


def extract_meta(html: str, attr_name: str, attr_value: str) -> str:
    pattern = rf'<meta[^>]+{attr_name}="{re.escape(attr_value)}"[^>]+content="([^"]*)"'
    match = re.search(pattern, html, flags=re.IGNORECASE)
    return unescape(match.group(1)).strip() if match else ""


def extract_first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return unescape(match.group(1)).strip() if match else ""


def extract_initial_state(html: str) -> dict:
    for marker in ("window.__INITIAL_STATE__=", "__INITIAL_STATE__="):
        raw = find_json_object(html, marker)
        if not raw:
            continue
        try:
            data = json.loads(unescape(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def normalize_html_info(html: str, state: dict, bvid: str) -> dict:
    video_data = state.get("videoData") or state.get("videoInfo") or state.get("archive") or {}
    owner = video_data.get("owner") or state.get("upData") or {}
    stat = video_data.get("stat") or {}

    title = (
        video_data.get("title")
        or state.get("h1Title")
        or extract_meta(html, "property", "og:title")
        or extract_meta(html, "name", "title")
        or extract_first_match(html, r'"title"\s*:\s*"([^"]+)"')
    )
    tname = video_data.get("tname") or state.get("tname") or extract_first_match(html, r'"tname"\s*:\s*"([^"]*)"')
    tid = safe_int(video_data.get("tid") or state.get("tid") or extract_first_match(html, r'"tid"\s*:\s*(\d+)'))
    duration = safe_int(video_data.get("duration") or extract_first_match(html, r'"duration"\s*:\s*(\d+)'))

    mid = (
        owner.get("mid")
        or owner.get("mid_id")
        or extract_first_match(html, r'"owner"\s*:\s*\{.*?"mid"\s*:\s*(\d+)')
        or extract_first_match(html, r'"mid"\s*:\s*(\d+)')
        or 0
    )
    up_name = (
        owner.get("name")
        or owner.get("uname")
        or extract_meta(html, "name", "author")
        or extract_first_match(html, r'"owner"\s*:\s*\{.*?"name"\s*:\s*"([^"]*)"')
        or extract_first_match(html, r'"uname"\s*:\s*"([^"]*)"')
    )
    if not title:
        raise ValueError("网页源码中未找到视频标题")

    return {
        "bvid": video_data.get("bvid") or bvid,
        "title": title,
        "tid": tid,
        "tname": tname,
        "pic": (
            video_data.get("pic")
            or video_data.get("cover")
            or extract_meta(html, "property", "og:image")
            or extract_first_match(html, r'"pic"\s*:\s*"([^"]*)"')
        ),
        "duration": duration,
        "owner": {
            "mid": safe_int(mid),
            "name": up_name or "",
        },
        "stat": {
            "view": safe_int(stat.get("view") or extract_first_match(html, r'"view"\s*:\s*(\d+)')),
            "like": safe_int(stat.get("like") or extract_first_match(html, r'"like"\s*:\s*(\d+)')),
            "coin": safe_int(stat.get("coin") or extract_first_match(html, r'"coin"\s*:\s*(\d+)')),
            "favorite": safe_int(stat.get("favorite") or extract_first_match(html, r'"favorite"\s*:\s*(\d+)')),
            "reply": safe_int(stat.get("reply") or extract_first_match(html, r'"reply"\s*:\s*(\d+)')),
            "share": safe_int(stat.get("share") or extract_first_match(html, r'"share"\s*:\s*(\d+)')),
        },
    }


def fetch_video_info_via_public_api(bvid: str) -> dict:
    query = urlencode({"bvid": bvid})
    payload = fetch_json(f"https://api.bilibili.com/x/web-interface/view?{query}")
    if payload.get("code") != 0:
        message = payload.get("message") or payload.get("msg") or "official api failed"
        raise ValueError(f"B站公开视频接口失败: {message}")
    info = payload.get("data") or {}
    if not info:
        raise ValueError("B站公开视频接口未返回视频详情")
    return info


def fetch_video_info_via_html(url: str, bvid: str) -> dict:
    candidates = [url.strip(), f"https://www.bilibili.com/video/{bvid}"]
    errors: list[str] = []

    for candidate in candidates:
        if not candidate:
            continue
        try:
            html = fetch_text(candidate, timeout=12)
            state = extract_initial_state(html)
            return normalize_html_info(html, state, bvid)
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")

    raise ValueError("网页源码解析失败: " + "；".join(errors))


def fetch_video_info(url: str, bvid: str) -> dict:
    errors: list[str] = []

    try:
        return fetch_video_info_via_public_api(bvid)
    except Exception as exc:
        errors.append(f"public api: {exc}")

    try:
        return sync(video.Video(bvid=bvid).get_info())
    except Exception as exc:
        errors.append(f"bilibili_api: {exc}")

    try:
        return fetch_video_info_via_html(url, bvid)
    except Exception as exc:
        errors.append(f"html: {exc}")

    raise ValueError("；".join(errors))


def extract_video_stats(info: dict) -> dict:
    stat = info.get("stat") or {}
    view = safe_int(stat.get("view") or info.get("play"))
    like = safe_int(stat.get("like"))
    coin = safe_int(stat.get("coin"))
    favorite = safe_int(stat.get("favorite"))
    reply = safe_int(stat.get("reply"))
    share = safe_int(stat.get("share"))
    return {
        "view": view,
        "like": like,
        "coin": coin,
        "favorite": favorite,
        "reply": reply,
        "share": share,
        "like_rate": like / max(view, 1),
        "coin_rate": coin / max(view, 1),
        "favorite_rate": favorite / max(view, 1),
    }


def build_resolved_payload(info: dict, bvid: str) -> dict:
    owner = info.get("owner", {})
    mid = safe_int(owner.get("mid"))
    up_name = owner.get("name") or owner.get("uname") or ""
    title = info.get("title", "")
    tid = safe_int(info.get("tid"))
    tname = info.get("tname", "")
    stats = extract_video_stats(info)
    partition = map_partition(tname, tid)
    topic = build_topic(title)
    style = guess_style(title, partition, tname)
    partition_label = PARTITION_LABELS.get(partition, partition)

    return {
        "bv_id": bvid,
        "mid": mid,
        "up_ids": [mid] if mid else [],
        "up_name": up_name,
        "cover": info.get("pic") or info.get("cover") or "",
        "partition": partition,
        "partition_label": partition_label,
        "tid": tid,
        "tname": tname,
        "title": title,
        "topic": topic,
        "style": style,
        "duration": safe_int(info.get("duration")),
        "stats": stats,
        "summary": f"{up_name or '未知UP'} · {tname or partition_label}",
    }


def resolve_video_payload(url: str) -> dict:
    bvid = extract_bvid(url)
    info = fetch_video_info(url, bvid)
    return build_resolved_payload(info, bvid)


def is_resolved_payload_usable(payload: object, url: str) -> bool:
    if not isinstance(payload, dict):
        return False
    bv_id = str(payload.get("bv_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    stats = payload.get("stats")
    if not bv_id or not title or not isinstance(stats, dict):
        return False

    try:
        expected_bv = extract_bvid(url)
    except Exception:
        expected_bv = ""

    return not expected_bv or bv_id.upper() == expected_bv.upper()


def inspect_title_strength(title: str) -> list[str]:
    points: list[str] = []
    if re.search(r"\d", title):
        points.append("标题里有数字或年份，信息密度更高。")
    if any(token in title for token in ["为什么", "如何", "别再", "终于", "实测", "教程", "攻略"]):
        points.append("标题具有明确的问题导向或结果导向。")
    if any(token in title for token in ["！", "?", "？"]):
        points.append("标题带有情绪张力或悬念。")
    if 8 <= len(title) <= 28:
        points.append("标题长度适中，表达相对集中。")
    if not points:
        points.append("标题主题明确，但还可以继续强化结果感和反差感。")
    return points


def classify_video_performance(resolved: dict) -> dict:
    stats = resolved.get("stats", {})
    view = safe_int(stats.get("view"))
    like_rate = float(stats.get("like_rate") or 0.0)
    coin_rate = float(stats.get("coin_rate") or 0.0)
    favorite_rate = float(stats.get("favorite_rate") or 0.0)

    score = 0
    reasons: list[str] = []

    if view >= 100000:
        score += 2
        reasons.append(f"当前播放 {view:,}，已经达到明显起量水平。")
    elif view >= 30000:
        score += 1
        reasons.append(f"当前播放 {view:,}，有一定自然流量基础。")
    else:
        reasons.append(f"当前播放 {view:,}，仍有明显提升空间。")

    if like_rate >= 0.05:
        score += 2
        reasons.append(f"点赞率 {like_rate:.2%}，互动质量较高。")
    elif like_rate >= 0.03:
        score += 1
        reasons.append(f"点赞率 {like_rate:.2%}，基本达到可继续放大的水平。")
    else:
        reasons.append(f"点赞率 {like_rate:.2%}，说明内容共鸣还不够强。")

    if favorite_rate >= 0.01 or coin_rate >= 0.005:
        score += 1
        reasons.append("收藏/投币数据说明内容有一定留存价值。")
    else:
        reasons.append("收藏和投币偏弱，内容的可复用价值还不够突出。")

    is_hot = score >= 4
    return {
        "label": "热门爆款" if is_hot else "播放偏低",
        "is_hot": is_hot,
        "score": score,
        "reasons": reasons,
        "summary": "这条视频更接近热门爆款，可重点拆解它为什么能火。"
        if is_hot
        else "这条视频当前更像播放偏低的视频，优先做针对性优化。",
    }


def build_hot_analysis(resolved: dict, performance: dict, topic_result: dict) -> dict:
    stats = resolved.get("stats", {})
    followup_topics = [idea.get("topic", "") for idea in topic_result.get("ideas", []) if idea.get("topic")]
    analysis_points = performance["reasons"] + inspect_title_strength(resolved.get("title", ""))
    analysis_points.append(
        f"当前分区为 {resolved.get('partition_label', resolved.get('partition', '未知分区'))}，"
        f"说明视频题材与该分区受众存在较高匹配度。"
    )
    if followup_topics:
        analysis_points.append("围绕当前视频继续延展，仍然有可继续放大的选题空间。")
    return {
        "analysis_points": analysis_points,
        "followup_topics": followup_topics[:3],
    }


def build_low_performance_analysis(resolved: dict, performance: dict, optimize_result: dict, topic_result: dict) -> dict:
    next_topics = [idea.get("topic", "") for idea in topic_result.get("ideas", []) if idea.get("topic")]
    return {
        "analysis_points": performance["reasons"] + [optimize_result.get("diagnosis", "")],
        "next_topics": next_topics[:3],
        "title_suggestions": optimize_result.get("optimized_titles", [])[:2],
        "cover_suggestion": optimize_result.get("cover_suggestion", ""),
        "content_suggestions": optimize_result.get("content_suggestions", [])[:5],
    }


def build_runtime_payload() -> dict:
    mode = CONFIG.runtime_mode()
    llm_enabled = CONFIG.llm_enabled()
    return {
        "mode": mode,
        "mode_label": RUNTIME_MODE_LABELS.get(mode, mode),
        "llm_enabled": llm_enabled,
        "chat_available": llm_enabled,
        "mode_title": "当前运行中：LLM Agent 模式" if llm_enabled else "当前运行中：无 Key 逻辑模式",
        "mode_description": "已切换到 LLM Agent 中枢，分析、决策和生成全部由大模型实时完成。"
        if llm_enabled
        else "当前未配置 LLM_API_KEY，系统运行在纯代码规则模式，不会消耗 token。",
        "token_policy": "会消耗 token，聊天助手已启用。" if llm_enabled else "不会消耗 token，聊天助手当前关闭。",
        "switch_hint": "如果要切回逻辑模式，清空 .env 里的 LLM_API_KEY 后重启服务。"
        if llm_enabled
        else "如果要切到 LLM 模式，填写 .env 里的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL 后重启服务。",
    }


def serialize_video_metric(video_metric: object) -> dict:
    payload = video_metric.to_dict() if hasattr(video_metric, "to_dict") else dict(video_metric)
    return {
        "bvid": payload.get("bvid", ""),
        "title": payload.get("title", ""),
        "author": payload.get("author", ""),
        "cover": payload.get("cover") or payload.get("pic") or payload.get("thumbnail") or "",
        "mid": safe_int(payload.get("mid")),
        "view": safe_int(payload.get("view")),
        "like": safe_int(payload.get("like")),
        "coin": safe_int(payload.get("coin")),
        "favorite": safe_int(payload.get("favorite")),
        "reply": safe_int(payload.get("reply")),
        "share": safe_int(payload.get("share")),
        "duration": safe_int(payload.get("duration")),
        "avg_view_duration": float(payload.get("avg_view_duration") or 0.0),
        "like_rate": float(payload.get("like_rate") or 0.0),
        "completion_rate": float(payload.get("completion_rate") or 0.0),
        "competition_score": float(payload.get("competition_score") or 0.0),
        "source": payload.get("source", ""),
        "url": payload.get("url", ""),
    }


def build_market_snapshot(partition_name: str, up_ids: list[int] | None = None) -> dict:
    normalized_partition = CONFIG.normalize_partition(partition_name)

    try:
        hot_board = [serialize_video_metric(item) for item in RAW_TOPIC_AGENT.fetch_hot_videos()[:6]]
    except Exception:
        hot_board = []

    try:
        partition_samples = [
            serialize_video_metric(item)
            for item in RAW_TOPIC_AGENT.fetch_partition_videos(normalized_partition)[:6]
        ]
    except Exception:
        partition_samples = []

    try:
        peer_samples = [
            serialize_video_metric(item)
            for item in RAW_TOPIC_AGENT.fetch_peer_up_videos(up_ids)[:6]
        ]
    except Exception:
        peer_samples = []

    return {
        "partition": normalized_partition,
        "partition_label": PARTITION_LABELS.get(normalized_partition, normalized_partition),
        "source_count": len(hot_board) + len(partition_samples) + len(peer_samples),
        "hot_board": hot_board,
        "partition_samples": partition_samples,
        "peer_samples": peer_samples,
    }


def select_reference_videos(sources: list[dict], exclude_bvid: str = "", limit: int = 6) -> list[dict]:
    ranked = sorted(
        [item for item in sources if item.get("url")],
        key=lambda item: (
            -safe_int(item.get("view")),
            -float(item.get("like_rate") or 0.0),
            item.get("title", ""),
        ),
    )
    result: list[dict] = []
    seen: set[str] = set()
    for item in ranked:
        bvid = (item.get("bvid") or "").strip()
        url = (item.get("url") or "").strip()
        if not url or url in seen:
            continue
        if exclude_bvid and bvid.lower() == exclude_bvid.lower():
            continue
        seen.add(url)
        result.append(
            {
                "title": item.get("title", ""),
                "url": url,
                "author": item.get("author", ""),
                "cover": item.get("cover", ""),
                "view": safe_int(item.get("view")),
                "like_rate": float(item.get("like_rate") or 0.0),
                "source": item.get("source", ""),
            }
        )
        if len(result) >= limit:
            break
    return result


def build_reference_videos_from_market_snapshot(market_snapshot: dict, exclude_bvid: str = "") -> list[dict]:
    sources = (
        (market_snapshot.get("hot_board") or [])
        + (market_snapshot.get("peer_samples") or [])
        + (market_snapshot.get("partition_samples") or [])
    )
    return select_reference_videos(sources, exclude_bvid=exclude_bvid, limit=6)


def extract_reference_links_from_tool_observations(observations: list[dict], exclude_bvid: str = "") -> list[dict]:
    sources: list[dict] = []
    for item in observations or []:
        observation = item.get("observation") if isinstance(item, dict) else {}
        if not isinstance(observation, dict):
            continue
        if isinstance(observation.get("market_snapshot"), dict):
            sources.extend(build_reference_videos_from_market_snapshot(observation.get("market_snapshot"), exclude_bvid))
        for key in ("hot_board", "peer_samples", "partition_samples"):
            value = observation.get(key)
            if isinstance(value, list):
                sources.extend(select_reference_videos(value, exclude_bvid=exclude_bvid, limit=6))
    return select_reference_videos(sources, exclude_bvid=exclude_bvid, limit=6)


def build_llm_video_payload(info: dict, bvid: str, url: str) -> dict:
    owner = info.get("owner", {})
    mid = safe_int(owner.get("mid"))
    up_name = owner.get("name") or owner.get("uname") or ""
    title = info.get("title", "")
    tid = safe_int(info.get("tid"))
    tname = info.get("tname", "")
    retrieval_partition = map_partition(tname, tid)

    return {
        "bv_id": bvid,
        "url": url.strip(),
        "title": title,
        "up_name": up_name,
        "mid": mid,
        "up_ids": [mid] if mid else [],
        "tid": tid,
        "tname": tname,
        "duration": safe_int(info.get("duration")),
        "stats": extract_video_stats(info),
        "retrieval_partition": retrieval_partition,
        "retrieval_partition_label": PARTITION_LABELS.get(retrieval_partition, retrieval_partition),
    }


def build_creator_briefing(field_name: str, direction: str, idea: str, partition_name: str) -> dict:
    normalized_partition = CONFIG.normalize_partition(partition_name)
    return {
        "user_input": {
            "field": field_name.strip(),
            "direction": direction.strip(),
            "idea": idea.strip(),
            "partition": partition_name,
            "normalized_partition": normalized_partition,
        },
        "market_snapshot": build_market_snapshot(normalized_partition),
    }


def build_video_briefing(url: str) -> dict:
    bvid = extract_bvid(url)
    info = fetch_video_info(url, bvid)
    video_payload = build_llm_video_payload(info, bvid, url)
    market_snapshot = build_market_snapshot(video_payload.get("retrieval_partition", "knowledge"), video_payload.get("up_ids"))
    return {
        "video": video_payload,
        "market_snapshot": market_snapshot,
    }


def build_hot_board_snapshot(partition_name: str) -> dict:
    market_snapshot = build_market_snapshot(partition_name)
    return {
        "partition": market_snapshot.get("partition"),
        "partition_label": market_snapshot.get("partition_label"),
        "hot_board": market_snapshot.get("hot_board", []),
        "partition_samples": market_snapshot.get("partition_samples", []),
    }


def extract_first_bili_url(text: str) -> str:
    match = re.search(r"https?://[^\s]+", text or "", flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


def get_llm_workspace_agent() -> LLMWorkspaceAgent:
    global LLM_WORKSPACE_AGENT
    if not CONFIG.llm_enabled():
        raise RuntimeError("当前未配置 LLM_API_KEY，LLM Agent 模式不可用。")

    if LLM_WORKSPACE_AGENT is None:
        LLM_WORKSPACE_AGENT = LLMWorkspaceAgent(
            tools=[
                AgentTool(
                    name="creator_briefing",
                    description="根据领域、方向、想法和分区，抓取热点榜、分区样本、同类样本原始数据。输入: {field, direction, idea, partition}",
                    handler=lambda payload: build_creator_briefing(
                        payload.get("field", ""),
                        payload.get("direction", ""),
                        payload.get("idea", ""),
                        payload.get("partition", "knowledge"),
                    ),
                ),
                AgentTool(
                    name="video_briefing",
                    description="解析 B 站视频链接，返回视频公开数据，并抓取相同分区与同类 UP 的原始样本。输入: {url}",
                    handler=lambda payload: build_video_briefing(payload.get("url", "")),
                ),
                AgentTool(
                    name="hot_board_snapshot",
                    description="获取指定分区的热点榜和分区样本原始数据，适合回答趋势、热点、近期什么内容火。输入: {partition}",
                    handler=lambda payload: build_hot_board_snapshot(payload.get("partition", "knowledge")),
                ),
            ]
        )
    return LLM_WORKSPACE_AGENT


def run_llm_module_create(data: dict) -> dict:
    agent = get_llm_workspace_agent()
    response_contract = (
        "返回一个 JSON 对象，字段必须包含：\n"
        "- normalized_profile: 字符串，整理后的创作方向\n"
        "- seed_topic: 字符串，当前要解决的核心问题\n"
        "- partition: 字符串，分区名\n"
        "- style: 字符串，文案风格\n"
        "- chosen_topic: 字符串，最终主选题\n"
        "- topic_result: 对象，至少包含 ideas(长度 3 的数组)，每项包含 topic, reason, video_type, keywords\n"
        "- copy_result: 对象，包含 topic, style, titles(3个), script(至少4段，含 section/duration/content), description, tags, pinned_comment\n"
    )
    try:
        return agent.run_structured(
        task_name="module_create",
        task_goal="基于用户输入和实时市场样本，为创作者输出更容易起量的 3 个选题，并生成完整可发布文案。",
        user_payload={
            "field": (data.get("field") or "").strip(),
            "direction": (data.get("direction") or "").strip(),
            "idea": (data.get("idea") or "").strip(),
            "partition": (data.get("partition") or "knowledge").strip() or "knowledge",
            "style": (data.get("style") or "干货").strip() or "干货",
        },
        response_contract=response_contract,
        allowed_tools=["creator_briefing"],
        required_tools=["creator_briefing"],
        required_final_keys=["normalized_profile", "seed_topic", "partition", "style", "chosen_topic", "topic_result", "copy_result"],
            max_steps=2,
        )
    except Exception as exc:
        fallback_result = run_llm_module_create_fallback(data)
        fallback_result["llm_warning"] = f"Agent 中枢生成失败，已切换到单次 LLM 回退：{exc}"
        return fallback_result


def run_llm_module_create_fallback(data: dict) -> dict:
    llm = LLMClient()
    llm.require_available()

    field_name = (data.get("field") or "").strip()
    direction = (data.get("direction") or "").strip()
    idea = (data.get("idea") or "").strip()
    partition_name = (data.get("partition") or "knowledge").strip() or "knowledge"
    style = (data.get("style") or "干货").strip() or "干货"
    briefing = build_creator_briefing(field_name, direction, idea, partition_name)

    system_prompt = (
        "You are a Bilibili topic and copywriting assistant. "
        "You already have user input and market samples. "
        "Return JSON only."
    )
    user_prompt = (
        "Return one JSON object with these required keys: "
        "normalized_profile, seed_topic, partition, style, chosen_topic, topic_result, copy_result.\n\n"
        f"user_input={json.dumps({'field': field_name, 'direction': direction, 'idea': idea, 'partition': partition_name, 'style': style}, ensure_ascii=False)}\n\n"
        f"creator_briefing={json.dumps(briefing, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "1. partition and style must reuse the current input.\n"
        "2. chosen_topic must be concrete and natural, not generic template wording.\n"
        "3. topic_result.ideas must contain 3 items, each with topic, reason, video_type, keywords.\n"
        "4. copy_result must include topic, style, titles(3), script(at least 4 sections with section/duration/content), description, tags, pinned_comment.\n"
        "5. Avoid repetitive phrases like a universal '高效做法' template unless the topic really demands it."
    )
    result = llm.invoke_json_required(system_prompt, user_prompt)
    if not isinstance(result, dict):
        raise ValueError("LLM module create fallback returned invalid format")
    result.setdefault("runtime_mode", "llm_agent")
    result.setdefault("agent_trace", ["creator_briefing", "llm_direct_fallback"])
    return result


def run_llm_module_analyze(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    agent = get_llm_workspace_agent()
    response_contract = (
        "返回一个 JSON 对象，字段必须包含：\n"
        "- resolved: 对象，包含 bv_id, title, up_name, tname, partition, partition_label, stats\n"
        "- performance: 对象，包含 label, is_hot, score, reasons, summary\n"
        "- topic_result: 对象，至少包含 ideas(长度 3 的数组)，每项包含 topic, reason, video_type, keywords\n"
        "- optimize_result: 对象，包含 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions\n"
        "- copy_result: 对象或 null；如果你判断视频表现偏低，则必须返回一套新的标题/脚本/简介/标签/置顶评论\n"
        "- analysis: 对象，包含 analysis_points，并根据判断补充 followup_topics 或 next_topics、title_suggestions、cover_suggestion、content_suggestions\n"
    )
    result = agent.run_structured(
        task_name="module_analyze",
        task_goal="基于后端已经解析出的当前视频真实信息，以及同类市场样本，判断它更接近爆款还是低表现，并解释原因，同时给出后续选题和优化方案。",
        user_payload={
            "url": (data.get("url") or "").strip(),
            "parsed_video": resolved,
            "market_snapshot": market_snapshot,
        },
        response_contract=response_contract,
        allowed_tools=["hot_board_snapshot"],
        required_final_keys=["resolved", "performance", "topic_result", "optimize_result", "analysis", "copy_result"],
    )
    result["resolved"] = resolved
    result["reference_videos"] = build_reference_videos_from_market_snapshot(market_snapshot, resolved.get("bv_id", ""))
    return result


def run_llm_module_analyze_fallback(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    llm = LLMClient()
    llm.require_available()
    system_prompt = (
        "你是 B 站视频分析助手。"
        "当前已经拿到后端解析出的真实视频信息和同类样本，请直接完成爆款/低表现判断、原因拆解、优化建议和后续选题。"
        "不要输出解释性废话，只返回 JSON。"
    )
    user_prompt = (
        "请根据下面的数据直接输出 JSON，对象字段必须包含："
        "resolved, performance, topic_result, optimize_result, copy_result, analysis。\n\n"
        f"当前视频真实信息：{json.dumps(resolved, ensure_ascii=False)}\n\n"
        f"市场样本：{json.dumps(market_snapshot, ensure_ascii=False)}\n\n"
        "要求：\n"
        "1. resolved 直接复用当前视频真实信息，不要改 BV、标题、播放等字段。\n"
        "2. performance 必须包含 label, is_hot, score, reasons, summary。\n"
        "3. topic_result.ideas 输出 3 个后续选题，每项包含 topic, reason, video_type, keywords。\n"
        "4. optimize_result 输出 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions。\n"
        "5. 如果你判断 is_hot=true，则 copy_result 返回 null，analysis 重点输出 analysis_points 和 followup_topics。\n"
        "6. 如果你判断 is_hot=false，则 copy_result 必须输出一版新文案，analysis 重点输出 analysis_points, next_topics, title_suggestions, cover_suggestion, content_suggestions。"
    )
    result = llm.invoke_json_required(system_prompt, user_prompt)
    if not isinstance(result, dict):
        raise ValueError("LLM fallback 返回格式无效")
    result["resolved"] = resolved
    result.setdefault("reference_videos", build_reference_videos_from_market_snapshot(market_snapshot, resolved.get("bv_id", "")))
    result.setdefault("runtime_mode", "llm_agent")
    result.setdefault("agent_trace", ["llm_direct_fallback"])
    return result


def run_llm_chat(data: dict) -> dict:
    agent = get_llm_workspace_agent()
    message = (data.get("message") or "").strip()
    history = data.get("history") if isinstance(data.get("history"), list) else []
    context = data.get("context") if isinstance(data.get("context"), dict) else {}

    creator_context = {
        "field": (context.get("field") or "").strip(),
        "direction": (context.get("direction") or "").strip(),
        "idea": (context.get("idea") or "").strip(),
        "partition": (context.get("partition") or "").strip(),
        "style": (context.get("style") or "").strip(),
    }
    video_url = (context.get("videoLink") or "").strip() or extract_first_bili_url(message)

    response_contract = (
        "返回一个 JSON 对象，字段必须包含：\n"
        "- reply: 字符串，直接回答用户问题；如果信息不足，要明确指出还缺什么\n"
        "- suggested_next_actions: 字符串数组，可为空\n"
        "- mode: 固定返回 llm_agent\n"
    )
    result = agent.run_structured(
        task_name="workspace_chat",
        task_goal="理解用户自然语言意图，自主决定是否调用工具来完成选题、视频分析、热点判断、文案建议等问题，并用中文直接回复。",
        user_payload={
            "message": message,
            "history": history[-8:],
            "creator_context": creator_context,
            "video_url": video_url,
        },
        response_contract=response_contract,
        allowed_tools=["creator_briefing", "video_briefing", "hot_board_snapshot"],
        required_final_keys=["reply", "suggested_next_actions", "mode"],
    )
    result["reference_links"] = extract_reference_links_from_tool_observations(
        result.get("tool_observations", []),
        exclude_bvid="",
    )
    return result


@app.get("/api/runtime-info")
def api_runtime_info():
    return jsonify({"success": True, "data": build_runtime_payload()})


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/resolve-bili-link")
def api_resolve_bili_link():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "请先输入 B 站视频链接"}), 400

    bvid = ""
    try:
        bvid = extract_bvid(url)
        info = fetch_video_info(url, bvid)
        return jsonify({"success": True, "data": build_resolved_payload(info, bvid)})
    except Exception as exc:
        suffix = f"（BV={bvid}）" if bvid else ""
        return jsonify({"success": False, "error": f"链接解析失败{suffix}：{exc}"}), 400


@app.post("/api/module-create")
def api_module_create():
    data = request.get_json(silent=True) or {}
    field_name = (data.get("field") or "").strip()
    direction = (data.get("direction") or "").strip()
    idea = (data.get("idea") or "").strip()
    if not field_name and not direction and not idea:
        return jsonify({"success": False, "error": "请至少输入领域、方向、想法中的一项"}), 400

    if CONFIG.llm_enabled():
        try:
            return jsonify({"success": True, "data": run_llm_module_create(data)})
        except Exception as exc:
            return jsonify({"success": False, "error": f"LLM Agent 生成失败：{exc}"}), 500

    seed_topic = build_seed_topic(field_name, direction, idea)
    partition_name = CONFIG.normalize_partition((data.get("partition") or "knowledge").strip() or "knowledge")
    style = (data.get("style") or "干货").strip() or "干货"

    raw_topic_result = run_topic(
        partition_name=partition_name,
        up_ids=None,
        seed_topic=seed_topic,
    )
    topic_result = build_creator_topic_result(
        field_name=field_name,
        direction=direction,
        idea=idea,
        partition_name=partition_name,
        style=style,
        base_topic_result=raw_topic_result,
    )
    chosen_topic = (topic_result.get("ideas") or [{}])[0].get("topic") or seed_topic
    copy_result = run_copy(topic=chosen_topic, style=style)

    return jsonify(
        {
            "success": True,
            "data": {
                "seed_topic": seed_topic,
                "normalized_profile": topic_result.get("normalized_profile", ""),
                "partition": partition_name,
                "style": style,
                "topic_result": topic_result,
                "copy_result": copy_result,
                "chosen_topic": chosen_topic,
            },
        }
    )


@app.post("/api/module-analyze")
def api_module_analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "请先输入 B 站视频链接"}), 400

    try:
        resolved = data.get("resolved") if is_resolved_payload_usable(data.get("resolved"), url) else resolve_video_payload(url)
    except Exception as exc:
        return jsonify({"success": False, "error": f"链接解析失败：{exc}"}), 400

    if CONFIG.llm_enabled():
        try:
            market_snapshot = build_market_snapshot(resolved.get("partition"), resolved.get("up_ids"))
            return jsonify({"success": True, "data": run_llm_module_analyze(data, resolved, market_snapshot)})
        except Exception as exc:
            try:
                market_snapshot = build_market_snapshot(resolved.get("partition"), resolved.get("up_ids"))
                fallback_result = run_llm_module_analyze_fallback(data, resolved, market_snapshot)
                fallback_result["llm_warning"] = f"Agent 中枢执行失败，已切换到 LLM 直出分析：{exc}"
                return jsonify({"success": True, "data": fallback_result})
            except Exception as fallback_exc:
                return jsonify({"success": False, "error": f"LLM Agent 分析失败：{exc}；LLM fallback 也失败：{fallback_exc}"}), 500

    topic_result = run_topic(
        partition_name=resolved.get("partition"),
        up_ids=resolved.get("up_ids"),
        seed_topic=resolved.get("topic"),
    )
    optimize_result = run_optimize(resolved.get("bv_id", "BV1Demo411111"))
    performance = classify_video_performance(resolved)

    copy_result = None
    analysis = {}
    if performance["is_hot"]:
        analysis = build_hot_analysis(resolved, performance, topic_result)
    else:
        copy_result = run_copy(topic=resolved.get("topic") or resolved.get("title") or "视频优化", style=resolved.get("style", "干货"))
        analysis = build_low_performance_analysis(resolved, performance, optimize_result, topic_result)

    reference_videos = select_reference_videos(topic_result.get("videos", []), exclude_bvid=resolved.get("bv_id", ""), limit=6)

    return jsonify(
        {
            "success": True,
            "data": {
                "resolved": resolved,
                "performance": performance,
                "topic_result": topic_result,
                "optimize_result": optimize_result,
                "copy_result": copy_result,
                "analysis": analysis,
                "reference_videos": reference_videos,
            },
        }
    )


@app.post("/api/chat")
def api_chat():
    if not CONFIG.llm_enabled():
        return jsonify({"success": False, "error": "当前是无 Key 规则模式，智能对话助手仅在配置 LLM_API_KEY 后可用。"}), 400

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "请输入对话内容"}), 400

    try:
        result = run_llm_chat(data)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": f"智能对话失败：{exc}"}), 500


@app.post("/api/topic")
def api_topic():
    data = request.get_json(silent=True) or {}
    result = run_topic(
        partition_name=data.get("partition", "knowledge"),
        up_ids=data.get("up_ids"),
        seed_topic=data.get("topic"),
    )
    return jsonify({"success": True, "data": result})


@app.post("/api/copy")
def api_copy():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "B站内容提效")
    style = data.get("style", "干货")
    result = run_copy(topic=topic, style=style)
    return jsonify({"success": True, "data": result})


@app.post("/api/operate")
def api_operate():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    dry_run = bool(data.get("dry_run", True))
    result = run_operate(bv_id=bv_id, dry_run=dry_run)
    return jsonify({"success": True, "data": result})


@app.post("/api/optimize")
def api_optimize():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    result = run_optimize(bv_id=bv_id)
    return jsonify({"success": True, "data": result})


@app.post("/api/pipeline")
def api_pipeline():
    data = request.get_json(silent=True) or {}
    result = run_pipeline(
        bv_id=data.get("bv_id", "BV1Demo411111"),
        partition_name=data.get("partition", "knowledge"),
        up_ids=data.get("up_ids"),
        style=data.get("style", "干货"),
        seed_topic=data.get("topic"),
    )
    return jsonify({"success": True, "data": result})


@app.errorhandler(Exception)
def handle_error(exc):
    return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
