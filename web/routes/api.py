from __future__ import annotations

import time
from importlib import import_module
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, stream_with_context

from web.core.shared import (
    CONFIG,
    KNOWLEDGE_BASE,
    SUPPORTED_KNOWLEDGE_UPLOAD_SUFFIXES,
    format_llm_error,
    ingest_uploaded_file,
    kb_sample,
    llm_error_http_status,
    run_copy,
    run_operate,
    run_optimize,
    run_pipeline,
    run_topic,
    to_plain_data,
    update_chroma_knowledge_base,
)
from web.services.content import (
    build_creator_topic_result,
    build_resolved_payload,
    build_seed_topic,
    extract_bvid,
    fetch_video_preview_info,
)
from web.services.session_memory import get_chat_session_metadata_store

from web.services.llm import (
    build_knowledge_base_status,
    execute_module_analyze_request,
    run_llm_chat,
    run_llm_module_analyze,
    run_llm_module_create,
)
from web.services.runtime import (
    ModuleAnalyzeRequestError,
    build_llm_runtime_reconfigure_data,
    build_rule_copy_agent,
    build_runtime_payload,
    get_knowledge_update_job,
    get_module_analyze_job,
    has_saved_runtime_llm_config,
    normalize_knowledge_search_category,
    runtime_llm_enabled,
    safe_int,
    save_runtime_llm_config,
    set_runtime_llm_enabled,
    start_knowledge_update_job,
    start_module_analyze_job,
    collapse_knowledge_matches,
    knowledge_item_matches_category,
)

# API 路由蓝本，用于 Flask 应用。
api_bp = Blueprint("api", __name__)


# 获取 web.app 模块的延迟加载。
def app_exports():
    return import_module("web.app")


# 获取运行时信息（分区、模式等）。
@api_bp.get("/api/runtime-info")
def api_runtime_info():
    return jsonify({"success": True, "data": build_runtime_payload()})


# 设置运行时 LLM 模式（启用/禁用）。
@api_bp.post("/api/runtime-mode")
def api_runtime_mode():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))

    if not enabled:
        set_runtime_llm_enabled(False)
        return jsonify({"success": True, "data": build_runtime_payload()})

    if not has_saved_runtime_llm_config():
        set_runtime_llm_enabled(False)
        payload = build_runtime_payload()
        payload["requires_config"] = True
        return jsonify({"success": True, "data": payload})

    set_runtime_llm_enabled(True)
    payload = build_runtime_payload()
    payload["requires_config"] = False
    return jsonify({"success": True, "data": payload})


# 保存运行时 LLM 配置（API Key、Base URL、Model 等）。
@api_bp.post("/api/runtime-llm-config")
def api_runtime_llm_config():
    data = request.get_json(silent=True) or {}
    try:
        save_runtime_llm_config(data)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    payload = build_runtime_payload()
    payload["requires_config"] = False
    return jsonify({"success": True, "data": payload})


# 获取知识库后端状态信息。
@api_bp.get("/api/knowledge/status")
def api_knowledge_status():
    return jsonify({"success": True, "data": build_knowledge_base_status()})


# 分页获取知识库中的文档列表。
@api_bp.get("/api/knowledge/sample")
def api_knowledge_sample():
    limit = max(1, min(safe_int(request.args.get("limit") or 10), 20))
    offset = max(0, safe_int(request.args.get("offset") or 0))
    try:
        result = kb_sample(limit=limit, offset=offset)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": f"读取知识库内容失败：{exc}"}), 500


# 搜索知识库中的相关文档。
@api_bp.get("/api/knowledge/search")
def api_knowledge_search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "请输入检索关键词。"}), 400
    limit = max(1, min(safe_int(request.args.get("limit") or 6), 12))
    try:
        category = normalize_knowledge_search_category(query)
        candidate_limit = max(limit, 48) if category else limit
        raw_result = KNOWLEDGE_BASE.retrieve(query, limit=candidate_limit)
        matches = raw_result.get("matches", [])
        if category:
            matches = [item for item in matches if knowledge_item_matches_category(item, category)]
        result = {
            "query": query,
            "category": category,
            "matches": collapse_knowledge_matches(matches)[:limit],
        }
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": f"检索知识库失败：{exc}"}), 500


# 上传文件到知识库（支持 txt/md/docx/pdf）。
@api_bp.post("/api/knowledge/upload")
def api_knowledge_upload():
    uploaded = request.files.get("file")
    if uploaded is None:
        return jsonify({"success": False, "error": "请先选择要上传的知识文件。"}), 400

    filename = Path(uploaded.filename or "").name
    if not filename:
        return jsonify({"success": False, "error": "文件名为空，无法导入知识库。"}), 400

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_KNOWLEDGE_UPLOAD_SUFFIXES:
        return jsonify({"success": False, "error": "仅支持 txt / md / docx / pdf 文件。"}), 400

    try:
        result = ingest_uploaded_file(
            filename,
            uploaded.read(),
            metadata={"source_channel": "web_upload"},
        )
        return jsonify(
            {
                "success": True,
                "data": {
                    "upload_result": result,
                    "knowledge_status": build_knowledge_base_status(),
                },
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "error": f"知识库导入失败：{exc}"}), 500


@api_bp.post("/api/knowledge/update")
def api_knowledge_update():
    data = request.get_json(silent=True) or {}
    limit = max(1, min(safe_int(data.get("limit") or 10), 20))
    from web.core.shared import KNOWLEDGE_UPDATE_EXECUTION_LOCK

    if not KNOWLEDGE_UPDATE_EXECUTION_LOCK.acquire(blocking=False):
        return jsonify({"success": False, "error": "已有热门知识库更新任务正在执行，请稍后重试。"}), 409
    try:
        result = update_chroma_knowledge_base(per_board_limit=limit)
        return jsonify(
            {
                "success": True,
                "data": {
                    "update_result": result,
                    "knowledge_status": build_knowledge_base_status(),
                },
            }
        )
    except Exception as exc:
        return jsonify({"success": False, "error": f"知识库更新失败：{exc}"}), 500
    finally:
        KNOWLEDGE_UPDATE_EXECUTION_LOCK.release()


@api_bp.post("/api/knowledge/update/start")
def api_knowledge_update_start():
    data = request.get_json(silent=True) or {}
    limit = max(1, min(safe_int(data.get("limit") or 10), 20))
    job, already_running, error = start_knowledge_update_job(limit)
    if error:
        return jsonify({"success": False, "error": error}), 409
    return jsonify(
        {
            "success": True,
            "data": {
                "job": job or {},
                "already_running": already_running,
            },
        }
    )


@api_bp.get("/api/knowledge/update/<job_id>")
def api_knowledge_update_job(job_id: str):
    job = get_knowledge_update_job(job_id.strip())
    if not job:
        return jsonify({"success": False, "error": "未找到对应的知识库更新任务。"}), 404
    return jsonify({"success": True, "data": job})


@api_bp.post("/api/resolve-bili-link")
def api_resolve_bili_link():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "请先输入 B 站视频链接"}), 400

    bvid = ""
    try:
        bvid = extract_bvid(url)
        info = fetch_video_preview_info(url, bvid)
        return jsonify({"success": True, "data": build_resolved_payload(info, bvid)})
    except Exception as exc:
        suffix = f"（BV={bvid}）" if bvid else ""
        return jsonify({"success": False, "error": f"链接解析失败{suffix}：{exc}"}), 400


@api_bp.post("/api/module-create")
def api_module_create():
    data = request.get_json(silent=True) or {}
    field_name = (data.get("field") or "").strip()
    direction = (data.get("direction") or "").strip()
    idea = (data.get("idea") or "").strip()
    if not field_name and not direction and not idea:
        return jsonify({"success": False, "error": "请至少输入领域、方向、想法中的一项"}), 400

    if runtime_llm_enabled():
        try:
            return jsonify({"success": True, "data": run_llm_module_create(data)})
        except Exception as exc:
            message = f"LLM Agent 生成失败：{format_llm_error(exc)}"
            return (
                jsonify(
                    {
                        "success": False,
                        "error": message,
                        "data": build_llm_runtime_reconfigure_data(message),
                    }
                ),
                llm_error_http_status(exc),
            )

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
    copy_result = to_plain_data(build_rule_copy_agent().run(topic=chosen_topic, style=style))

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


@api_bp.post("/api/module-analyze")
def api_module_analyze():
    data = request.get_json(silent=True) or {}
    try:
        result = execute_module_analyze_request(data)
        return jsonify({"success": True, "data": result})
    except ModuleAnalyzeRequestError as exc:
        return jsonify({"success": False, "error": exc.message, "data": exc.payload}), exc.status_code
    except Exception as exc:
        return jsonify({"success": False, "error": f"视频分析失败：{exc}"}), 500


@api_bp.post("/api/module-analyze/start")
def api_module_analyze_start():
    data = request.get_json(silent=True) or {}
    job = app_exports().start_module_analyze_job(data)
    return jsonify({"success": True, "data": {"job": job}})


@api_bp.get("/api/module-analyze/jobs/<job_id>")
def api_module_analyze_job(job_id: str):
    job = app_exports().get_module_analyze_job(job_id.strip())
    if not job:
        return jsonify({"success": False, "error": "未找到对应的视频分析任务。"}), 404
    return jsonify({"success": True, "data": job})


@api_bp.get("/api/module-analyze/jobs/<job_id>/events")
def api_module_analyze_job_events(job_id: str):
    from web.services.runtime import build_sse_event

    job_id = job_id.strip()
    if not app_exports().get_module_analyze_job(job_id):
        return jsonify({"success": False, "error": "未找到对应的视频分析任务。"}), 404

    @stream_with_context
    def generate():
        last_version = -1
        yield "retry: 1000\n\n"
        while True:
            job = app_exports().get_module_analyze_job(job_id)
            if not job:
                yield build_sse_event("error", {"error": "未找到对应的视频分析任务。"})
                break
            version = safe_int(job.get("version"))
            status = str(job.get("status") or "")
            if version != last_version:
                event_name = "done" if status in {"completed", "failed"} else "progress"
                yield build_sse_event(event_name, job)
                last_version = version
                if status in {"completed", "failed"}:
                    break
            else:
                yield ": keep-alive\n\n"
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@api_bp.post("/api/chat")
def api_chat():
    if not runtime_llm_enabled():
        return jsonify({"success": False, "error": "当前是无 Key 逻辑模式，请先开启 LLM Agent 模式后再使用智能对话助手。"}), 400

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": "请输入对话内容"}), 400

    try:
        result = run_llm_chat(data)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        message = f"智能对话失败：{format_llm_error(exc)}"
        return (
            jsonify(
                {
                    "success": False,
                    "error": message,
                    "data": build_llm_runtime_reconfigure_data(message),
                }
            ),
            llm_error_http_status(exc),
        )


@api_bp.get("/api/chat/sessions")
def api_chat_sessions_list():
    store = get_chat_session_metadata_store()
    sessions = store.list_sessions()
    return jsonify({"success": True, "data": {"sessions": sessions}})


@api_bp.get("/api/chat/sessions/<session_id>")
def api_chat_session_detail(session_id):
    store = get_chat_session_metadata_store()
    session_data = store.get_session(session_id)
    if not session_data:
        return jsonify({"success": False, "error": "会话不存在"}), 404
    return jsonify({"success": True, "data": session_data})


@api_bp.delete("/api/chat/sessions/<session_id>")
def api_chat_session_delete(session_id):
    store = get_chat_session_metadata_store()
    deleted = store.delete_session(session_id)
    if not deleted:
        return jsonify({"success": False, "error": "会话不存在或已删除"}), 404
    return jsonify({"success": True})


@api_bp.post("/api/topic")
def api_topic():
    data = request.get_json(silent=True) or {}
    result = run_topic(
        partition_name=data.get("partition", "knowledge"),
        up_ids=data.get("up_ids"),
        seed_topic=data.get("topic"),
    )
    return jsonify({"success": True, "data": result})


@api_bp.post("/api/copy")
def api_copy():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "B站内容提效")
    style = data.get("style", "干货")
    result = run_copy(topic=topic, style=style)
    return jsonify({"success": True, "data": result})


@api_bp.post("/api/operate")
def api_operate():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    dry_run = bool(data.get("dry_run", True))
    result = run_operate(bv_id=bv_id, dry_run=dry_run)
    return jsonify({"success": True, "data": result})


@api_bp.post("/api/optimize")
def api_optimize():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    result = run_optimize(bv_id=bv_id)
    return jsonify({"success": True, "data": result})


@api_bp.post("/api/pipeline")
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
