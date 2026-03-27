"""轻量级前端入口。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

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


def extract_bvid(url: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]+)", url or "")
    if not match:
        raise ValueError("未识别到有效 BV 号")
    return match.group(1)


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

    if tid in {36, 201, 208, 209, 229}:  # 知识/校园学习等近似映射
        return "knowledge"
    if tid in {95, 124, 122}:  # 科技/软件应用/野生技术协会
        return "tech"
    if tid in {4, 17, 65, 136, 172}:  # 游戏相关
        return "game"
    if tid in {160, 138, 21, 76}:  # 生活相关
        return "life"
    if tid in {5, 71, 137, 181}:  # 娱乐相关
        return "ent"
    return "knowledge"


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/resolve-bili-link")
def api_resolve_bili_link():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "请先输入 B 站视频链接"}), 400

    try:
        bvid = extract_bvid(url)
        target = video.Video(bvid=bvid)
        info = sync(target.get_info())
        owner = info.get("owner", {})
        mid = int(owner.get("mid") or 0)
        tid = int(info.get("tid") or 0)
        tname = info.get("tname", "")
        partition = map_partition(tname, tid)
        return jsonify(
            {
                "success": True,
                "data": {
                    "bv_id": bvid,
                    "mid": mid,
                    "partition": partition,
                    "title": info.get("title", ""),
                    "tname": tname,
                },
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "error": f"解析失败：{exc}"}), 400


@app.post("/api/topic")
def api_topic():
    data = request.get_json(silent=True) or {}
    result = run_topic(
        partition_name=data.get("partition", "knowledge"),
        up_ids=data.get("up_ids"),
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
    )
    return jsonify({"success": True, "data": result})


@app.errorhandler(Exception)
def handle_error(exc):
    return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
