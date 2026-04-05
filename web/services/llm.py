from __future__ import annotations

from importlib import import_module

from web.services.reference import *


def app_exports():
    return import_module("web.app")

def build_llm_video_payload(info: dict, bvid: str, url: str) -> dict:
    resolved = build_resolved_payload(info, bvid)
    return build_llm_video_payload_from_resolved(resolved, url)


# 直接把 resolved 重排成更适合放进 LLM 提示词的视频结构。
def build_llm_video_payload_from_resolved(resolved: dict, url: str) -> dict:
    benchmark_profile = build_video_benchmark_profile(resolved)
    retrieval_partition = str(benchmark_profile.get("effective_partition") or resolved.get("partition") or "").strip()
    retrieval_partition_label = str(benchmark_profile.get("effective_partition_label") or "").strip() or PARTITION_LABELS.get(
        retrieval_partition, retrieval_partition
    )

    return {
        "bv_id": resolved.get("bv_id", ""),
        "url": url.strip(),
        "title": resolved.get("title", ""),
        "keywords": extract_video_keywords(resolved.get("keywords")),
        "topic": resolved.get("topic", ""),
        "style": resolved.get("style", ""),
        "up_name": resolved.get("up_name", ""),
        "mid": safe_int(resolved.get("mid")),
        "up_ids": list(resolved.get("up_ids") or []),
        "tid": safe_int(resolved.get("tid")),
        "tname": resolved.get("tname", ""),
        "duration": safe_int(resolved.get("duration")),
        "stats": dict(resolved.get("stats") or {}),
        "retrieval_partition": retrieval_partition,
        "retrieval_partition_label": retrieval_partition_label,
        "benchmark_lane_label": benchmark_profile.get("lane_label", ""),
        "benchmark_terms": list(benchmark_profile.get("terms") or []),
        "benchmark_queries": list(benchmark_profile.get("queries") or []),
    }


# 为内容创作模块构造一份供 LLM 使用的完整简报。
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


# 把创作简报压缩成更适合放入提示词的轻量结构。
def compact_creator_briefing_for_llm(briefing: dict) -> dict:
    return {
        "user_input": briefing.get("user_input", {}),
        "market_snapshot": compact_market_snapshot_for_llm(briefing.get("market_snapshot") or {}),
    }


# 根据视频链接构造一份供 LLM 使用的视频分析简报。
def build_video_briefing(url: str) -> dict:
    bvid = extract_bvid(url)
    info = fetch_video_info(url, bvid)
    resolved = build_resolved_payload(info, bvid)
    video_payload = build_llm_video_payload_from_resolved(resolved, url)
    market_snapshot = build_hot_peer_market_snapshot(resolved)
    return {
        "video": video_payload,
        "market_snapshot": market_snapshot,
    }


# 在进入视频分析 Agent 前，把当前视频与同方向爆款样本一次性压成稳定上下文。
def build_video_analyze_preloaded_context(resolved: dict, url: str, market_snapshot: dict) -> dict:
    return {
        "video": build_llm_video_payload_from_resolved(resolved, url),
        "market_snapshot": compact_market_snapshot_for_llm(market_snapshot),
    }


# 构造指定分区的热点看板快照，供聊天和分析工具复用。
def build_hot_board_snapshot(partition_name: str) -> dict:
    market_snapshot = build_market_snapshot(partition_name)
    return {
        "partition": market_snapshot.get("partition"),
        "partition_label": market_snapshot.get("partition_label"),
        "hot_board": market_snapshot.get("hot_board", []),
        "partition_samples": market_snapshot.get("partition_samples", []),
    }


# 从任意文本里抓取第一条 B 站相关 URL。
def extract_first_bili_url(text: str) -> str:
    match = re.search(r"https?://[^\s]+", text or "", flags=re.IGNORECASE)
    return match.group(0).strip() if match else ""


# 把工具返回的市场数据沉淀进本地知识库，供后续 RAG 检索复用。
def save_tool_result_to_knowledge_base(source_id: str, text: str, metadata: dict | None = None) -> None:
    clean_id = normalize_creator_text(source_id) or "workspace"
    clean_text = str(text or "").strip()
    if not clean_text:
        return
    try:
        RUNTIME_TOOL_KNOWLEDGE_BASE.add_document(
            Document(
                id=re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", clean_id)[:80] or "workspace",
                text=clean_text,
                metadata=metadata or {},
            )
        )
    except Exception:
        return


def build_knowledge_base_status() -> dict:
    status = KNOWLEDGE_BASE.backend_status()
    status["vector_db_path"] = CONFIG.vector_db_path
    status["supported_upload_types"] = sorted(SUPPORTED_KNOWLEDGE_UPLOAD_SUFFIXES)
    memory_store = get_long_term_memory()
    status["memory_backend"] = getattr(memory_store, "backend", "disabled")
    status["memory_collection"] = getattr(memory_store, "collection_name", "user_long_term_memory")
    status["active_update_job"] = get_active_knowledge_update_job()
    return status


@traceable(run_type="tool", name="web.creator_briefing_tool_handler", tags=["tool", "creator_briefing", "rag"])
def creator_briefing_tool_handler(payload: dict) -> dict:
    result = build_creator_briefing(
        payload.get("field", ""),
        payload.get("direction", ""),
        payload.get("idea", ""),
        payload.get("partition", "knowledge"),
    )
    save_tool_result_to_knowledge_base(
        f"creator_{payload.get('field', '')}_{payload.get('direction', '')}_{payload.get('partition', '')}",
        json.dumps(result, ensure_ascii=False),
        {
            "source": "creator_briefing",
            "partition": payload.get("partition", "knowledge"),
        },
    )
    return result


@traceable(run_type="tool", name="web.video_briefing_tool_handler", tags=["tool", "video_briefing", "rag"])
def video_briefing_tool_handler(payload: dict) -> dict:
    result = build_video_briefing(payload.get("url", ""))
    save_tool_result_to_knowledge_base(
        f"video_{((result.get('video') or {}).get('bv_id') or payload.get('url', ''))}",
        json.dumps(result, ensure_ascii=False),
        {
            "source": "video_briefing",
            "partition": (result.get("video") or {}).get("retrieval_partition", ""),
        },
    )
    return result


@traceable(run_type="tool", name="web.hot_board_snapshot_tool_handler", tags=["tool", "hot_board_snapshot", "rag"])
def hot_board_snapshot_tool_handler(payload: dict) -> dict:
    result = build_hot_board_snapshot(payload.get("partition", "knowledge"))
    save_tool_result_to_knowledge_base(
        f"hot_{payload.get('partition', 'knowledge')}",
        json.dumps(result, ensure_ascii=False),
        {
            "source": "hot_board_snapshot",
            "partition": payload.get("partition", "knowledge"),
        },
    )
    return result


def allowed_tools_for_scene(scene_name: str) -> list[str]:
    return list(LLM_SCENE_ALLOWED_TOOLS.get(scene_name, LLM_SCENE_ALLOWED_TOOLS["workspace_chat"]))


def should_preload_creator_briefing(data: dict) -> bool:
    query_text = " ".join(
        str(data.get(key) or "").strip()
        for key in ["field", "direction", "idea", "partition"]
        if str(data.get(key) or "").strip()
    )
    if not query_text:
        return False
    return any(keyword in query_text for keyword in CREATOR_BRIEFING_TRIGGER_KEYWORDS)


def load_creator_preprocessed_context(data: dict) -> dict:
    if not should_preload_creator_briefing(data):
        return {}
    try:
        briefing = creator_briefing_tool_handler(
            {
                "field": (data.get("field") or "").strip(),
                "direction": (data.get("direction") or "").strip(),
                "idea": (data.get("idea") or "").strip(),
                "partition": (data.get("partition") or "knowledge").strip() or "knowledge",
            }
        )
    except Exception:
        return {}
    return {"creator_briefing": compact_creator_briefing_for_llm(briefing)}


# 懒加载并返回全局 LLMWorkspaceAgent 实例。
def get_llm_workspace_agent() -> LLMWorkspaceAgent:
    global LLM_WORKSPACE_AGENT, LLM_WORKSPACE_SIGNATURE
    active_config = get_active_runtime_llm_config()
    if not active_config:
        raise RuntimeError("当前未开启 LLM Agent 模式，或还没有可用的 LLM 配置。")

    signature = (
        active_config.get("provider", ""),
        active_config.get("base_url", ""),
        active_config.get("api_key", ""),
        active_config.get("model", ""),
    )
    if LLM_WORKSPACE_AGENT is None or LLM_WORKSPACE_SIGNATURE != signature:
        LLM_WORKSPACE_AGENT = LLMWorkspaceAgent(
            llm_client=build_runtime_llm_client(),
            memory_store=get_long_term_memory(),
            tools=[
                AgentTool(
                    name="video_briefing",
                    description="解析 B 站视频链接，返回视频公开数据，并补同方向爆款对标样本。输入: {url}",
                    handler=video_briefing_tool_handler,
                ),
                AgentTool(
                    name="hot_board_snapshot",
                    description="获取指定分区的热点榜和分区样本原始数据，适合回答趋势、热点、近期什么内容火。输入: {partition}",
                    handler=hot_board_snapshot_tool_handler,
                ),
                RetrievalTool(),
                AgentTool(
                    name="web_search",
                    description="实时搜索热点、平台活动、竞品趋势和外部公开信息。输入: {query, limit}",
                    handler=lambda payload: WEB_SEARCH.search(payload.get("query", ""), int(payload.get("limit") or 5)),
                ),
            ],
        )
        LLM_WORKSPACE_SIGNATURE = signature
    return LLM_WORKSPACE_AGENT

def video_analyze_retrieval_tool_handler(payload: dict) -> dict:
    query = str(payload.get("query") or "").strip()
    limit = max(1, min(safe_int(payload.get("limit") or 4), 8))
    result = KNOWLEDGE_BASE.retrieve(
        query,
        limit=limit,
        metadata_filter=dict(VIDEO_ANALYZE_RETRIEVAL_FILTER),
    )
    matches = []
    for item in result.get("matches", []):
        metadata = dict((item or {}).get("metadata") or {})
        source = str(metadata.get("source") or "")
        original_source = str(metadata.get("original_source") or "")
        if source in VIDEO_ANALYZE_DIRTY_SOURCES or original_source in VIDEO_ANALYZE_DIRTY_SOURCES:
            continue
        matches.append(item)
    return {
        "query": result.get("query", query),
        "matches": matches,
        "match_count": len(matches),
        "metadata_filter": dict(VIDEO_ANALYZE_RETRIEVAL_FILTER),
    }


def video_analyze_action_validator(
    action: str,
    action_input: dict,
    scratchpad: list[dict],
    used_tools: list[str],
) -> str:
    if action == "hot_board_snapshot":
        return "视频分析模块已禁用 hot_board_snapshot。"
    if action == "video_briefing":
        return "当前视频分析链路已在进入 Agent 前完成视频预解析和对标样本预加载，不允许再调用 video_briefing。"
    if action == "web_search":
        latest_retrieval = next(
            (
                item
                for item in reversed(scratchpad)
                if isinstance(item, dict) and item.get("action") == "retrieval"
            ),
            None,
        )
        if latest_retrieval is None:
            return "必须先完成 retrieval，再决定是否联网搜索。"
        observation = latest_retrieval.get("observation") if isinstance(latest_retrieval, dict) else {}
        match_count = safe_int((observation or {}).get("match_count"))
        if match_count >= 3:
            return "retrieval 已返回足够同赛道样本，当前不允许再调用 web_search。"
    return ""


def get_video_analyze_agent() -> LLMWorkspaceAgent:
    global LLM_VIDEO_ANALYZE_AGENT, LLM_VIDEO_ANALYZE_SIGNATURE
    active_config = get_active_runtime_llm_config()
    if not active_config:
        raise RuntimeError("当前未开启 LLM Agent 模式，或还没有可用的 LLM 配置。")

    signature = (
        active_config.get("provider", ""),
        active_config.get("base_url", ""),
        active_config.get("api_key", ""),
        active_config.get("model", ""),
    )
    if LLM_VIDEO_ANALYZE_AGENT is None or LLM_VIDEO_ANALYZE_SIGNATURE != signature:
        LLM_VIDEO_ANALYZE_AGENT = LLMWorkspaceAgent(
            llm_client=build_runtime_llm_client(),
            enable_memory=False,
            tools=[
                AgentTool(
                    name="retrieval",
                    description="从 bilibili_knowledge 中检索同垂类静态爆款样本，自动过滤历史工具回流数据。输入: {query, limit}",
                    handler=video_analyze_retrieval_tool_handler,
                ),
                AgentTool(
                    name="web_search",
                    description="当 retrieval 样本不足时联网搜索最新赛道爆款规则与案例。输入: {query, limit}",
                    handler=lambda payload: WEB_SEARCH.search(payload.get("query", ""), int(payload.get("limit") or 5)),
                ),
            ],
        )
        LLM_VIDEO_ANALYZE_SIGNATURE = signature
    return LLM_VIDEO_ANALYZE_AGENT


def finalize_module_analyze_result(result: dict, resolved: dict, market_snapshot: dict) -> dict:
    payload = dict(result or {})
    payload["resolved"] = resolved
    performance = normalize_module_performance_payload(payload.get("performance"), resolved)
    payload["performance"] = performance
    topic_result = payload.get("topic_result") if isinstance(payload.get("topic_result"), dict) else {"ideas": []}
    payload["topic_result"] = topic_result
    reference_query = build_reference_query_text(resolved)
    reference_videos = app_exports().build_module_analyze_reference_videos(
        market_snapshot,
        payload.get("tool_observations") if isinstance(payload.get("tool_observations"), list) else [],
        exclude_bvid=resolved.get("bv_id", ""),
        query_text=reference_query,
        resolved=resolved,
    )
    optimize_result_raw = payload.get("optimize_result")
    optimize_result = normalize_object_payload(optimize_result_raw)
    if not optimize_result:
        optimize_texts = normalize_text_list(optimize_result_raw, limit=5)
        if optimize_texts:
            optimize_result = {
                "diagnosis": optimize_texts[0],
                "content_suggestions": optimize_texts[1:],
            }
    analysis = normalize_module_analysis_payload(
        payload,
        resolved=resolved,
        performance=performance,
        topic_result=topic_result,
        optimize_result=optimize_result,
        reference_videos=reference_videos,
    )
    optimize_result["diagnosis"] = normalize_text_value(optimize_result.get("diagnosis")) or normalize_text_value(
        performance.get("summary")
    )
    optimize_result["optimized_titles"] = merge_text_lists(
        optimize_result.get("optimized_titles"),
        analysis.get("title_suggestions"),
        limit=2,
    )
    optimize_result["cover_suggestion"] = normalize_text_value(optimize_result.get("cover_suggestion")) or normalize_text_value(
        analysis.get("cover_suggestion")
    )
    optimize_result["content_suggestions"] = merge_text_lists(
        optimize_result.get("content_suggestions"),
        analysis.get("content_suggestions"),
        limit=5,
    )
    payload["optimize_result"] = optimize_result
    payload["analysis"] = analysis
    copy_result_payload = payload.get("copy_result") if isinstance(payload.get("copy_result"), dict) else {}
    copy_topic = (
        clean_copy_text(copy_result_payload.get("topic", ""))
        or clean_copy_text(((topic_result.get("ideas") or [{}])[0]).get("topic", ""))
        or resolved.get("topic")
        or resolved.get("title")
        or "视频优化"
    )
    if performance.get("is_hot"):
        payload["copy_result"] = None
    else:
        payload["copy_result"] = normalize_copy_result_payload(
            payload.get("copy_result"),
            copy_topic,
            resolved.get("style", "干货"),
        )
    payload["reference_videos"] = reference_videos
    payload["reference_videos_notice"] = build_reference_videos_notice(reference_videos, market_snapshot)
    payload.setdefault("runtime_mode", "llm_agent")
    return payload


# 在 LLM Agent 模式下执行内容创作模块的完整生成流程。
@traceable(run_type="chain", name="web.run_llm_module_create", tags=["web", "llm", "rag", "module_create"])
def run_llm_module_create(data: dict) -> dict:
    agent = get_llm_workspace_agent()
    default_style = (data.get("style") or "干货").strip() or "干货"
    preloaded_context = load_creator_preprocessed_context(data)
    response_contract = (
        "返回一个 JSON 对象，字段必须包含：\n"
        "- normalized_profile: 字符串，整理后的创作方向\n"
        "- seed_topic: 字符串，当前要解决的核心问题\n"
        "- partition: 字符串，分区名\n"
        "- style: 字符串，文案风格\n"
        "- chosen_topic: 字符串，最终主选题\n"
        "- topic_result: 对象，至少包含 ideas(长度 3 的数组)，每项包含 topic, reason, video_type, keywords；topic 必须是具体的新方向，不要提问句，不要把原题后面机械接“哪种切口/哪种表达/下一条拍什么”\n"
        "- copy_result: 对象，包含 topic, style, titles(3个), script(至少4段，含 section/duration/content), description, tags, pinned_comment\n"
    )
    try:
        result = agent.run_structured(
        task_name="module_create",
        task_goal="基于用户输入、按需预加载的创作简报和工具 observation，为创作者输出更容易起量的 3 个选题，并生成完整可发布文案。",
        user_payload={
            "field": (data.get("field") or "").strip(),
            "direction": (data.get("direction") or "").strip(),
            "idea": (data.get("idea") or "").strip(),
            "partition": (data.get("partition") or "knowledge").strip() or "knowledge",
            "style": (data.get("style") or "干货").strip() or "干货",
            "preloaded_context": preloaded_context,
            "memory_user_id": "web_module_create",
        },
        response_contract=response_contract,
        allowed_tools=allowed_tools_for_scene("module_create"),
        required_final_keys=["normalized_profile", "seed_topic", "partition", "style", "chosen_topic", "topic_result", "copy_result"],
        )
        copy_topic = (
            clean_copy_text(result.get("chosen_topic", ""))
            or clean_copy_text(result.get("seed_topic", ""))
            or build_seed_topic(
                (data.get("field") or "").strip(),
                (data.get("direction") or "").strip(),
                (data.get("idea") or "").strip(),
            )
        )
        result["copy_result"] = normalize_copy_result_payload(
            result.get("copy_result"),
            copy_topic,
            clean_copy_text(result.get("style", "")) or default_style,
        )
        return result
    except Exception as exc:
        if should_skip_same_provider_fallback(exc):
            raise RuntimeError(
                f"LLM 服务当前不可用：{format_llm_error(exc)} 当前不会继续尝试同 provider 的 fallback，请稍后重试。"
            ) from exc
        try:
            fallback_result = run_llm_module_create_fallback(data)
            fallback_result["llm_warning"] = f"Agent 中枢生成失败，已切换到单次 LLM 回退：{format_llm_error(exc)}"
            return fallback_result
        except Exception as fallback_exc:
            raise RuntimeError(
                f"LLM Agent 生成失败：{format_llm_error(exc)}；LLM fallback 也失败：{format_llm_error(fallback_exc)}"
            ) from fallback_exc


# 当 Agent 中枢不可用时，直接用单次 LLM 调用回退生成创作结果。
@traceable(run_type="chain", name="web.run_llm_module_create_fallback", tags=["web", "llm", "fallback", "module_create"])
def run_llm_module_create_fallback(data: dict) -> dict:
    llm = build_runtime_llm_client()
    llm.require_available()

    field_name = (data.get("field") or "").strip()
    direction = (data.get("direction") or "").strip()
    idea = (data.get("idea") or "").strip()
    partition_name = (data.get("partition") or "knowledge").strip() or "knowledge"
    style = (data.get("style") or "干货").strip() or "干货"
    briefing = compact_creator_briefing_for_llm(build_creator_briefing(field_name, direction, idea, partition_name))

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
        "3. topic_result.ideas must contain 3 items, each with topic, reason, video_type, keywords, and each topic must be a concrete new direction instead of a question template.\n"
        "4. copy_result must include topic, style, titles(3), script(at least 4 sections with section/duration/content), description, tags, pinned_comment.\n"
        "5. copy_result.titles must be narrative, statement-style Bilibili titles with a natural vlog / daily-record feeling when the topic fits; no question titles, no teaching tone.\n"
        "6. Avoid repetitive phrases like a universal '高效做法' template unless the topic really demands it."
    )
    result = llm.invoke_json_required(system_prompt, user_prompt)
    if not isinstance(result, dict):
        raise ValueError("LLM module create fallback returned invalid format")
    copy_topic = (
        clean_copy_text(result.get("chosen_topic", ""))
        or clean_copy_text(result.get("seed_topic", ""))
        or build_seed_topic(field_name, direction, idea)
    )
    result["copy_result"] = normalize_copy_result_payload(result.get("copy_result"), copy_topic, style)
    result.setdefault("runtime_mode", "llm_agent")
    result.setdefault("agent_trace", ["creator_briefing", "llm_direct_fallback"])
    return result


# 在 LLM Agent 模式下执行视频分析模块的完整分析流程。
# 当分析 Agent 中枢不可用时，直接用单次 LLM 调用回退生成分析结果。
@traceable(run_type="chain", name="web.run_llm_module_analyze_fallback", tags=["web", "llm", "fallback", "module_analyze"])
def run_llm_module_analyze_fallback(
    data: dict,
    resolved: dict,
    market_snapshot: dict,
    market_snapshot_future=None,
    progress_callback=None,
) -> dict:
    exports = app_exports()
    if market_snapshot_future:
        market_snapshot, prefetch_state = get_prefetched_market_snapshot(market_snapshot_future, resolved, None)
        sample_count = safe_int(market_snapshot.get("source_count"))
        if sample_count > 0:
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_ready",
                percent=82.0,
                message=f"已获取 {sample_count} 个对标爆款视频",
                reference_sample_count=sample_count,
            )
        elif prefetch_state == "error":
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_failed",
                percent=82.0,
                message="对标样本加载失败，正在整理回退分析结果",
                reference_sample_count=0,
            )
    llm = build_runtime_llm_client()
    llm.require_available()
    baseline_performance = exports.classify_video_performance(resolved)
    system_prompt = (
        "你是 B 站视频分析助手。"
        "当前已经拿到后端解析出的真实视频信息，以及代码预加载的同方向爆款对标样本。"
        "请直接完成爆款/低表现判断、原因拆解、优化建议和后续选题。"
        "不要输出解释性废话，只返回 JSON。"
    )
    user_prompt = (
        "请根据下面的数据直接输出 JSON，对象字段必须包含："
        "resolved, performance, topic_result, optimize_result, copy_result, analysis。\n\n"
        f"当前视频真实信息：{json.dumps(resolved, ensure_ascii=False)}\n\n"
        f"规则基线判断：{json.dumps(baseline_performance, ensure_ascii=False)}\n\n"
        f"市场样本：{json.dumps(market_snapshot, ensure_ascii=False)}\n\n"
        "要求：\n"
        "1. resolved 直接复用当前视频真实信息，不要改 BV、标题、播放等字段。\n"
        "2. performance 必须包含 label, is_hot, score, reasons, summary。\n"
        "2.1 如果规则基线已经明确判定为爆款，除非你能给出更强的同赛道反证，否则不要改判成低表现。\n"
        "3. topic_result.ideas 输出 3 个后续选题，每项包含 topic, reason, video_type, keywords；topic 必须是新的具体方向，不要提问句。\n"
        "4. optimize_result 输出 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions。\n"
        "5. 如果你判断 is_hot=true，则 copy_result 返回 null，analysis 重点输出 analysis_points 和 followup_topics。\n"
        "6. 如果你判断 is_hot=false，则 copy_result 必须输出一版新文案，analysis 重点输出 analysis_points, next_topics, title_suggestions, cover_suggestion, content_suggestions。\n"
        "7. copy_result.titles 必须是陈述型、叙事型、生活化标题，不要提问句，不要教学口吻，不要出现“为什么 / 怎么 / 哪种 / 更容易起量 / 更容易进推荐”这类模板。\n"
        "8. 如果当前标题属于异地恋 / 情侣约会 / 520 日常 vlog，copy_result.script 必须写成可直接对镜口播的生活化脚本，严格保留 0-8s 开头钩子、8-28s 核心画面1、28-56s 核心画面2、56-75s 结尾互动；内容必须贴合酒店、早午餐、逛街拍照、小清吧、异地恋见面这些场景，禁止出现切口、测反馈、完播、方向跑偏、实战拆解等运营词。\n"
        "9. analysis 里的 followup_topics / next_topics 也必须是具体新方向，不要把原视频标题后面机械加问题后缀。\n"
        "10. analysis 必须额外包含：benchmark_analysis, remake_script_structure, advanced_title_sets, cover_plan, tag_strategy, publish_strategy, reusable_hit_points。\n"
        "11. benchmark_analysis 要基于同赛道高表现样本，总结 common_title_formulas, common_rhythm_formulas, common_structure_formulas。\n"
        "12. advanced_title_sets 里要输出 short_titles / long_titles / conflict_titles，每组 3 个。\n"
        "13. cover_plan 要输出 copy_lines, layout_advice, color_scheme, highlight_elements。\n"
        "14. publish_strategy 要输出 best_publish_windows, should_ask_for_coin, coin_call_to_action, suggested_comment_guides。"
    )
    result = llm.invoke_json_required(system_prompt, user_prompt)
    if not isinstance(result, dict):
        raise ValueError("LLM fallback 返回格式无效")
    result.setdefault("agent_trace", ["llm_direct_fallback"])
    return exports.finalize_module_analyze_result(result, resolved, market_snapshot)


# 运行视频分析模块，让 LLM Agent 按既定工具链完成单次独立分析。
@traceable(run_type="chain", name="web.run_llm_module_analyze", tags=["web", "llm", "rag", "module_analyze"])
def run_llm_module_analyze(
    data: dict,
    resolved: dict,
    market_snapshot: dict,
    market_snapshot_future=None,
    progress_callback=None,
) -> dict:
    exports = app_exports()
    agent = exports.get_video_analyze_agent()
    url = (data.get("url") or "").strip()
    preloaded_context = build_video_analyze_preloaded_context(resolved, url, market_snapshot)
    try:
        result = agent.run_structured(
            task_name="module_analyze",
            task_goal=VIDEO_ANALYZE_TASK_GOAL,
            user_payload={
                "url": url,
                "parsed_video": resolved,
                "preloaded_context": preloaded_context,
            },
            response_contract=VIDEO_ANALYZE_RESPONSE_CONTRACT,
            allowed_tools=allowed_tools_for_scene("module_analyze"),
            required_tools=VIDEO_ANALYZE_REQUIRED_TOOLS,
            required_final_keys=VIDEO_ANALYZE_REQUIRED_FINAL_KEYS,
            load_history=False,
            save_memory=False,
            enable_reflection=False,
            system_prompt_override=VIDEO_ANALYZE_SYSTEM_PROMPT,
            strict_required_tool_order=True,
            action_validator=video_analyze_action_validator,
        )
        if market_snapshot_future:
            market_snapshot, prefetch_state = get_prefetched_market_snapshot(market_snapshot_future, resolved, None)
            sample_count = safe_int(market_snapshot.get("source_count"))
            if sample_count > 0:
                emit_module_analyze_progress(
                    progress_callback,
                    stage="reference_videos_ready",
                    percent=82.0,
                    message=f"已获取 {sample_count} 个对标爆款视频",
                    reference_sample_count=sample_count,
                )
            elif prefetch_state == "error":
                emit_module_analyze_progress(
                    progress_callback,
                    stage="reference_videos_failed",
                    percent=82.0,
                    message="对标样本加载失败，正在整理分析结果",
                    reference_sample_count=0,
                )
        return exports.finalize_module_analyze_result(result, resolved, market_snapshot)
    except Exception as exc:
        if should_skip_same_provider_fallback(exc):
            raise
        raise RuntimeError(f"视频分析 Agent 执行或结果归一化失败：{format_llm_error(exc)}") from exc


@traceable(run_type="chain", name="web.run_llm_chat", tags=["web", "llm", "rag", "workspace_chat"])
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
            "memory_user_id": "web_workspace_chat",
        },
        response_contract=response_contract,
        allowed_tools=allowed_tools_for_scene("workspace_chat"),
        required_final_keys=["reply", "suggested_next_actions", "mode"],
    )
    chat_query_text = " ".join(
        value
        for value in [
            message,
            creator_context.get("field", ""),
            creator_context.get("direction", ""),
            creator_context.get("idea", ""),
            creator_context.get("partition", ""),
        ]
        if value
    )
    result["reference_links"] = extract_reference_links_from_tool_observations(
        result.get("tool_observations", []),
        exclude_bvid="",
        query_text=chat_query_text,
    )
    return result


def execute_module_analyze_request(data: dict, progress_callback=None) -> dict:
    exports = app_exports()
    payload = dict(data or {})
    url = (payload.get("url") or "").strip()
    if not url:
        raise ModuleAnalyzeRequestError("请先输入 B 站视频链接", 400)

    emit_module_analyze_progress(
        progress_callback,
        stage="resolve_video",
        percent=8.0,
        message="正在解析视频信息...",
    )
    try:
        resolved = (
            payload.get("resolved")
            if exports.is_resolved_payload_usable(payload.get("resolved"), url)
            else exports.resolve_video_payload(url)
        )
    except Exception as exc:
        raise ModuleAnalyzeRequestError(f"链接解析失败：{exc}", 400) from exc

    emit_module_analyze_progress(
        progress_callback,
        stage="video_resolved",
        percent=18.0,
        message="已解析当前视频信息",
        resolved=resolved,
    )

    if exports.runtime_llm_enabled():
        emit_module_analyze_progress(
            progress_callback,
            stage="load_reference_videos",
            percent=28.0,
            message="正在加载对标样本...",
            resolved=resolved,
        )
        executor = ThreadPoolExecutor(max_workers=1)
        market_snapshot_future = executor.submit(build_hot_peer_market_snapshot, resolved)
        market_snapshot, prefetch_state = get_prefetched_market_snapshot(
            market_snapshot_future,
            resolved,
            VIDEO_ANALYZE_MARKET_SNAPSHOT_PREFETCH_WAIT_SECONDS,
        )
        sample_count = safe_int(market_snapshot.get("source_count"))
        if sample_count > 0:
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_ready",
                percent=40.0,
                message=f"已获取 {sample_count} 个对标爆款视频",
                reference_sample_count=sample_count,
            )
        elif prefetch_state == "pending":
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_pending",
                percent=40.0,
                message="对标样本仍在加载，先继续检索本地知识库...",
                reference_sample_count=0,
            )
        elif prefetch_state == "error":
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_failed",
                percent=40.0,
                message="对标样本加载失败，先继续检索本地知识库...",
                reference_sample_count=0,
            )
        else:
            emit_module_analyze_progress(
                progress_callback,
                stage="reference_videos_sparse",
                percent=40.0,
                message="当前未拿到足够对标样本，先继续检索本地知识库...",
                reference_sample_count=0,
            )

        try:
            emit_module_analyze_progress(
                progress_callback,
                stage="retrieval_and_analysis",
                percent=56.0,
                message="正在检索本地知识库并分析视频...",
            )
            result = run_llm_module_analyze(
                payload,
                resolved,
                market_snapshot,
                market_snapshot_future=market_snapshot_future,
                progress_callback=progress_callback,
            )
            emit_module_analyze_progress(
                progress_callback,
                stage="finalizing_result",
                percent=94.0,
                message="正在整理优化建议和参考视频...",
                reference_sample_count=len(result.get("reference_videos") or []),
            )
            return result
        except Exception as exc:
            if should_skip_same_provider_fallback(exc):
                message = (
                    f"LLM Agent 分析失败：{format_llm_error(exc)} "
                    "当前不会继续尝试同 provider 的 fallback，请稍后重试。"
                )
                raise ModuleAnalyzeRequestError(
                    message,
                    llm_error_http_status(exc),
                    build_llm_runtime_reconfigure_data(message),
                ) from exc

            emit_module_analyze_progress(
                progress_callback,
                stage="fallback_analysis",
                percent=72.0,
                message="Agent 中枢执行失败，正在切换 LLM 直出分析...",
            )
            try:
                fallback_result = run_llm_module_analyze_fallback(
                    payload,
                    resolved,
                    market_snapshot,
                    market_snapshot_future=market_snapshot_future,
                    progress_callback=progress_callback,
                )
                fallback_result["llm_warning"] = f"Agent 中枢执行失败，已切换到 LLM 直出分析：{format_llm_error(exc)}"
                emit_module_analyze_progress(
                    progress_callback,
                    stage="finalizing_result",
                    percent=94.0,
                    message="正在整理回退分析结果...",
                    reference_sample_count=len(fallback_result.get("reference_videos") or []),
                )
                return fallback_result
            except Exception as fallback_exc:
                message = (
                    f"LLM Agent 分析失败：{format_llm_error(exc)}；"
                    f"LLM fallback 也失败：{format_llm_error(fallback_exc)}"
                )
                raise ModuleAnalyzeRequestError(
                    message,
                    llm_error_http_status(fallback_exc),
                    build_llm_runtime_reconfigure_data(message),
                ) from fallback_exc
        finally:
            executor.shutdown(wait=False)

    emit_module_analyze_progress(
        progress_callback,
        stage="classify_performance",
        percent=40.0,
        message="正在判断视频表现...",
    )
    topic_result = exports.run_topic(
        partition_name=resolved.get("partition"),
        up_ids=resolved.get("up_ids"),
        seed_topic=resolved.get("topic"),
    )
    performance = exports.classify_video_performance(resolved)

    copy_result = None
    optimize_result: dict = {}
    analysis = {}
    if performance["is_hot"]:
        emit_module_analyze_progress(
            progress_callback,
            stage="generate_suggestions",
            percent=72.0,
            message="正在整理爆款原因和后续题材...",
        )
        analysis = exports.build_hot_analysis(resolved, performance, topic_result)
    else:
        emit_module_analyze_progress(
            progress_callback,
            stage="generate_suggestions",
            percent=72.0,
            message="正在生成优化建议和文案...",
        )
        optimize_result = to_plain_data(exports.build_rule_optimization_agent().run(resolved.get("bv_id", "BV1Demo411111")))
        copy_result = to_plain_data(
            exports.build_rule_copy_agent().run(
                topic=resolved.get("topic") or resolved.get("title") or "视频优化",
                style=resolved.get("style", "干货"),
            )
        )
        analysis = exports.build_low_performance_analysis(resolved, performance, optimize_result, topic_result)

    emit_module_analyze_progress(
        progress_callback,
        stage="finalizing_result",
        percent=90.0,
        message="正在整理参考视频和最终结果...",
    )
    reference_videos = exports.select_reference_videos(
        topic_result.get("videos", []),
        exclude_bvid=resolved.get("bv_id", ""),
        limit=6,
        query_text=build_reference_query_text(resolved),
        resolved=resolved,
    )

    return {
        "resolved": resolved,
        "performance": performance,
        "topic_result": topic_result,
        "optimize_result": optimize_result,
        "copy_result": copy_result,
        "analysis": analysis,
        "reference_videos": reference_videos,
        "reference_videos_notice": (
            "" if reference_videos else "当前题材公开可用的对标样本不足，暂未整理出可直接展示的参考视频。"
        ),
        "runtime_mode": "rules",
    }
