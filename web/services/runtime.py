from __future__ import annotations

from web.core.shared import *


# 判断是否已保存有效的运行时 LLM 配置（API Key 非空）。
def has_saved_runtime_llm_config() -> bool:
    return bool((RUNTIME_LLM_CONFIG or {}).get("api_key", "").strip())


# 返回当前时间的格式化字符串（YYYY-MM-DD HH:MM:SS）。
def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# 对知识库更新任务做快照，深拷贝并移除内部时间戳字段后返回。
def snapshot_knowledge_update_job(job: dict | None) -> dict | None:
    if not job:
        return None
    payload = deepcopy(job)
    payload.pop("updated_at_ts", None)
    return payload


# 清理已过期的知识库更新任务（在 KNOWLEDGE_UPDATE_JOB_LOCK 锁内调用）。
def cleanup_knowledge_update_jobs_locked() -> None:
    expires_before = time.time() - KNOWLEDGE_UPDATE_JOB_TTL_SECONDS
    expired_job_ids = [
        job_id
        for job_id, job in KNOWLEDGE_UPDATE_JOBS.items()
        if str(job.get("status") or "") not in {"queued", "running"}
        and float(job.get("updated_at_ts") or 0) < expires_before
    ]
    for job_id in expired_job_ids:
        KNOWLEDGE_UPDATE_JOBS.pop(job_id, None)


# 获取当前正在运行的知识库更新任务（在锁内调用，不做清理）。
def active_knowledge_update_job_locked() -> dict | None:
    if not KNOWLEDGE_UPDATE_ACTIVE_JOB_ID:
        return None
    job = KNOWLEDGE_UPDATE_JOBS.get(KNOWLEDGE_UPDATE_ACTIVE_JOB_ID)
    if not job:
        return None
    if str(job.get("status") or "") not in {"queued", "running"}:
        return None
    return job


# 获取当前活跃的知识库更新任务（含自动清理）。
def get_active_knowledge_update_job() -> dict | None:
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        cleanup_knowledge_update_jobs_locked()
        return snapshot_knowledge_update_job(active_knowledge_update_job_locked())


# 根据 job_id 获取指定的知识库更新任务（含自动清理）。
def get_knowledge_update_job(job_id: str) -> dict | None:
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        cleanup_knowledge_update_jobs_locked()
        return snapshot_knowledge_update_job(KNOWLEDGE_UPDATE_JOBS.get(job_id))


# 更新指定知识库更新任务的进度信息（状态、百分比、消息等）。
def update_knowledge_update_job(job_id: str, payload: dict) -> dict | None:
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        job = KNOWLEDGE_UPDATE_JOBS.get(job_id)
        if job is None:
            return None
        job.update(payload)
        job["id"] = job_id
        job["updated_at"] = now_text()
        job["updated_at_ts"] = time.time()
        if str(job.get("status") or "") in {"completed", "failed"} and not job.get("completed_at"):
            job["completed_at"] = job["updated_at"]
        return snapshot_knowledge_update_job(job)


# 清除当前活跃知识库更新任务（仅当 job_id 匹配时）。
def clear_active_knowledge_update_job(job_id: str) -> None:
    global KNOWLEDGE_UPDATE_ACTIVE_JOB_ID
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        if KNOWLEDGE_UPDATE_ACTIVE_JOB_ID == job_id:
            KNOWLEDGE_UPDATE_ACTIVE_JOB_ID = None


# 在后台线程中执行知识库更新任务，更新任务状态并处理锁和错误。
def run_knowledge_update_job(job_id: str, limit: int) -> None:
    acquired = KNOWLEDGE_UPDATE_EXECUTION_LOCK.acquire(blocking=False)
    if not acquired:
        clear_active_knowledge_update_job(job_id)
        update_knowledge_update_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "percent": 0.0,
                "message": "已有热门知识库更新任务正在执行，请稍后重试",
                "error": "已有热门知识库更新任务正在执行，请稍后重试",
            },
        )
        return

    try:
        update_knowledge_update_job(
            job_id,
            {
                "status": "running",
                "stage": "prepare",
                "percent": 0.0,
                "message": "正在准备热门知识库更新任务",
                "started_at": now_text(),
            },
        )

        def progress_callback(progress: dict) -> None:
            update_knowledge_update_job(job_id, progress)

        result = update_chroma_knowledge_base(per_board_limit=limit, progress_callback=progress_callback)
        from web.services.llm import build_knowledge_base_status

        clear_active_knowledge_update_job(job_id)
        update_knowledge_update_job(
            job_id,
            {
                "status": "completed",
                "stage": "completed",
                "percent": 100.0,
                "message": "热门知识库更新完成",
                "result": result,
                "knowledge_status": build_knowledge_base_status(),
            },
        )
    except Exception as exc:
        clear_active_knowledge_update_job(job_id)
        job = get_knowledge_update_job(job_id) or {}
        update_knowledge_update_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "percent": float(job.get("percent") or 0),
                "message": f"知识库更新失败：{exc}",
                "error": str(exc),
            },
        )
    finally:
        KNOWLEDGE_UPDATE_EXECUTION_LOCK.release()


# 创建并启动一个新的知识库更新任务，返回(任务快照, 是否已有任务运行, 错误信息)。
def start_knowledge_update_job(limit: int) -> tuple[dict | None, bool, str]:
    global KNOWLEDGE_UPDATE_ACTIVE_JOB_ID
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        cleanup_knowledge_update_jobs_locked()
        active_job = active_knowledge_update_job_locked()
        if active_job:
            return snapshot_knowledge_update_job(active_job), True, ""
        if KNOWLEDGE_UPDATE_EXECUTION_LOCK.locked():
            return None, False, "已有热门知识库更新任务正在执行，请稍后重试。"

        job_id = uuid4().hex
        created_at = now_text()
        KNOWLEDGE_UPDATE_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "percent": 0.0,
            "message": "更新任务已创建，等待执行",
            "limit": limit,
            "created_at": created_at,
            "started_at": "",
            "updated_at": created_at,
            "updated_at_ts": time.time(),
            "completed_at": "",
            "processed_items": 0,
            "total_items": 0,
            "processed_boards": 0,
            "total_boards": 0,
            "board_type": "",
            "current_title": "",
            "result": None,
            "knowledge_status": None,
            "error": "",
        }
        KNOWLEDGE_UPDATE_ACTIVE_JOB_ID = job_id
        snapshot = snapshot_knowledge_update_job(KNOWLEDGE_UPDATE_JOBS[job_id])

    threading.Thread(
        target=run_knowledge_update_job,
        args=(job_id, limit),
        daemon=True,
        name=f"knowledge-update-{job_id[:8]}",
    ).start()
    return snapshot, False, ""


# 对视频分析任务做快照，深拷贝并移除内部时间戳字段后返回。
def snapshot_module_analyze_job(job: dict | None) -> dict | None:
    if not job:
        return None
    payload = deepcopy(job)
    payload.pop("updated_at_ts", None)
    return payload


# 清理已过期的视频分析任务（在锁内调用）。
def cleanup_module_analyze_jobs_locked() -> None:
    expires_before = time.time() - MODULE_ANALYZE_JOB_TTL_SECONDS
    expired_job_ids = [
        job_id
        for job_id, job in MODULE_ANALYZE_JOBS.items()
        if str(job.get("status") or "") not in {"queued", "running"}
        and float(job.get("updated_at_ts") or 0) < expires_before
    ]
    for job_id in expired_job_ids:
        MODULE_ANALYZE_JOBS.pop(job_id, None)


# 根据 job_id 获取指定的视频分析任务（含自动清理）。
def get_module_analyze_job(job_id: str) -> dict | None:
    with MODULE_ANALYZE_JOB_LOCK:
        cleanup_module_analyze_jobs_locked()
        return snapshot_module_analyze_job(MODULE_ANALYZE_JOBS.get(job_id))


# 更新指定视频分析任务的进度信息（状态、结果、版本号等）。
def update_module_analyze_job(job_id: str, payload: dict) -> dict | None:
    with MODULE_ANALYZE_JOB_LOCK:
        job = MODULE_ANALYZE_JOBS.get(job_id)
        if job is None:
            return None
        job.update(payload)
        job["id"] = job_id
        job["version"] = safe_int(job.get("version")) + 1
        job["updated_at"] = now_text()
        job["updated_at_ts"] = time.time()
        if str(job.get("status") or "") in {"completed", "failed"} and not job.get("completed_at"):
            job["completed_at"] = job["updated_at"]
        return snapshot_module_analyze_job(job)


# 向视频分析任务的进度回调函数发送进度更新。
def emit_module_analyze_progress(
    progress_callback,
    *,
    stage: str,
    percent: float,
    message: str,
    **extra,
) -> None:
    if not callable(progress_callback):
        return
    payload = {
        "stage": stage,
        "percent": float(percent),
        "message": message,
    }
    payload.update(extra)
    progress_callback(payload)


# 构造 Server-Sent Events 格式的事件字符串，用于 SSE 流式推送。
def build_sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# 在后台线程中执行视频分析任务，更新任务状态并处理执行和错误。
def run_module_analyze_job(job_id: str, data: dict) -> None:
    from web.services.llm import execute_module_analyze_request

    try:
        update_module_analyze_job(
            job_id,
            {
                "status": "running",
                "stage": "prepare",
                "percent": 0.0,
                "message": "正在准备视频分析任务",
                "started_at": now_text(),
            },
        )

        def progress_callback(progress: dict) -> None:
            update_module_analyze_job(job_id, progress)

        result = execute_module_analyze_request(data, progress_callback=progress_callback)
        update_module_analyze_job(
            job_id,
            {
                "status": "completed",
                "stage": "completed",
                "percent": 100.0,
                "message": "视频分析已完成",
                "resolved": result.get("resolved"),
                "result": result,
                "error": "",
                "payload": {},
                "http_status": 200,
                "reference_sample_count": len(result.get("reference_videos") or []),
                "runtime_mode": result.get("runtime_mode") or runtime_mode(),
            },
        )
    except ModuleAnalyzeRequestError as exc:
        job = get_module_analyze_job(job_id) or {}
        update_module_analyze_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "percent": float(job.get("percent") or 0.0),
                "message": exc.message,
                "error": exc.message,
                "payload": exc.payload,
                "http_status": exc.status_code,
            },
        )
    except Exception as exc:
        job = get_module_analyze_job(job_id) or {}
        message = f"视频分析失败：{exc}"
        update_module_analyze_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "percent": float(job.get("percent") or 0.0),
                "message": message,
                "error": message,
                "payload": {},
                "http_status": 500,
            },
        )


# 创建并启动一个新的视频分析任务，返回任务元数据。
def start_module_analyze_job(data: dict) -> dict:
    with MODULE_ANALYZE_JOB_LOCK:
        cleanup_module_analyze_jobs_locked()
        job_id = uuid4().hex
        created_at = now_text()
        MODULE_ANALYZE_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "percent": 0.0,
            "message": "视频分析任务已创建，等待执行",
            "url": str((data or {}).get("url") or "").strip(),
            "created_at": created_at,
            "started_at": "",
            "updated_at": created_at,
            "updated_at_ts": time.time(),
            "completed_at": "",
            "version": 0,
            "resolved": None,
            "result": None,
            "error": "",
            "payload": {},
            "http_status": 200,
            "reference_sample_count": 0,
            "runtime_mode": runtime_mode(),
        }
        snapshot = snapshot_module_analyze_job(MODULE_ANALYZE_JOBS[job_id]) or {}

    threading.Thread(
        target=run_module_analyze_job,
        args=(job_id, dict(data or {})),
        daemon=True,
        name=f"module-analyze-{job_id[:8]}",
    ).start()
    return snapshot


# 判断当前开关状态下是否真正启用了 LLM Agent 模式。
def runtime_llm_enabled() -> bool:
    return bool(RUNTIME_LLM_ENABLED and has_saved_runtime_llm_config())


# 返回当前运行模式标识，优先看页面运行时开关而不是 .env 默认值。
def runtime_mode() -> str:
    return "llm_agent" if runtime_llm_enabled() else "rules"


# 返回当前处于启用状态的 LLM 配置，没有启用时返回空。
def get_active_runtime_llm_config() -> dict[str, str] | None:
    if not runtime_llm_enabled():
        return None
    return dict(RUNTIME_LLM_CONFIG or {})


# 返回当前保存的 LLM 配置，不论运行模式开关是否已开启。
def get_saved_runtime_llm_config() -> dict[str, str] | None:
    if not has_saved_runtime_llm_config():
        return None
    return dict(RUNTIME_LLM_CONFIG or {})


# 对 API Key 做脱敏，避免把完整密钥直接返回给前端。
def mask_api_key(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}{'*' * max(4, len(raw) - 8)}{raw[-4:]}"


# 根据当前激活的运行时配置构造 LLMClient 所需参数。
def build_runtime_llm_client_kwargs() -> dict:
    config = get_active_runtime_llm_config()
    if not config:
        return {}
    return {
        "provider": config.get("provider", "openai"),
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", ""),
        "model": config.get("model", "") or (CONFIG.llm_model or "gpt-5.4"),
        "timeout_seconds": CONFIG.llm_timeout_seconds,
        "max_retries": CONFIG.llm_max_retries,
        "retry_backoff_seconds": CONFIG.llm_retry_backoff_seconds,
    }


# 基于当前运行时配置创建一个 LLMClient 实例。
def build_runtime_llm_client() -> LLMClient:
    kwargs = build_runtime_llm_client_kwargs()
    return LLMClient(**kwargs) if kwargs else LLMClient(api_key="", base_url="", model=(CONFIG.llm_model or "gpt-5.4"))


# 构造一个明确禁用模型请求的 LLMClient，供规则模式里的纯规则分支使用。
def build_disabled_llm_client() -> LLMClient:
    return LLMClient(api_key="", base_url="", model=(CONFIG.llm_model or "gpt-5.4"))


# 构造规则模式专用的文案 Agent，确保不会因为 .env 里有 Key 就触发 LLM。
def build_rule_copy_agent() -> CopywritingAgent:
    return CopywritingAgent(llm_client=build_disabled_llm_client())


# 构造规则模式专用的优化 Agent，确保优化建议只走规则逻辑。
def build_rule_optimization_agent() -> OptimizationAgent:
    return OptimizationAgent(llm_client=build_disabled_llm_client())


# 校验并清洗前端提交的运行时 LLM 配置。
def sanitize_runtime_llm_config_payload(data: dict) -> dict[str, str]:
    base_url = str(data.get("base_url") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    provider = str(data.get("provider") or "").strip() or "openai"
    model = str(data.get("model") or "").strip() or (CONFIG.llm_model or "gpt-5.4")

    if not base_url or not api_key or not provider:
        raise ValueError("请完整填写 URL、Key 和模型供应商。")
    if not re.match(r"^https?://", base_url, flags=re.IGNORECASE):
        raise ValueError("URL 需要以 http:// 或 https:// 开头。")

    return {
        "provider": provider,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "source": "runtime",
    }


# 清空缓存的 LLM Agent，确保切模式或改配置后会按新参数重建。
def clear_llm_workspace_agent_cache() -> None:
    global LLM_WORKSPACE_AGENT, LLM_WORKSPACE_SIGNATURE, LLM_WORKSPACE_CHAT_AGENT, LLM_WORKSPACE_CHAT_SIGNATURE
    LLM_WORKSPACE_AGENT = None
    LLM_WORKSPACE_SIGNATURE = None
    LLM_WORKSPACE_CHAT_AGENT = None
    LLM_WORKSPACE_CHAT_SIGNATURE = None


# 保存新的运行时 LLM 配置，并立即切换到 LLM Agent 模式。
def save_runtime_llm_config(data: dict) -> dict[str, str]:
    global RUNTIME_LLM_CONFIG, RUNTIME_LLM_ENABLED
    config = sanitize_runtime_llm_config_payload(data)
    RUNTIME_LLM_CONFIG = config
    RUNTIME_LLM_ENABLED = True
    clear_llm_workspace_agent_cache()
    return dict(config)


# 根据开关状态切换当前运行模式，但保留已经填写过的 LLM 配置。
def set_runtime_llm_enabled(enabled: bool) -> None:
    global RUNTIME_LLM_ENABLED
    RUNTIME_LLM_ENABLED = bool(enabled)
    clear_llm_workspace_agent_cache()


# 把任意输入尽量安全地转换成整数，失败时返回 0。
def safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


# 把可选数值安全转成整数；空值返回 None，便于前端区分“未知”和“0”。
def safe_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None


# 把带“万/亿”等单位的展示数值转换成整数指标。
def safe_metric_int(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value or "").strip().lower()
    if not text:
        return 0

    multiplier = 1
    if text.endswith("万"):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100000000
        text = text[:-1]

    text = text.replace(",", "")
    try:
        return int(float(text) * multiplier)
    except Exception:
        return 0


# 从知识库文本（JSON字符串）中提取指定字段的值。
def extract_knowledge_text_field(text: object, field_name: str) -> str:
    raw = str(text or "")
    if not raw or not field_name:
        return ""

    field_pattern = re.escape(str(field_name))
    full_match = re.search(rf'"{field_pattern}"\s*:\s*"((?:\\.|[^"])*)"', raw)
    if full_match:
        try:
            return json.loads(f'"{full_match.group(1)}"').strip()
        except Exception:
            return full_match.group(1).replace('\\"', '"').replace("\\\\", "\\").strip()

    partial_match = re.search(rf'"{field_pattern}"\s*:\s*"([^\n]*)', raw)
    return partial_match.group(1).rstrip('", ').strip() if partial_match else ""


# 规范化知识库搜索类别输入，只返回在预定义规则中存在的类别，否则返回空。
def normalize_knowledge_search_category(value: object) -> str:
    clean = str(value or "").strip()
    return clean if clean in KNOWLEDGE_SEARCH_CATEGORY_RULES else ""


# 从知识库条目中推断其所属的大区分（game/tech/life/ent/knowledge）。
def infer_knowledge_item_broad_partition(item: dict) -> str:
    metadata = item.get("metadata") or {}
    board_type = str(metadata.get("board_type") or extract_knowledge_text_field(item.get("text"), "榜单来源") or "").strip().lower()
    if board_type.startswith("分区热门榜:"):
        return board_type.split(":", 1)[1].strip()

    partition = str(metadata.get("partition") or extract_knowledge_text_field(item.get("text"), "分区") or "").strip().lower()
    title = str(metadata.get("title") or extract_knowledge_text_field(item.get("text"), "视频标题") or "").strip().lower()
    combined = f"{partition} {title}"
    if any(token in combined for token in ["游戏", "电竞", "手游", "端游"]):
        return "game"
    if any(token in combined for token in ["科技", "数码", "手机", "电脑", "汽车", "ai", "软件"]):
        return "tech"
    if any(token in combined for token in ["生活", "日常", "vlog", "美食", "探店", "家居", "萌宠", "宠物", "体育", "健身", "运动"]):
        return "life"
    if any(token in combined for token in ["番剧", "国创", "纪录片", "电影", "电视剧", "综艺", "动画", "鬼畜", "翻唱", "音乐", "舞蹈", "宅舞", "街舞", "美妆", "穿搭", "娱乐"]):
        return "ent"
    if any(token in combined for token in ["知识", "科普", "学习", "职场", "考研", "面试", "心理", "婚恋", "情感"]):
        return "knowledge"
    return ""


# 判断知识库条目是否匹配指定类别（通过分区、关键词、大区等规则判断）。
def knowledge_item_matches_category(item: dict, category: str) -> bool:
    rule = KNOWLEDGE_SEARCH_CATEGORY_RULES.get(category) or {}
    if not rule or rule.get("match_all"):
        return True

    metadata = item.get("metadata") or {}
    board_type = str(metadata.get("board_type") or extract_knowledge_text_field(item.get("text"), "榜单来源") or "").strip().lower()
    partition = str(metadata.get("partition") or extract_knowledge_text_field(item.get("text"), "分区") or "").strip().lower()
    title = str(metadata.get("title") or extract_knowledge_text_field(item.get("text"), "视频标题") or "").strip().lower()
    combined = " ".join(filter(None, [board_type, partition, title]))
    broad_partition = infer_knowledge_item_broad_partition(item)
    allowed_broad = tuple(str(value).strip().lower() for value in rule.get("broad_partitions") or ())

    partitions = tuple(str(value).strip().lower() for value in rule.get("partitions") or ())
    if partition and any(token and token in partition for token in partitions):
        return True

    keywords = tuple(str(value).strip().lower() for value in rule.get("keywords") or ())
    if any(token and token in combined for token in keywords):
        return True

    if broad_partition and allowed_broad and broad_partition not in allowed_broad:
        return False

    return bool(rule.get("allow_broad_match") and broad_partition in allowed_broad)


# 获取知识库条目中的分块索引，用于排序（未找到时返回一个大值排到最后）。
def knowledge_chunk_index(item: dict) -> int:
    metadata = item.get("metadata") or {}
    value = metadata.get("chunk_index")
    return safe_int(value) if value is not None else 10**9


# 合并知识库检索结果中相同文档的不同分块，保留最高分和最早出现的分块内容。
def collapse_knowledge_matches(matches: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for index, item in enumerate(matches or []):
        metadata = dict((item or {}).get("metadata") or {})
        key = str((item or {}).get("id") or metadata.get("document_id") or f"knowledge_doc_{index}")
        candidate = {
            **dict(item or {}),
            "metadata": metadata,
            "_rank": index,
            "_chunk_index": knowledge_chunk_index({"metadata": metadata}),
        }
        existing = groups.get(key)
        if existing is None:
            groups[key] = candidate
            continue

        next_score = candidate.get("score")
        prev_score = existing.get("score")
        try:
            if next_score is not None and (prev_score is None or float(next_score) < float(prev_score)):
                existing["score"] = float(next_score)
        except Exception:
            pass

        if candidate["_chunk_index"] < existing["_chunk_index"]:
            existing["text"] = candidate.get("text", existing.get("text", ""))
            existing["metadata"] = metadata
            existing["_chunk_index"] = candidate["_chunk_index"]
            if candidate.get("id"):
                existing["id"] = candidate["id"]

    ordered = sorted(groups.values(), key=lambda item: item.get("_rank", 0))
    return [{key: value for key, value in item.items() if not key.startswith("_")} for item in ordered]


# 复用文案 Agent 的清洗逻辑来清理文本输出。
def clean_copy_text(value: object) -> str:
    return RAW_COPY_AGENT._clean_text(str(value or ""))


# 基于规则兜底文案构造一个适合接口直接返回的 payload。
def build_fallback_copy_payload(topic: str, style: str) -> dict:
    fallback = RAW_COPY_AGENT._fallback(topic, style)
    return {
        "topic": topic,
        "style": style,
        "titles": fallback.titles,
        "script": fallback.script,
        "description": fallback.description,
        "tags": fallback.tags,
        "pinned_comment": fallback.pinned_comment,
    }


# 统一清洗文案结果结构，确保前端拿到完整可用的字段。
def normalize_copy_result_payload(copy_result: object, topic: str, style: str) -> dict:
    clean_topic = clean_copy_text(topic) or "B站内容策划"
    clean_style = clean_copy_text(style) or "干货"
    fallback_result = RAW_COPY_AGENT._fallback(clean_topic, clean_style)
    fallback = {
        "topic": clean_topic,
        "style": clean_style,
        "titles": fallback_result.titles,
        "script": fallback_result.script,
        "description": fallback_result.description,
        "tags": fallback_result.tags,
        "pinned_comment": fallback_result.pinned_comment,
    }

    if not isinstance(copy_result, dict):
        return fallback

    titles = RAW_COPY_AGENT._normalize_titles(copy_result.get("titles"), fallback["titles"])
    script = RAW_COPY_AGENT._pick_script(copy_result, fallback_result)

    tags_raw = copy_result.get("tags")
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for item in tags_raw:
            clean = clean_copy_text(item)
            if len(clean) < 2 or clean in tags:
                continue
            tags.append(clean)
    if not tags:
        tags = fallback["tags"]

    return {
        "topic": clean_copy_text(copy_result.get("topic", "")) or clean_topic,
        "style": clean_copy_text(copy_result.get("style", "")) or clean_style,
        "titles": titles,
        "script": script,
        "description": clean_copy_text(copy_result.get("description", "")) or fallback["description"],
        "tags": tags,
        "pinned_comment": clean_copy_text(copy_result.get("pinned_comment", "")) or fallback["pinned_comment"],
    }


# 解码 HTTP 响应体，兼容 B 站当前会返回的 gzip 压缩页面。

# 构造前端初始化运行时所需的全部配置信息（运行模式、LLM状态、会话存储指标等）。
def build_runtime_payload() -> dict:
    from web.services.session_memory import get_chat_session_memory_store

    mode = runtime_mode()
    llm_enabled = runtime_llm_enabled()
    saved_config = get_saved_runtime_llm_config() or {}
    config_source = saved_config.get("source", "")
    switch_checked = bool(RUNTIME_LLM_ENABLED)
    has_saved_config = bool(saved_config)
    return {
        "mode": mode,
        "mode_label": RUNTIME_MODE_LABELS.get(mode, mode),
        "llm_enabled": llm_enabled,
        "chat_available": llm_enabled,
        "switch_checked": switch_checked,
        "has_saved_llm_config": has_saved_config,
        "saved_config_source": config_source,
        "saved_provider": saved_config.get("provider", ""),
        "saved_model": saved_config.get("model", ""),
        "saved_base_url": saved_config.get("base_url", ""),
        "saved_api_key_masked": mask_api_key(saved_config.get("api_key", "")),
        "requires_config": False,
        "mode_title": "当前运行中：LLM Agent 模式" if llm_enabled else "当前运行中：无 Key 逻辑模式",
        "mode_description": "已切换到 LLM Agent 中枢，分析、决策和生成全部由大模型实时完成。"
        if llm_enabled
        else "当前运行在无 Key 逻辑模式，分析和生成走规则链路，不会消耗 token。",
        "token_policy": "会消耗 token，聊天助手已启用。" if llm_enabled else "不会消耗 token，聊天助手当前关闭。",
        "switch_hint": "关闭右侧开关即可立即切回无 Key 逻辑模式。"
        if llm_enabled
        else (
            "当前已保存 LLM 配置，打开右侧开关即可切回 LLM Agent 模式。"
            if has_saved_config
            else "当前还没有可用 LLM 配置，打开右侧开关后需要先填写 URL、Key 和模型供应商。"
        ),
        "session_memory_metrics": get_chat_session_memory_store().metrics_snapshot(),
    }


# 当 LLM 当前配置不可用时，构造一份前端可直接用来拉起重配表单的提示数据。
def build_llm_runtime_reconfigure_data(reason: str) -> dict:
    runtime_payload = build_runtime_payload()
    runtime_payload["requires_config"] = True
    return {
        "show_runtime_config": True,
        "reason": reason,
        "runtime_payload": runtime_payload,
    }


# 视频分析请求异常类，包含错误消息、HTTP状态码和额外负载信息。
class ModuleAnalyzeRequestError(Exception):
    def __init__(self, message: str, status_code: int = 400, payload: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = dict(payload or {})


# 把 VideoMetrics 或同结构对象展开成普通字典。
