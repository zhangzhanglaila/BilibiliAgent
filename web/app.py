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
    parts = [part.strip() for part in [field_name, direction, idea] if part and part.strip()]
    if not parts:
        return ""
    return " / ".join(parts)


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
    seed_topic = build_seed_topic(field_name, direction, idea)
    if not seed_topic:
        return jsonify({"success": False, "error": "请至少输入领域、方向、想法中的一项"}), 400

    partition_name = (data.get("partition") or "knowledge").strip() or "knowledge"
    style = (data.get("style") or "干货").strip() or "干货"

    topic_result = run_topic(
        partition_name=partition_name,
        up_ids=None,
        seed_topic=seed_topic,
    )
    chosen_topic = (topic_result.get("ideas") or [{}])[0].get("topic") or seed_topic
    copy_result = run_copy(topic=chosen_topic, style=style)

    return jsonify(
        {
            "success": True,
            "data": {
                "seed_topic": seed_topic,
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

    resolved = resolve_video_payload(url)
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
            },
        }
    )


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
