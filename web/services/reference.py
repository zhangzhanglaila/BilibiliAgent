from __future__ import annotations

from importlib import import_module

from web.services.content import *


def app_exports():
    return import_module("web.app")

def looks_like_music_reference(text: str) -> bool:
    normalized = normalize_reference_text(text)
    return bool(normalized) and any(token in normalized for token in MUSIC_REFERENCE_KEYWORDS)


def extract_latin_reference_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z0-9]+){0,3}", text or ""):
        phrase = re.sub(r"\s+", " ", match).strip()
        marker = phrase.lower()
        if len(phrase) < 2 or marker in seen:
            continue
        seen.add(marker)
        phrases.append(phrase)
    return phrases[:4]


def append_benchmark_query(queries: list[str], seen: set[str], parts: list[str]) -> None:
    value = re.sub(r"\s+", " ", " ".join(str(part or "").strip() for part in parts if str(part or "").strip())).strip(
        " ，,。.;；:-_|"
    )
    marker = value.lower()
    if len(value) < 2 or marker in seen:
        return
    seen.add(marker)
    queries.append(value)


def normalize_benchmark_term(value: object) -> str:
    clean = re.sub(r"\s+", " ", str(value or "").strip()).strip(" ，,。.;；:-_|")
    marker = clean.lower()
    if len(clean) < 2 or marker in VIDEO_BENCHMARK_QUERY_STOPWORDS or clean in VIDEO_BENCHMARK_WEAK_TERMS:
        return ""
    return clean


def append_benchmark_term(terms: list[str], value: object, limit: int = 8) -> None:
    clean = normalize_benchmark_term(value)
    marker = clean.lower()
    if not clean or marker in {item.lower() for item in terms}:
        return
    if any(marker != existing.lower() and marker in existing.lower() for existing in terms):
        return
    terms.append(clean)
    if limit > 0 and len(terms) > limit:
        del terms[limit:]


def extract_geo_reference_terms(text: str) -> list[str]:
    terms: list[str] = []
    source_text = str(text or "")
    for token in OVERSEAS_REFERENCE_LOCATION_TERMS:
        if token in source_text:
            append_benchmark_term(terms, token, limit=4)
    for match in re.findall(r"([\u4e00-\u9fff]{2,4})(?:赶海|海边|海鲜|低潮|退潮)", source_text):
        if match in {"田园美食", "海鲜收获", "赶海收获", "生活记录"}:
            continue
        append_benchmark_term(terms, match, limit=4)
    return terms[:4]


def extract_sea_harvest_reference_terms(text: str) -> list[str]:
    terms: list[str] = []
    source_text = str(text or "")
    for token in ("赶海", "海鲜", "海鲜收获"):
        append_benchmark_term(terms, token)
    geo_terms = extract_geo_reference_terms(source_text)
    for token in geo_terms:
        append_benchmark_term(terms, token)
        if token in OVERSEAS_REFERENCE_LOCATION_TERMS:
            append_benchmark_term(terms, "海外赶海")
    for token in SEA_HARVEST_TARGET_KEYWORDS:
        if token in source_text:
            append_benchmark_term(terms, token)
    for match in re.findall(r"(?:海参|章鱼|八爪鱼|[\u4e00-\u9fff]{1,4}[蟹虾鱼贝蛤螺蚝鲍])", source_text):
        append_benchmark_term(terms, match)
    if "繁殖" in source_text:
        append_benchmark_term(terms, "繁殖潮")
    return terms[:8]


def extract_narrative_reference_terms(text: str) -> list[str]:
    terms: list[str] = []
    source_text = str(text or "")
    if "网络" in source_text and "喷子" in source_text:
        append_benchmark_term(terms, "网络喷子")
    if "线下" in source_text and "见面" in source_text:
        append_benchmark_term(terms, "线下见面")
    for token in NARRATIVE_REFERENCE_KEYWORDS:
        if token in source_text:
            append_benchmark_term(terms, token)
    return terms[:8]


def build_reference_match_terms(query_text: str = "", resolved: dict | None = None) -> list[str]:
    terms: list[str] = []
    if isinstance(resolved, dict):
        profile = build_video_benchmark_profile(resolved)
        append_benchmark_term(terms, profile.get("lane_label"), limit=24)
        for term in profile.get("terms") or []:
            append_benchmark_term(terms, term, limit=24)
    for term in extract_reference_terms(query_text):
        append_benchmark_term(terms, term, limit=24)
    return terms[:24]

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
        "estimated": bool((payload.get("extra") or {}).get("estimated")),
    }


# 汇总全站、分区和同类账号样本，生成一份市场快照。
def build_market_snapshot(partition_name: str, up_ids: list[int] | None = None) -> dict:
    normalized_partition = CONFIG.normalize_partition(partition_name)
    partition_label = PARTITION_LABELS.get(normalized_partition, normalized_partition)

    def fetch_hot_board() -> list[dict]:
        try:
            return [serialize_video_metric(item) for item in RAW_TOPIC_AGENT.fetch_hot_videos()[:6]]
        except Exception:
            return []

    def fetch_partition_samples() -> list[dict]:
        try:
            return [
                {
                    **serialize_video_metric(item),
                    "partition": normalized_partition,
                    "partition_label": partition_label,
                }
                for item in RAW_TOPIC_AGENT.fetch_partition_videos(normalized_partition)[:6]
            ]
        except Exception:
            return []

    def fetch_peer_samples() -> list[dict]:
        try:
            return [
                {
                    **serialize_video_metric(item),
                    "partition": normalized_partition,
                    "partition_label": partition_label,
                }
                for item in RAW_TOPIC_AGENT.fetch_peer_up_videos(up_ids)[:6]
            ]
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=3) as executor:
        hot_future = executor.submit(fetch_hot_board)
        partition_future = executor.submit(fetch_partition_samples)
        peer_future = executor.submit(fetch_peer_samples)
        hot_board = hot_future.result()
        partition_samples = partition_future.result()
        peer_samples = peer_future.result()

    return {
        "partition": normalized_partition,
        "partition_label": partition_label,
        "source_count": len(hot_board) + len(partition_samples) + len(peer_samples),
        "hot_board": hot_board,
        "partition_samples": partition_samples,
        "peer_samples": peer_samples,
    }


def build_empty_market_snapshot(partition_name: str) -> dict:
    normalized_partition = CONFIG.normalize_partition(partition_name)
    partition_label = PARTITION_LABELS.get(normalized_partition, normalized_partition)
    return {
        "partition": normalized_partition,
        "partition_label": partition_label,
        "source_count": 0,
        "hot_board": [],
        "partition_samples": [],
        "peer_samples": [],
    }


# 为视频分析模块归纳方向词、检索词和更可靠的对标分区。
def build_video_benchmark_profile(resolved: dict) -> dict:
    partition_label = str(resolved.get("partition_label") or "").strip()
    tname = str(resolved.get("tname") or "").strip()
    title = str(resolved.get("title") or "").strip()
    topic = str(resolved.get("topic") or "").strip()
    raw_keywords = extract_video_keywords(resolved.get("keywords"))
    keywords = [keyword for keyword in raw_keywords if keyword not in VIDEO_BENCHMARK_WEAK_TERMS]
    title_terms = [
        term
        for term in extract_reference_terms(" ".join([title, topic]))
        if term not in VIDEO_BENCHMARK_QUERY_STOPWORDS and term not in VIDEO_BENCHMARK_WEAK_TERMS
    ]
    series_terms = extract_series_reference_terms(" ".join([title, topic]))
    latin_phrases = extract_latin_reference_phrases(" ".join([title, topic]))
    combined_text = " ".join([title, topic, tname, partition_label, *raw_keywords])
    effective_partition = str(resolved.get("partition") or "").strip()
    lane_label = tname or partition_label
    query_lane_label = normalize_benchmark_term(lane_label)
    if looks_like_music_reference(combined_text):
        effective_partition = "ent"
        lane_label = "音乐"
        query_lane_label = "音乐"
        if "钢琴" in combined_text and "钢琴" not in keywords:
            keywords.insert(0, "钢琴")

    terms: list[str] = []
    append_benchmark_term(terms, query_lane_label)
    short_keywords = [keyword for keyword in keywords if 2 <= len(keyword) <= 10]
    sea_harvest_terms = any(token in combined_text for token in SEA_HARVEST_KEYWORDS)
    narrative_terms = extract_narrative_reference_terms(combined_text)
    if sea_harvest_terms:
        for term in extract_sea_harvest_reference_terms(combined_text):
            append_benchmark_term(terms, term)
    else:
        for term in narrative_terms:
            append_benchmark_term(terms, term)
    for term in series_terms + short_keywords + latin_phrases + title_terms + keywords:
        append_benchmark_term(terms, term)
        if len(terms) >= 8:
            break

    queries: list[str] = []
    seen_queries: set[str] = set()
    geo_terms = extract_geo_reference_terms(combined_text)
    sea_terms = [term for term in terms if term in extract_sea_harvest_reference_terms(combined_text)]
    if sea_harvest_terms:
        append_benchmark_query(queries, seen_queries, [query_lane_label, "赶海", "海鲜收获"])
        if any(term in OVERSEAS_REFERENCE_LOCATION_TERMS for term in geo_terms):
            append_benchmark_query(queries, seen_queries, ["海外赶海", "海鲜收获"])
        if geo_terms:
            append_benchmark_query(
                queries,
                seen_queries,
                [
                    geo_terms[0],
                    "赶海",
                    next(
                        (
                            term
                            for term in sea_terms
                            if term not in {"赶海", "海鲜", "海鲜收获", "海外赶海", *geo_terms}
                        ),
                        "海鲜",
                    ),
                ],
            )
        append_benchmark_query(queries, seen_queries, ["赶海", "海鲜", next((term for term in sea_terms if term not in {"赶海", "海鲜", "海鲜收获", "海外赶海"}), "收获")])
    elif narrative_terms:
        if "网络喷子" in narrative_terms:
            append_benchmark_query(queries, seen_queries, ["网络喷子", "人性"])
            append_benchmark_query(queries, seen_queries, ["网络喷子", "线下见面"])
        append_benchmark_query(queries, seen_queries, narrative_terms[:3])
    if latin_phrases:
        append_benchmark_query(queries, seen_queries, [query_lane_label, latin_phrases[0]])
        if short_keywords:
            append_benchmark_query(queries, seen_queries, [short_keywords[0], latin_phrases[0]])
        if series_terms:
            append_benchmark_query(queries, seen_queries, [series_terms[0], latin_phrases[0]])
    if series_terms:
        append_benchmark_query(queries, seen_queries, [series_terms[0], query_lane_label or terms[0] if terms else ""])
        append_benchmark_query(queries, seen_queries, [series_terms[0]])
    append_benchmark_query(queries, seen_queries, [query_lane_label, *terms[1:3]])
    append_benchmark_query(queries, seen_queries, terms[:3])
    append_benchmark_query(queries, seen_queries, terms[:2])
    if title:
        append_benchmark_query(queries, seen_queries, [title[:32]])
    if not queries:
        append_benchmark_query(queries, seen_queries, [lane_label or partition_label, title[:20]])

    return {
        "effective_partition": effective_partition or str(resolved.get("partition") or "").strip(),
        "effective_partition_label": PARTITION_LABELS.get(effective_partition, effective_partition) if effective_partition else partition_label,
        "lane_label": lane_label or partition_label,
        "terms": terms[:8],
        "queries": queries[:4],
    }


# 为视频分析模块构造“同方向爆款”检索词，避免退化成同 UP 样本。
def build_video_benchmark_queries(resolved: dict) -> list[str]:
    return list(build_video_benchmark_profile(resolved).get("queries") or [])


def fetch_hot_peer_samples_with_relaxed_backfill(
    queries: list[str],
    *,
    exclude_bvid: str,
    limit: int,
) -> list[dict]:
    normalized_queries = [str(query or "").strip() for query in queries if str(query or "").strip()]
    if not normalized_queries:
        return []

    candidates: list[dict] = []
    seen: set[str] = set()

    def extend(items: list[dict]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            bvid = normalize_text_value(item.get("bvid"))
            url = normalize_text_value(item.get("url"))
            identity = bvid.lower() if bvid else url
            if not identity or identity in seen:
                continue
            seen.add(identity)
            candidates.append(item)
            if len(candidates) >= limit:
                return

    try:
        strict_samples = RAW_TOPIC_AGENT.fetch_hot_peer_videos(
            normalized_queries,
            exclude_bvid=exclude_bvid,
            limit=limit,
            recent_days=VIDEO_ANALYZE_HOT_PEER_RECENT_DAYS,
            min_view=VIDEO_ANALYZE_HOT_PEER_MIN_VIEW,
            min_like=VIDEO_ANALYZE_HOT_PEER_MIN_LIKE,
        )
    except Exception:
        strict_samples = []
    try:
        extend([serialize_video_metric(item) for item in strict_samples])
    except Exception:
        pass
    if len(candidates) >= limit:
        return candidates[:limit]

    try:
        relaxed_samples = RAW_TOPIC_AGENT.fetch_hot_peer_videos(
            normalized_queries,
            exclude_bvid=exclude_bvid,
            limit=max(limit * 2, 8),
            recent_days=VIDEO_ANALYZE_HOT_PEER_RELAXED_RECENT_DAYS,
            min_view=VIDEO_ANALYZE_HOT_PEER_RELAXED_MIN_VIEW,
            min_like=VIDEO_ANALYZE_HOT_PEER_RELAXED_MIN_LIKE,
        )
    except Exception:
        relaxed_samples = []
    try:
        extend([serialize_video_metric(item) for item in relaxed_samples])
    except Exception:
        pass
    return candidates[:limit]


# 仅为视频分析模块预抓同方向爆款对标样本，不混入同 UP 数据。
def build_hot_peer_market_snapshot(resolved: dict) -> dict:
    profile = build_video_benchmark_profile(resolved)
    snapshot = build_empty_market_snapshot(profile.get("effective_partition") or resolved.get("partition"))
    if profile.get("effective_partition_label"):
        snapshot["partition_label"] = profile.get("effective_partition_label")
    queries = list(profile.get("queries") or [])
    if not queries:
        return snapshot
    ranking_resolved = dict(resolved or {})
    ranking_resolved["partition"] = snapshot["partition"]
    ranking_resolved["partition_label"] = snapshot["partition_label"]
    if profile.get("lane_label") and not ranking_resolved.get("tname"):
        ranking_resolved["tname"] = profile.get("lane_label")
    if profile.get("terms"):
        ranking_resolved["keywords"] = list(profile.get("terms") or [])
    query_text = " ".join([str(profile.get("lane_label") or "").strip(), *list(profile.get("terms") or [])]).strip()
    try:
        raw_samples = fetch_hot_peer_samples_with_relaxed_backfill(
            queries,
            exclude_bvid=resolved.get("bv_id", ""),
            limit=VIDEO_ANALYZE_HOT_PEER_LIMIT,
        )
        peer_samples = []
        for item in raw_samples[: VIDEO_ANALYZE_HOT_PEER_LIMIT * 3]:
            candidate = {
                **dict(item or {}),
                "partition": snapshot["partition"],
                "partition_label": snapshot["partition_label"],
            }
            _, meta = build_reference_rank_entry(candidate, query_text=query_text, resolved=ranking_resolved)
            if query_text and has_strict_reference_signal(ranking_resolved, query_text) and not meta.get("is_related"):
                continue
            peer_samples.append(candidate)
            if len(peer_samples) >= VIDEO_ANALYZE_HOT_PEER_LIMIT:
                break
    except Exception:
        peer_samples = []
    snapshot["peer_samples"] = peer_samples
    snapshot["source_count"] = len(peer_samples)
    return snapshot


# 把单条市场样本压缩成更适合放进提示词的轻量结构。
def compact_market_item_for_llm(item: dict) -> dict:
    return {
        "bvid": item.get("bvid", ""),
        "title": item.get("title", ""),
        "author": item.get("author", ""),
        "view": safe_int(item.get("view")),
        "like": safe_int(item.get("like")),
        "coin": safe_int(item.get("coin")),
        "favorite": safe_int(item.get("favorite")),
        "reply": safe_int(item.get("reply")),
        "share": safe_int(item.get("share")),
        "like_rate": float(item.get("like_rate") or 0.0),
        "completion_rate": float(item.get("completion_rate") or 0.0),
        "competition_score": float(item.get("competition_score") or 0.0),
        "source": item.get("source", ""),
        "url": item.get("url", ""),
    }


# 把完整市场快照压缩成更适合提供给 LLM 的输入。
def compact_market_snapshot_for_llm(market_snapshot: dict, limit: int = 4) -> dict:
    return {
        "partition": market_snapshot.get("partition", ""),
        "partition_label": market_snapshot.get("partition_label", ""),
        "source_count": safe_int(market_snapshot.get("source_count")),
        "hot_board": [compact_market_item_for_llm(item) for item in (market_snapshot.get("hot_board") or [])[:limit]],
        "partition_samples": [
            compact_market_item_for_llm(item) for item in (market_snapshot.get("partition_samples") or [])[:limit]
        ],
        "peer_samples": [compact_market_item_for_llm(item) for item in (market_snapshot.get("peer_samples") or [])[:limit]],
    }


# 判断一条候选样本是否是真实可打开的参考视频。
def is_real_reference_video(item: dict) -> bool:
    bvid = (item.get("bvid") or "").strip()
    url = (item.get("url") or "").strip()
    if not url or item.get("estimated"):
        return False
    return bool(re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE))


# 归一化参考视频检索文本，方便后续做关键词拆分。
def normalize_reference_text(text: str) -> str:
    value = re.sub(r"[【】\[\]（）()<>《》\"'`~!@#$%^&*_+=|\\/:;,.?？！，。、“”·-]+", " ", text or "")
    collapsed = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    return re.sub(r"\s+", " ", collapsed).strip().lower()


# 把一个候选检索词按规则追加到去重后的词表里。
def append_reference_term(terms: list[str], term: str) -> None:
    value = (term or "").strip().lower()
    if len(value) < 2 or value.isdigit() or value in REFERENCE_STOPWORDS or value in terms:
        return
    terms.append(value)


# 从文本里抽取可用于搜索参考视频的一组关键词。
def extract_reference_terms(text: str) -> list[str]:
    clean = normalize_reference_text(text)
    chunks = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", clean)
    terms: list[str] = []

    for chunk in chunks:
        append_reference_term(terms, chunk)
        if re.fullmatch(r"[A-Za-z0-9]+", chunk):
            continue

        max_size = min(5, len(chunk))
        min_size = 2 if len(chunk) <= 5 else 3
        for size in range(max_size, min_size - 1, -1):
            for index in range(0, len(chunk) - size + 1):
                append_reference_term(terms, chunk[index : index + size])
    return terms[:32]


def extract_series_reference_terms(text: str) -> list[str]:
    source_text = str(text or "")
    compact_text = re.sub(r"\s+", "", source_text)
    terms: list[str] = []
    seen: set[str] = set()

    def append_series_term(value: str) -> None:
        clean = normalize_benchmark_term(value)
        marker = clean.lower()
        if not clean or marker in seen:
            return
        seen.add(marker)
        terms.append(clean)

    for match in re.findall(r"《([^》]{2,24})》", compact_text):
        cleaned = re.sub(r"(?:第)?\d+(?:\.\d+)?(?:季|集|期|弹|部|篇)?$", "", match).strip()
        append_series_term(cleaned)

    for match in re.findall(r"([\u4e00-\u9fff]{3,16})(?:第?\d+(?:\.\d+)?(?:季|集|期|弹|部|篇)?)", compact_text):
        append_series_term(match)

    return terms[:4]


# 判断原标题是否真的带有内容语义，而不是抽象情绪句或短梗。
def has_semantic_reference_title(title: str, keywords: list[str] | None = None) -> bool:
    clean_title = normalize_reference_text(title)
    if not clean_title:
        return False

    keyword_list = extract_video_keywords(keywords)
    if keyword_list and any(keyword.lower() in clean_title for keyword in keyword_list):
        return True

    semantic_tokens = [
        "舞蹈",
        "卡点",
        "变速",
        "颜值",
        "美女",
        "身材",
        "穿搭",
        "约会",
        "异地恋",
        "vlog",
        "教程",
        "赶海",
        "海货",
        "海鲜",
        "潮水",
        "蛤",
        "蛏",
        "海螺",
        "螃蟹",
    ]
    if any(token in clean_title for token in semantic_tokens):
        return True
    return False


# 判断当前视频是否已经拿到了足够明确的内容语义，可对参考视频启用严格相关性过滤。
def has_strict_reference_signal(resolved: dict | None = None, query_text: str = "") -> bool:
    resolved = resolved or {}
    keywords = extract_video_keywords(resolved.get("keywords"))
    if keywords:
        return True
    topic = str(resolved.get("topic") or "")
    title = str(resolved.get("title") or "")
    return has_semantic_reference_title(topic) or has_semantic_reference_title(title) or has_semantic_reference_title(query_text)


# 组合视频上下文和额外输入，生成参考视频检索文本。
def build_reference_query_text(resolved: dict | None = None, extra_text: str = "") -> str:
    parts: list[str] = []
    if isinstance(resolved, dict):
        profile = build_video_benchmark_profile(resolved)
        lane_label = normalize_benchmark_term(profile.get("lane_label"))
        keywords = [normalize_benchmark_term(item) for item in (profile.get("terms") or [])]
        keywords = [item for item in keywords if item]
        if lane_label:
            keywords = [item for item in keywords if item != lane_label]
        semantic_values: list[str] = []

        for value in (lane_label, resolved.get("topic")):
            value = str(value or "").strip()
            if value:
                semantic_values.append(value)

        if keywords:
            semantic_values.append(" ".join(keywords[:4]))
        else:
            fallback_keywords = extract_video_keywords(resolved.get("keywords"))
            if fallback_keywords:
                semantic_values.append(" ".join(fallback_keywords[:3]))

        title = (resolved.get("title") or "").strip()
        if title and has_semantic_reference_title(title, keywords=keywords) and not keywords:
            semantic_values.append(title)

        for value in semantic_values:
            if value and value not in parts:
                parts.append(value)

    extra_clean = (extra_text or "").strip()
    if extra_clean:
        parts.append(extra_clean)
    return " ".join(parts)


# 返回筛选参考视频时使用的最低播放门槛。
def build_reference_view_floor(resolved: dict | None = None) -> int:
    return 100000


# 从工具调用观测结果里抽取可用于参考视频检索的查询文本。
def extract_reference_query_from_observation(observation: dict) -> str:
    if not isinstance(observation, dict):
        return ""

    if isinstance(observation.get("video"), dict):
        return build_reference_query_text(
            {
                "title": observation["video"].get("title", ""),
                "topic": observation["video"].get("topic", "") or observation["video"].get("title", ""),
                "keywords": observation["video"].get("keywords", []),
                "tname": observation["video"].get("tname", ""),
                "partition_label": observation["video"].get("retrieval_partition_label", ""),
            }
        )

    if isinstance(observation.get("user_input"), dict):
        user_input = observation["user_input"]
        return build_reference_query_text(
            {
                "title": "",
                "topic": "",
                "tname": "",
                "partition_label": user_input.get("partition", ""),
                "up_name": "",
            },
            extra_text=" ".join(
                [
                    (user_input.get("field") or "").strip(),
                    (user_input.get("direction") or "").strip(),
                    (user_input.get("idea") or "").strip(),
                ]
            ),
        )

    return ""


# 去掉参考搜索结果里的 HTML 标签和转义字符。
def strip_reference_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or "")).strip()


# 根据上下文组合多组参考视频搜索词，提升召回率。
def build_reference_search_queries(query_text: str = "", resolved: dict | None = None) -> list[str]:
    queries: list[str] = []
    has_semantic_keywords = False

    if isinstance(resolved, dict):
        profile = build_video_benchmark_profile(resolved)
        base_topic = (resolved.get("topic") or resolved.get("title") or "").strip()
        partition_label = normalize_benchmark_term(profile.get("lane_label")) or (
            resolved.get("partition_label") or resolved.get("tname") or ""
        ).strip()
        keywords = [term for term in profile.get("terms") or [] if normalize_benchmark_term(term)]

        for query in profile.get("queries") or []:
            queries.append(query)

        if keywords:
            has_semantic_keywords = True
            queries.append(" ".join(keywords[:2]))
            if len(keywords) >= 3:
                queries.append(" ".join(keywords[:3]))

        if base_topic:
            queries.append(base_topic[:50])
            if keywords:
                queries.append(f"{base_topic[:28]} {' '.join(keywords[:2])}".strip())
            if partition_label and partition_label not in base_topic and not has_semantic_keywords:
                queries.append(f"{base_topic[:40]} {partition_label}")

    if not has_semantic_keywords:
        compact_query = " ".join(extract_reference_terms(query_text))[:60].strip()
        if compact_query:
            queries.append(compact_query)

        core_terms = sorted(
            [term for term in extract_reference_terms(query_text) if len(term) >= 2],
            key=lambda item: (-len(item), item),
        )
        if core_terms:
            queries.append(" ".join(core_terms[:2]))
            if len(core_terms) >= 3:
                queries.append(" ".join(core_terms[:3]))

    deduped: list[str] = []
    for item in queries:
        value = (item or "").strip()
        if len(value) < 2 or value in deduped:
            continue
        deduped.append(value)
    return deduped[:3] if has_semantic_keywords else deduped[:5]


# 通过 B 站相关推荐接口直接拉取一批相关参考视频。
def fetch_direct_related_reference_videos(bvid: str, limit: int = 10) -> list[dict]:
    clean_bvid = (bvid or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", clean_bvid, flags=re.IGNORECASE):
        return []

    payload = fetch_json(f"https://api.bilibili.com/x/web-interface/archive/related?{urlencode({'bvid': clean_bvid})}")
    if safe_int(payload.get("code")) != 0:
        raise ValueError(payload.get("message") or "B站相关推荐接口失败")

    items = payload.get("data") or []
    results: list[dict] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        candidate_bvid = (item.get("bvid") or "").strip()
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", candidate_bvid, flags=re.IGNORECASE):
            continue

        stat = item.get("stat") or {}
        owner = item.get("owner") or {}
        results.append(
            {
                "bvid": candidate_bvid,
                "title": strip_reference_html(item.get("title", "")),
                "author": strip_reference_html(owner.get("name") or item.get("owner_name") or ""),
                "cover": item.get("pic") or item.get("cover") or "",
                "mid": safe_int(owner.get("mid")),
                "view": safe_int(stat.get("view")),
                "like": safe_int(stat.get("like")),
                "coin": safe_int(stat.get("coin")),
                "favorite": safe_int(stat.get("favorite")),
                "reply": safe_int(stat.get("reply")),
                "share": safe_int(stat.get("share")),
                "duration": safe_int(item.get("duration")),
                "avg_view_duration": 0.0,
                "like_rate": safe_int(stat.get("like")) / max(safe_int(stat.get("view")), 1),
                "completion_rate": 0.0,
                "competition_score": 0.0,
                "source": "当前视频相关推荐",
                "url": f"https://www.bilibili.com/video/{candidate_bvid}",
                "estimated": False,
            }
        )
    return results


def fetch_same_up_reference_videos(resolved: dict | None = None, limit: int = 8) -> list[dict]:
    resolved = resolved or {}
    exports = app_exports()
    up_ids = [safe_int(item) for item in (resolved.get("up_ids") or []) if safe_int(item) > 0]
    if not up_ids and safe_int(resolved.get("mid")) > 0:
        up_ids = [safe_int(resolved.get("mid"))]
    if not up_ids:
        return []

    try:
        items = exports.RAW_TOPIC_AGENT.fetch_peer_up_videos(up_ids)[: max(limit * 2, 8)]
    except Exception:
        return []

    results: list[dict] = []
    current_mid = safe_int(resolved.get("mid"))
    for item in items:
        try:
            payload = exports.serialize_video_metric(item)
        except Exception:
            continue
        payload["source"] = (
            "当前UP主近期视频"
            if current_mid and safe_int(payload.get("mid")) == current_mid
            else payload.get("source") or "同类UP近期视频"
        )
        payload["partition"] = payload.get("partition") or resolved.get("partition", "")
        payload["partition_label"] = payload.get("partition_label") or resolved.get("partition_label", "")
        results.append(payload)
        if len(results) >= limit:
            break
    return results


# 通过搜索接口按关键词拉取参考视频候选集。
def fetch_search_reference_videos(query: str, limit: int = 8) -> list[dict]:
    if not query:
        return []

    params = {
        "search_type": "video",
        "keyword": query,
        "order": "click",
        "page": 1,
        "page_size": max(1, min(limit, 20)),
    }
    url = f"https://api.bilibili.com/x/web-interface/search/type?{urlencode(params)}"
    payload = fetch_json(url)
    if safe_int(payload.get("code")) != 0:
        raise ValueError(payload.get("message") or "B站搜索接口失败")

    data = payload.get("data") or {}
    items = data.get("result") or []
    results: list[dict] = []

    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        bvid = (item.get("bvid") or "").strip()
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
            continue
        search_like_raw = item.get("like")
        search_like = safe_metric_int(search_like_raw) if search_like_raw not in (None, "") else None
        view = safe_metric_int(item.get("play"))

        results.append(
            {
                "bvid": bvid,
                "title": strip_reference_html(item.get("title", "")),
                "author": strip_reference_html(item.get("author", "")),
                "cover": item.get("pic") or item.get("cover") or "",
                "mid": safe_int(item.get("mid")),
                "view": view,
                "like": search_like,
                "coin": 0,
                "favorite": safe_metric_int(item.get("favorites")),
                "reply": safe_metric_int(item.get("review")),
                "share": 0,
                "duration": safe_int(item.get("duration")),
                "avg_view_duration": 0.0,
                "like_rate": (search_like or 0) / max(view, 1),
                "completion_rate": 0.0,
                "competition_score": 0.0,
                "source": f"相关搜索:{query}",
                "url": item.get("arcurl") or f"https://www.bilibili.com/video/{bvid}",
                "estimated": False,
            }
        )
    return results


# 用搜索结果扩充参考视频候选集，补足直连相关推荐不够的情况。
def enrich_reference_sources_with_search(
    sources: list[dict],
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    exports = app_exports()
    combined: list[dict] = []
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    if isinstance(resolved, dict):
        try:
            combined.extend(exports.fetch_direct_related_reference_videos(resolved.get("bv_id", "")))
        except Exception:
            pass
        if len(combined) < 6:
            try:
                combined.extend(exports.fetch_same_up_reference_videos(resolved, limit=8))
            except Exception:
                pass
    combined.extend(list(sources or []))
    for query in build_reference_search_queries(query_text=query_text, resolved=resolved):
        try:
            combined.extend(exports.fetch_search_reference_videos(query, limit=6 if strict_related_only else 8))
        except Exception:
            continue
    return combined


# 按需补齐参考视频的公开详情，主要用于把搜索候选补成明确播放和点赞数据。
def fetch_reference_video_detail(bvid: str, url: str = "") -> dict | None:
    clean_bvid = (bvid or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", clean_bvid, flags=re.IGNORECASE):
        return None

    cache_key = clean_bvid.lower()
    cached = REFERENCE_VIDEO_DETAIL_CACHE.get(cache_key)
    if isinstance(cached, dict):
        return dict(cached)

    info: dict | None = None
    reference_url = url or f"https://www.bilibili.com/video/{clean_bvid}"
    used_public_api = False
    try:
        info = fetch_video_info_via_public_api(clean_bvid)
        used_public_api = True
    except Exception:
        if reference_url:
            try:
                info = fetch_video_info_via_html(reference_url, clean_bvid)
            except Exception:
                info = None

    if not isinstance(info, dict) or not info:
        return None

    enriched_info = dict(info)
    title = enriched_info.get("title", "")
    tid = safe_int(enriched_info.get("tid"))
    merged_keywords = extract_video_keywords(enriched_info.get("keywords"))

    if used_public_api:
        for keyword in fetch_video_tags(clean_bvid):
            if keyword not in merged_keywords:
                merged_keywords.append(keyword)

    tname = normalize_video_tname(enriched_info.get("tname", ""), tid, merged_keywords, title)
    if not tname and reference_url:
        try:
            html_info = fetch_video_info_via_html(reference_url, clean_bvid)
        except Exception:
            html_info = None
        if isinstance(html_info, dict) and html_info:
            if not title:
                title = html_info.get("title", "")
            if not tid:
                tid = safe_int(html_info.get("tid"))
            for keyword in extract_video_keywords(html_info.get("keywords")):
                if keyword not in merged_keywords:
                    merged_keywords.append(keyword)
            tname = normalize_video_tname(html_info.get("tname", ""), tid, merged_keywords, title)
            if html_info.get("pic") and not enriched_info.get("pic"):
                enriched_info["pic"] = html_info.get("pic")

    enriched_info["keywords"] = merged_keywords[:8]
    if tname:
        enriched_info["tname"] = tname

    owner = enriched_info.get("owner") or {}
    stats = extract_video_stats(enriched_info)
    keywords = extract_video_keywords(enriched_info.get("keywords"))
    context_text = " ".join([title, tname, *keywords])
    partition = map_partition(tname, tid, context_text=context_text)
    topic = build_topic(title, keywords=keywords, tname=tname, tid=tid)
    style = guess_style(title, partition, tname, context_text=" ".join(keywords))
    detail = {
        "bvid": clean_bvid,
        "title": title,
        "author": owner.get("name") or owner.get("uname") or "",
        "cover": enriched_info.get("pic") or enriched_info.get("cover") or "",
        "mid": safe_int(owner.get("mid") or owner.get("mid_id")),
        "tid": tid,
        "tname": tname,
        "partition": partition,
        "partition_label": PARTITION_LABELS.get(partition, partition),
        "keywords": keywords,
        "topic": topic,
        "style": style,
        "view": stats.get("view"),
        "like": stats.get("like"),
        "coin": stats.get("coin"),
        "favorite": stats.get("favorite"),
        "reply": stats.get("reply"),
        "share": stats.get("share"),
        "duration": safe_int(enriched_info.get("duration")),
        "like_rate": float(stats.get("like_rate") or 0.0),
        "url": reference_url,
    }
    REFERENCE_VIDEO_DETAIL_CACHE[cache_key] = detail
    return dict(detail)


# 判断当前候选是否还缺少前端展示所需的明确指标。
def reference_video_needs_metric_refresh(item: dict) -> bool:
    view = safe_optional_int(item.get("view"))
    like = safe_optional_int(item.get("like"))
    like_rate = float(item.get("like_rate") or 0.0)
    return view is None or view <= 0 or like is None or (like <= 0 and like_rate <= 0.0)


def reference_video_needs_cover_refresh(item: dict) -> bool:
    return not normalize_text_value(item.get("cover"))


def reference_video_needs_semantic_refresh(item: dict) -> bool:
    partition = str(item.get("partition") or "").strip()
    topic = str(item.get("topic") or "").strip()
    tname = str(item.get("tname") or "").strip()
    keywords = extract_video_keywords(item.get("keywords"))
    return not partition or not topic or (not tname and not keywords)


def build_reference_semantic_text(item: dict) -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("topic") or ""),
        str(item.get("tname") or ""),
        str(item.get("partition") or ""),
        str(item.get("partition_label") or ""),
        str(item.get("style") or ""),
        " ".join(extract_video_keywords(item.get("keywords"))),
    ]
    return normalize_reference_text(" ".join(part for part in parts if part))


# 为最终展示前的参考视频补齐播放、点赞和基础信息。
def enrich_reference_video_for_display(item: dict, require_semantics: bool = False) -> dict:
    enriched = dict(item or {})
    need_refresh = (
        reference_video_needs_metric_refresh(enriched)
        or reference_video_needs_cover_refresh(enriched)
        or (require_semantics and reference_video_needs_semantic_refresh(enriched))
    )
    if not need_refresh:
        return enriched

    detail = app_exports().fetch_reference_video_detail(enriched.get("bvid", ""), enriched.get("url", ""))
    if not detail:
        return enriched

    for key in (
        "title",
        "author",
        "cover",
        "mid",
        "tid",
        "tname",
        "partition",
        "partition_label",
        "topic",
        "keywords",
        "style",
        "view",
        "like",
        "coin",
        "favorite",
        "reply",
        "share",
        "duration",
        "url",
    ):
        value = detail.get(key)
        if value not in (None, ""):
            enriched[key] = value
    enriched["like_rate"] = float(detail.get("like_rate") or enriched.get("like_rate") or 0.0)
    return enriched


# 判断参考视频卡片所需的播放和点赞是否都已经拿到明确数据。
def has_complete_reference_display_metrics(item: dict) -> bool:
    view = safe_optional_int(item.get("view"))
    like = safe_optional_int(item.get("like"))
    like_rate = float(item.get("like_rate") or 0.0)
    return bool(view and view > 0 and like is not None and (like > 0 or like_rate > 0.0))


# 为参考视频构造排序键，综合相关性、播放量和互动质量排序。
def build_reference_rank_entry(item: dict, query_text: str = "", resolved: dict | None = None) -> tuple[tuple, dict]:
    normalized_semantic_text = build_reference_semantic_text(item)
    title_terms = set(extract_reference_terms(item.get("title", "")))
    semantic_terms = set(extract_reference_terms(normalized_semantic_text))
    query_terms = build_reference_match_terms(query_text=query_text, resolved=resolved)
    matched_terms: list[str] = []

    for term in query_terms:
        if term in title_terms or term in semantic_terms or term in normalized_semantic_text:
            matched_terms.append(term)

    resolved = resolved or {}
    benchmark_profile = build_video_benchmark_profile(resolved) if resolved else {}
    lane_terms = set(extract_reference_terms(str(benchmark_profile.get("lane_label") or "")))
    target_keywords = extract_video_keywords(resolved.get("keywords"))
    target_keyword_terms = build_reference_match_terms(" ".join(target_keywords))
    matched_keywords = [
        term
        for term in target_keyword_terms
        if term in title_terms or term in semantic_terms or term in normalized_semantic_text
    ]
    core_matched_terms = [term for term in matched_terms if term not in lane_terms]
    overlap_score = sum(len(term) * len(term) for term in core_matched_terms)
    strong_match_count = sum(1 for term in core_matched_terms if len(term) >= 3)
    same_up = 1 if safe_int(item.get("mid")) and safe_int(item.get("mid")) == safe_int(resolved.get("mid")) else 0
    same_author = 1 if (item.get("author") or "").strip() == (resolved.get("up_name") or "").strip() else 0
    target_partition = str(resolved.get("partition") or "").strip()
    item_partition = str(item.get("partition") or "").strip()
    same_partition = 1 if target_partition and item_partition and item_partition == target_partition else 0
    source = item.get("source", "")
    source_priority = 0
    if "当前视频相关推荐" in source:
        source_priority = 4
    elif "相关搜索" in source:
        source_priority = 3
    elif "同类UP" in source:
        source_priority = 2
    elif same_up or same_author:
        source_priority = 1
    elif "分区" in source:
        source_priority = 0
    elif "热榜" in source:
        source_priority = -1

    strict_signal = has_strict_reference_signal(resolved, query_text)
    broad_life_story = target_partition == "life" and not normalize_benchmark_term(benchmark_profile.get("lane_label"))
    partition_aligned = (
        not strict_signal
        or not target_partition
        or not item_partition
        or same_partition == 1
        or (broad_life_story and (len(core_matched_terms) >= 2 or len(matched_keywords) >= 2))
    )
    semantic_aligned = bool(matched_keywords) or len(core_matched_terms) >= 2 or (strong_match_count >= 1 and len(core_matched_terms) >= 1)
    is_related = (semantic_aligned and partition_aligned) or bool(same_up) or bool(same_author)
    rank_key = (
        1 if is_related else 0,
        same_up,
        same_author,
        source_priority,
        len(matched_keywords),
        strong_match_count,
        overlap_score,
        same_partition,
        float(item.get("like_rate") or 0.0),
        safe_int(item.get("view")),
        -(float(item.get("competition_score") or 0.0)),
        item.get("title", ""),
    )
    return rank_key, {
        "is_related": is_related,
        "matched_terms": matched_terms,
        "matched_keywords": matched_keywords,
        "same_partition": same_partition,
        "source_priority": source_priority,
    }


# 为参考视频生成去重标识，避免同一条视频经不同来源重复出现在前端。
def build_reference_identity_keys(item: dict) -> list[str]:
    keys: list[str] = []
    bvid = (item.get("bvid") or "").strip().lower()
    url = (item.get("url") or "").strip()
    canonical_url = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")

    if bvid:
        keys.append(f"bvid:{bvid}")
    if canonical_url:
        keys.append(f"url:{canonical_url}")
    return keys


# 从候选集里筛出最适合前端展示的参考视频。
def select_reference_videos(
    sources: list[dict],
    exclude_bvid: str = "",
    limit: int = 6,
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    sources = enrich_reference_sources_with_search(sources, query_text=query_text, resolved=resolved)
    rough_entries = []
    entries = []
    candidate_seen: set[str] = set()
    view_floor = build_reference_view_floor(resolved)
    soft_view_floor = 50000
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    for item in sources:
        if not is_real_reference_video(item):
            continue
        identity_keys = build_reference_identity_keys(item)
        if identity_keys and any(key in candidate_seen for key in identity_keys):
            continue
        for key in identity_keys:
            candidate_seen.add(key)
        if strict_related_only:
            rough_rank, rough_meta = build_reference_rank_entry(item, query_text=query_text, resolved={})
            rough_entries.append((rough_rank, rough_meta, item))
            continue
        candidate = enrich_reference_video_for_display(item, require_semantics=False)
        rank_key, meta = build_reference_rank_entry(candidate, query_text=query_text, resolved=resolved)
        entries.append((rank_key, meta, candidate))

    if strict_related_only:
        for index, (_, rough_meta, item) in enumerate(sorted(rough_entries, key=lambda entry: entry[0], reverse=True)):
            source = item.get("source", "")
            should_enrich = bool(rough_meta.get("is_related")) or "当前视频相关推荐" in source or index < 8
            candidate = enrich_reference_video_for_display(item, require_semantics=should_enrich)
            rank_key, meta = build_reference_rank_entry(candidate, query_text=query_text, resolved=resolved)
            entries.append((rank_key, meta, candidate))

    ranked = sorted(entries, key=lambda entry: entry[0], reverse=True)
    result: list[dict] = []
    seen: set[str] = set()
    strong_related_pool = [
        item for _, meta, item in ranked if meta.get("is_related") and safe_int(item.get("view")) >= view_floor
    ]
    medium_related_pool = [
        item
        for _, meta, item in ranked
        if meta.get("is_related") and soft_view_floor <= safe_int(item.get("view")) < view_floor
    ]
    related_pool = [
        item for _, meta, item in ranked if meta.get("is_related") and safe_int(item.get("view")) < soft_view_floor
    ]
    fallback_high_pool = [
        item for _, meta, item in ranked if not meta.get("is_related") and safe_int(item.get("view")) >= soft_view_floor
    ]
    fallback_pool = [
        item for _, meta, item in ranked if not meta.get("is_related") and safe_int(item.get("view")) < soft_view_floor
    ]

    pools = (
        (strong_related_pool, medium_related_pool, related_pool)
        if strict_related_only
        else (strong_related_pool, medium_related_pool, fallback_high_pool, related_pool, fallback_pool)
    )

    def append_reference_card(item: dict) -> bool:
        candidate = enrich_reference_video_for_display(item)
        bvid = (candidate.get("bvid") or "").strip()
        url = (candidate.get("url") or "").strip()
        identity_keys = build_reference_identity_keys(candidate)
        if not url or any(key in seen for key in identity_keys):
            return False
        if exclude_bvid and bvid.lower() == exclude_bvid.lower():
            return False
        if not has_complete_reference_display_metrics(candidate):
            return False
        for key in identity_keys:
            seen.add(key)
        result.append(
            {
                "title": candidate.get("title", ""),
                "url": url,
                "author": candidate.get("author", ""),
                "cover": candidate.get("cover", ""),
                "view": safe_int(candidate.get("view")),
                "like": safe_optional_int(candidate.get("like")),
                "like_rate": float(candidate.get("like_rate") or 0.0),
                "source": candidate.get("source", ""),
            }
        )
        return len(result) >= limit

    for pool in pools:
        for item in pool:
            if append_reference_card(item):
                return result
    if strict_related_only and len(result) < limit:
        related_fallback_pool = [item for _, _, item in ranked if "当前视频相关推荐" in str(item.get("source") or "")]
        for item in related_fallback_pool:
            if append_reference_card(item):
                return result
    return result


# 从市场快照里提炼并筛出一组最终参考视频。
def build_reference_videos_from_market_snapshot(
    market_snapshot: dict,
    exclude_bvid: str = "",
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    resolved = resolved or {}
    entries: list[tuple[tuple, float, dict]] = []
    seen: set[str] = set()
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    target_partition = str(resolved.get("partition") or market_snapshot.get("partition") or "").strip()
    target_partition_label = (
        str(resolved.get("partition_label") or "")
        or str(market_snapshot.get("partition_label") or "")
        or PARTITION_LABELS.get(target_partition, target_partition)
    )

    for group_name in ("peer_samples", "hot_board", "partition_samples"):
        for raw_item in market_snapshot.get(group_name) or []:
            item = dict(raw_item or {})
            bvid = (item.get("bvid") or "").strip()
            url = (item.get("url") or "").strip()
            if exclude_bvid and bvid.lower() == exclude_bvid.lower():
                continue
            if not (title_or_url := ((item.get("title") or "").strip() or url)):
                continue
            if url and url in seen:
                continue
            if bvid and not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
                continue

            candidate = {
                "title": item.get("title", ""),
                "url": url or f"https://www.bilibili.com/video/{bvid}",
                "author": item.get("author", ""),
                "cover": item.get("cover", ""),
                "view": safe_int(item.get("view")),
                "like": safe_optional_int(item.get("like")),
                "like_rate": float(item.get("like_rate") or 0.0),
                "source": item.get("source", ""),
                "bvid": bvid,
                "partition": str(item.get("partition") or target_partition),
                "partition_label": str(item.get("partition_label") or target_partition_label),
                "topic": build_topic(title_or_url, keywords=extract_video_keywords([title_or_url]), tname=target_partition_label),
                "keywords": extract_video_keywords([title_or_url]),
            }
            rank_key, meta = build_reference_rank_entry(candidate, query_text=query_text, resolved=resolved)
            if strict_related_only and not meta.get("is_related"):
                continue
            source_bonus = 1.0 if group_name == "peer_samples" else 0.5 if group_name == "hot_board" else 0.0
            entries.append((rank_key, source_bonus, candidate))
            if candidate["url"]:
                seen.add(candidate["url"])

    ranked = sorted(entries, key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [
        {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "author": item.get("author", ""),
                "cover": item.get("cover", ""),
                "view": safe_int(item.get("view")),
                "like": safe_optional_int(item.get("like")),
                "like_rate": float(item.get("like_rate") or 0.0),
                "source": item.get("source", ""),
                "bvid": item.get("bvid", ""),
                "partition": item.get("partition", ""),
                "partition_label": item.get("partition_label", ""),
                "topic": item.get("topic", ""),
                "keywords": item.get("keywords", []),
            }
            for _, _, item in ranked[:6]
            if item.get("url")
        ]


def normalize_text_value(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n-_|，,。.;；")


def normalize_text_list(value: object, limit: int = 0) -> list[str]:
    raw_items: list[object] = []
    if isinstance(value, str):
        raw_items.extend(re.split(r"[\r\n]+|[；;]+", value))
    elif isinstance(value, (list, tuple, set)):
        raw_items.extend(list(value))
    elif value not in (None, ""):
        raw_items.append(value)

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = normalize_text_value(item)
        if not text:
            continue
        marker = text.lower()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(text)
        if limit > 0 and len(result) >= limit:
            break
    return result


def merge_text_lists(*values: object, limit: int = 0) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in normalize_text_list(value):
            marker = item.lower()
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
            if limit > 0 and len(result) >= limit:
                return result
    return result


def normalize_object_payload(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        candidates = [text]
        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}")
            if 0 <= start < end:
                candidates.append(text[start:end + 1])
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            if isinstance(payload, dict):
                return dict(payload)
        return {}
    if isinstance(value, (list, tuple)):
        try:
            payload = dict(value)
        except Exception:
            return {}
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def normalize_named_list_payload(value: object, target_key: str, limit: int = 0) -> dict:
    payload = normalize_object_payload(value)
    if payload:
        return payload
    texts = normalize_text_list(value, limit=limit)
    if not texts:
        return {}
    return {target_key: texts}


def normalize_bool_flag(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        clean = value.strip().lower()
        if clean in {"true", "1", "yes", "y", "是"}:
            return True
        if clean in {"false", "0", "no", "n", "否"}:
            return False
    if value in (None, ""):
        return default
    return bool(value)


def extract_retrieval_matches_from_tool_observations(observations: list[dict]) -> list[dict]:
    matches: list[dict] = []
    for item in observations or []:
        if not isinstance(item, dict) or item.get("action") != "retrieval":
            continue
        observation = item.get("observation")
        if not isinstance(observation, dict):
            continue
        for match in observation.get("matches") or []:
            if isinstance(match, dict):
                matches.append(match)
    return matches


def build_reference_video_from_knowledge_match(match: dict) -> dict | None:
    metadata = dict((match or {}).get("metadata") or {})
    text = str((match or {}).get("text") or "")
    title = normalize_text_value(metadata.get("title") or extract_knowledge_text_field(text, "视频标题"))
    url = normalize_text_value(metadata.get("url") or extract_knowledge_text_field(text, "链接"))
    bvid = normalize_text_value(metadata.get("bvid") or extract_knowledge_text_field(text, "BVID"))
    if not url and re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
        url = f"https://www.bilibili.com/video/{bvid}"
    if not title or not url:
        return None

    partition_text = normalize_text_value(metadata.get("partition") or extract_knowledge_text_field(text, "分区"))
    broad_partition = infer_knowledge_item_broad_partition({"metadata": metadata, "text": text}) or map_partition(
        partition_text,
        0,
        context_text=f"{title} {partition_text}",
    )
    view = safe_int(metadata.get("view") or extract_knowledge_text_field(text, "播放量"))
    like = safe_optional_int(metadata.get("like"))
    if like is None:
        like = safe_optional_int(extract_knowledge_text_field(text, "点赞量"))
    keywords = extract_video_keywords([title, partition_text, extract_knowledge_text_field(text, "评论热词")])
    board_type = normalize_text_value(metadata.get("board_type") or extract_knowledge_text_field(text, "榜单来源"))
    partition_label = PARTITION_LABELS.get(broad_partition, partition_text or broad_partition)
    return {
        "title": title,
        "url": url,
        "author": normalize_text_value(metadata.get("author") or extract_knowledge_text_field(text, "UP主")),
        "cover": normalize_text_value(metadata.get("cover")),
        "view": view,
        "like": like,
        "like_rate": float((like or 0) / max(view, 1)),
        "source": board_type or "知识库高表现样本",
        "bvid": bvid,
        "partition": broad_partition,
        "partition_label": partition_label,
        "topic": build_topic(title, keywords=keywords, tname=partition_text),
        "keywords": keywords,
        "_retrieval_score": float((match or {}).get("score") or 0.0),
    }


def build_reference_videos_from_retrieval_matches(
    matches: list[dict],
    exclude_bvid: str = "",
    query_text: str = "",
    resolved: dict | None = None,
    limit: int = 6,
) -> list[dict]:
    resolved = resolved or {}
    entries: list[tuple[tuple, float, dict]] = []
    seen: set[str] = set()
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    for match in matches or []:
        item = build_reference_video_from_knowledge_match(match)
        if not item:
            continue
        bvid = (item.get("bvid") or "").strip()
        if exclude_bvid and bvid.lower() == exclude_bvid.lower():
            continue
        identity = (item.get("url") or "").strip() or bvid
        if not identity or identity in seen:
            continue
        seen.add(identity)
        rank_key, meta = build_reference_rank_entry(item, query_text=query_text, resolved=resolved)
        if strict_related_only and not meta.get("is_related"):
            continue
        retrieval_bonus = -float(item.get("_retrieval_score") or 0.0)
        entries.append((rank_key, retrieval_bonus, item))

    ranked = sorted(entries, key=lambda entry: (entry[0], entry[1]), reverse=True)
    result: list[dict] = []
    for _, _, item in ranked[:limit]:
        result.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "author": item.get("author", ""),
                "cover": item.get("cover", ""),
                "view": safe_int(item.get("view")),
                "like": safe_optional_int(item.get("like")),
                "like_rate": float(item.get("like_rate") or 0.0),
                "source": item.get("source", ""),
                "bvid": item.get("bvid", ""),
                "partition": item.get("partition", ""),
                "partition_label": item.get("partition_label", ""),
                "topic": item.get("topic", ""),
                "keywords": item.get("keywords", []),
            }
        )
    return result


def build_module_analyze_reference_videos(
    market_snapshot: dict,
    tool_observations: list[dict] | None = None,
    exclude_bvid: str = "",
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    retrieval_matches = extract_retrieval_matches_from_tool_observations(tool_observations or [])
    retrieval_videos = build_reference_videos_from_retrieval_matches(
        retrieval_matches,
        exclude_bvid=exclude_bvid,
        query_text=query_text,
        resolved=resolved,
        limit=6,
    )
    market_videos = build_reference_videos_from_market_snapshot(
        market_snapshot,
        exclude_bvid=exclude_bvid,
        query_text=query_text,
        resolved=resolved,
    )

    merged: list[dict] = []
    seen: set[str] = set()
    for item in retrieval_videos + market_videos:
        identity_keys = build_reference_identity_keys(item)
        url = (item.get("url") or "").strip()
        if not url:
            continue
        if not identity_keys:
            identity_keys = [f"url:{url}"]
        if any(key in seen for key in identity_keys):
            continue
        for key in identity_keys:
            seen.add(key)
        merged.append(item)

    ranked_entries: list[tuple[tuple, dict]] = []
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    for item in merged:
        rank_key, meta = build_reference_rank_entry(item, query_text=query_text, resolved=resolved)
        if strict_related_only and not meta.get("is_related"):
            continue
        ranked_entries.append((rank_key, item))

    result: list[dict] = []
    for _, item in sorted(ranked_entries, key=lambda entry: entry[0], reverse=True):
        if not normalize_text_value(item.get("cover")):
            continue
        if not has_complete_reference_display_metrics(item):
            continue
        result.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "author": item.get("author", ""),
                "cover": item.get("cover", ""),
                "view": safe_int(item.get("view")),
                "like": safe_optional_int(item.get("like")),
                "like_rate": float(item.get("like_rate") or 0.0),
                "source": item.get("source", ""),
            }
        )
        if len(result) >= 6:
            return result
    if len(result) >= 6:
        return result[:6]

    backfill_sources = retrieval_videos + market_videos
    backfilled_videos = app_exports().select_reference_videos(
        backfill_sources,
        exclude_bvid=exclude_bvid,
        limit=6,
        query_text=query_text,
        resolved=resolved,
    )
    seen: set[str] = set()
    for item in result:
        for key in build_reference_identity_keys(item):
            seen.add(key)
    for item in backfilled_videos:
        identity_keys = build_reference_identity_keys(item)
        if identity_keys and any(key in seen for key in identity_keys):
            continue
        result.append(item)
        for key in identity_keys:
            seen.add(key)
        if len(result) >= 6:
            break
    return result


def infer_title_formula(title: str) -> str:
    text = normalize_text_value(title)
    if not text:
        return "具体场景 + 明确结果"
    if any(token in text for token in ("本来", "结果", "却", "居然", "反而", "不是", "别再", "翻车", "崩溃")):
        return "反差冲突 + 结果落点"
    if any(token in text for token in ("第一次", "终于", "这次", "今天", "刚刚", "昨晚")):
        return "时间场景 + 真实经历"
    if re.search(r"\d", text):
        return "数字信息 + 具体对象 + 结果承诺"
    if any(token in text for token in ("情侣", "异地恋", "约会", "日常", "vlog", "情绪", "见面")):
        return "人物关系 + 场景细节 + 情绪反应"
    return "具体场景 + 核心看点 + 情绪结果"


def build_partition_playbook(resolved: dict, performance: dict) -> dict:
    partition = str(resolved.get("partition") or "").strip()
    if partition == "knowledge":
        return {
            "rhythm": ["前 3 秒先抛结论或反常识点，中段再补证据和案例。", "中段按 2-3 个明确小结论推进，避免背景铺垫过长。", "结尾用取舍建议或下一条延展问题承接评论区。"],
            "structure": ["标题先给结果，再补充具体对象或使用场景。", "内容结构优先用“结论 -> 证据 -> 对比 -> 结尾动作”组织。", "封面突出核心名词和结果词，不要把说明文字堆满。"],
            "openings": ["先说最终结论，再补一句为什么这次值得看。", "第一句直接抛最容易踩坑的地方。", "开场 1 句话先把“误区/差异/结果”说透。"],
            "middle": ["第 1 段先给最直观的证据或案例。", "第 2 段补反例、对比或成本差异。", "第 3 段收束成可直接执行的动作。"],
            "ending": ["结尾引导观众补充自己踩过的坑。", "可顺带预告下一条更细的实测或分支场景。"],
            "publish_windows": ["工作日 19:00-22:00", "周末 10:00-12:00"],
            "color_scheme": "高对比黄黑或蓝白，突出“结果词 + 核心对象”。",
            "coin": True,
            "comment_guides": ["你最想让我继续拆哪一种具体场景？", "评论区告诉我你现在最卡在哪一步。"],
        }
    if partition in {"life", "ent"}:
        return {
            "rhythm": ["开场直接上情绪点、人物关系或最有代入感的画面。", "中段按时间线推进，每 8-12 秒给一次情绪或画面变化。", "结尾留下选择题、站队点或续集伏笔。"],
            "structure": ["标题优先写人物关系、当天场景和情绪结果。", "内容结构用“开场钩子 -> 两段核心画面 -> 互动结尾”最稳。", "封面文案控制在 6-10 个字，人物表情和动作要先于环境。"],
            "openings": ["先给见面、反差或情绪最满的一幕。", "第一句直接说今天最有感觉的那个瞬间。", "开头先给“本来以为...结果...”的反差。"],
            "middle": ["中段第一段推进核心场景。", "第二段补细节特写、人物反应或关系变化。", "镜头切换跟着情绪走，不要平铺叙事。"],
            "ending": ["结尾丢给观众一个代入式问题。", "顺手埋一个下次还会继续拍的续集点。"],
            "publish_windows": ["工作日 18:30-22:30", "周末 14:00-18:00"],
            "color_scheme": "暖色调橙红或奶白 + 高饱和点缀，突出人物和情绪。",
            "coin": False,
            "comment_guides": ["如果是你，你会怎么选？", "评论区告诉我你最想看下一次拍哪一段。"],
        }
    if partition == "game":
        return {
            "rhythm": ["先上高光或翻车瞬间，中段再解释过程。", "每一段都要有明显结果反馈，避免空讲。", "结尾抛版本、打法或下一局想看什么。"],
            "structure": ["标题把版本答案、高光或反差写在前面。", "中段按“高光 -> 过程 -> 复盘”组织。", "封面优先突出角色、装备或关键场面。"],
            "openings": ["开场先丢最炸的一幕。", "第一句直接说这局最离谱的点。"],
            "middle": ["中段快速回放关键决策。", "补充对手反应、失误或翻盘细节。"],
            "ending": ["结尾引导观众站队打法。", "可预告下一期版本或角色测试。"],
            "publish_windows": ["工作日 18:00-23:00", "周末 12:00-18:00"],
            "color_scheme": "深底高对比荧光色，突出角色或战斗瞬间。",
            "coin": True,
            "comment_guides": ["你觉得这波最关键的点在哪？", "下条想看我继续测哪套打法？"],
        }
    return {
        "rhythm": ["开场先给最强结果或最大反差，中段再补上下文。", "中段每一段只讲一个重点，避免信息挤在一起。", "结尾给互动问题或下一条续集点。"],
        "structure": ["标题先给具体场景，再补结果或情绪。", "内容结构优先保持“钩子 -> 递进 -> 互动收束”。", "封面文案要短，主体元素比背景更重要。"],
        "openings": ["前 3 秒先放最能留人的一句话或画面。", "开头先交代这条内容最值的那个点。"],
        "middle": ["中段第一段推进核心内容。", "第二段补充细节、对比或情绪变化。"],
        "ending": ["结尾主动引导观众留言自己的经历。", "顺手埋下集方向，给评论区继续互动理由。"],
        "publish_windows": ["工作日 19:00-22:00", "周末 10:00-12:00"],
        "color_scheme": "高对比主色 + 一个强调色，保证主体和文案可读性。",
        "coin": not bool(performance.get("is_hot")),
        "comment_guides": ["你最想继续看哪一部分？", "评论区说说你自己的真实体验。"],
    }


def build_default_title_sets(resolved: dict, performance: dict) -> dict:
    base_topic = normalize_text_value(resolved.get("topic") or resolved.get("title") or resolved.get("partition_label") or "这条内容")
    base_topic = base_topic[:18] or "这条内容"
    is_hot = bool(performance.get("is_hot"))
    short_titles = [
        f"{base_topic}这次终于拍顺了",
        f"{base_topic}这条开头更容易把人留下",
        f"{base_topic}这一版更有代入感",
    ]
    long_titles = [
        f"把{base_topic}最有感觉的一段直接放到前面，这条更容易让人看到最后",
        f"同样是拍{base_topic}，这一版先给结果再讲过程，留人会更稳",
        f"{base_topic}这次不靠堆信息，先把最能共鸣的画面放出来",
    ]
    conflict_titles = [
        f"本来以为{base_topic}会很普通，结果开头这一幕最先把人留下",
        f"不是{base_topic}没人看，是第一眼还没把冲突和情绪抛出来",
        f"{base_topic}最容易拍散的地方，恰好就是这条最该放大的爆点",
    ]
    if is_hot:
        short_titles[0] = f"{base_topic}这一版已经很有爆点了"
        long_titles[0] = f"{base_topic}这条之所以容易起量，是因为最强画面一上来就先给到了"
    return {
        "short_titles": normalize_text_list(short_titles, limit=3),
        "long_titles": normalize_text_list(long_titles, limit=3),
        "conflict_titles": normalize_text_list(conflict_titles, limit=3),
    }


def build_default_cover_plan(resolved: dict, title_sets: dict, playbook: dict) -> dict:
    keywords = extract_video_keywords(resolved.get("keywords"))
    hero = keywords[0] if keywords else normalize_text_value(resolved.get("topic") or resolved.get("partition_label") or "核心画面")
    secondary = keywords[1] if len(keywords) > 1 else "情绪反应"
    short_titles = normalize_text_list((title_sets or {}).get("short_titles"), limit=2)
    return {
        "copy_lines": merge_text_lists(short_titles, [f"{hero}这段最有感觉", f"{hero}别先铺背景"], limit=3),
        "layout_advice": [
            "主文案放左上或右上，控制在 6-10 个字内，别挡住人物表情和主体动作。",
            "主体人物或核心物体放在画面右侧 2/3 区域，背景只保留能说明场景的关键信息。",
            "优先突出表情、动作、结果画面，不要平均分配画面信息。",
        ],
        "color_scheme": normalize_text_value(playbook.get("color_scheme")) or "高对比主色 + 一个强调色，先保证可读性。",
        "highlight_elements": normalize_text_list([hero, secondary, resolved.get("partition_label"), "人物表情", "结果画面"], limit=4),
    }


def build_default_tag_strategy(resolved: dict, benchmark_videos: list[dict]) -> dict:
    keywords = extract_video_keywords(resolved.get("keywords"))
    partition_label = normalize_text_value(resolved.get("partition_label") or PARTITION_LABELS.get(resolved.get("partition", ""), ""))
    hot_terms = []
    for item in benchmark_videos[:3]:
        hot_terms.extend(extract_reference_terms(item.get("title", ""))[:4])
    hot_tags = []
    for term in hot_terms:
        candidate = normalize_text_value(term)
        if len(candidate) < 2 or candidate in hot_tags:
            continue
        hot_tags.append(candidate)
        if len(hot_tags) >= 4:
            break
    core_traffic = merge_text_lists(keywords[:3], [partition_label, normalize_text_value(resolved.get("topic"))], limit=4)
    vertical = merge_text_lists([partition_label, resolved.get("style")], keywords[3:6], limit=4)
    hot = merge_text_lists(hot_tags, ["同赛道爆款", "高表现拆解"], limit=4)
    recommended = merge_text_lists(core_traffic, vertical, hot, limit=8)
    return {
        "core_traffic_tags": core_traffic,
        "vertical_tags": vertical,
        "hot_tags": hot,
        "recommended_tags": recommended,
    }


def build_default_publish_strategy(resolved: dict, performance: dict, playbook: dict) -> dict:
    partition_label = normalize_text_value(resolved.get("partition_label") or PARTITION_LABELS.get(resolved.get("partition", ""), "当前赛道"))
    should_ask_for_coin = normalize_bool_flag(playbook.get("coin"), default=not bool(performance.get("is_hot")))
    return {
        "best_publish_windows": normalize_text_list(playbook.get("publish_windows"), limit=3),
        "should_ask_for_coin": should_ask_for_coin,
        "coin_call_to_action": (
            "结尾可以弱引导“如果这条对你有帮助，先收藏投币，我继续补下一条同题材拆解”。"
            if should_ask_for_coin
            else "这类内容更适合优先引导评论和收藏，投币引导放轻一些。"
        ),
        "suggested_comment_guides": merge_text_lists(
            playbook.get("comment_guides"),
            [f"评论区告诉我你还想看哪一种{partition_label}场景。", "如果是你，你会先保留哪一个镜头或观点？"],
            limit=3,
        ),
    }


def build_default_reusable_hit_points(
    resolved: dict,
    benchmark_videos: list[dict],
    playbook: dict,
    performance: dict,
) -> list[str]:
    formulas = [infer_title_formula(item.get("title", "")) for item in benchmark_videos[:3] if item.get("title")]
    lead_formula = formulas[0] if formulas else "具体场景 + 结果落点"
    partition_label = normalize_text_value(resolved.get("partition_label") or PARTITION_LABELS.get(resolved.get("partition", ""), "当前赛道"))
    points = [
        f"同赛道高表现内容普遍把「{lead_formula}」放进标题，而不是先讲空泛背景。",
        "开头先给结果、反差或情绪最高的一幕，再补过程，留人会更稳。",
        "中段只推进 2-3 个核心信息点，每一段都要有新的画面或结果反馈。",
        "结尾不要硬收，最好留一个评论问题或续集承接点。",
        f"封面和标签都要继续围绕「{partition_label} + 核心对象 + 情绪/结果词」来组织。",
    ]
    if performance.get("is_hot"):
        points[0] = f"这条视频已经踩中了「{lead_formula}」这类点击公式，下一条重点是放大而不是换赛道。"
    return normalize_text_list(points, limit=5)


def build_default_analysis_payload(
    resolved: dict,
    performance: dict,
    topic_result: dict,
    optimize_result: dict,
    reference_videos: list[dict],
) -> dict:
    playbook = build_partition_playbook(resolved, performance)
    title_sets = build_default_title_sets(resolved, performance)
    cover_plan = build_default_cover_plan(resolved, title_sets, playbook)
    benchmark_videos = [dict(item or {}) for item in reference_videos[:3]]
    benchmark_formulas = normalize_text_list([infer_title_formula(item.get("title", "")) for item in benchmark_videos], limit=3)
    benchmark_analysis = {
        "benchmark_videos": benchmark_videos,
        "common_title_formulas": merge_text_lists(benchmark_formulas, ["具体场景 + 明确结果", "反差或情绪词 + 关键对象"], limit=3),
        "common_rhythm_formulas": normalize_text_list(playbook.get("rhythm"), limit=3),
        "common_structure_formulas": normalize_text_list(playbook.get("structure"), limit=3),
    }
    remake_script_structure = {
        "opening_hooks": normalize_text_list(playbook.get("openings"), limit=3),
        "middle_rhythm": normalize_text_list(playbook.get("middle"), limit=3),
        "ending_interactions": normalize_text_list(playbook.get("ending"), limit=3),
    }
    tag_strategy = build_default_tag_strategy(resolved, benchmark_videos)
    publish_strategy = build_default_publish_strategy(resolved, performance, playbook)
    reusable_hit_points = build_default_reusable_hit_points(resolved, benchmark_videos, playbook, performance)
    title_suggestions = merge_text_lists(
        title_sets.get("short_titles"),
        title_sets.get("conflict_titles"),
        optimize_result.get("optimized_titles"),
        limit=3,
    )
    cover_suggestion = "；".join(
        part
        for part in [
            " / ".join(normalize_text_list(cover_plan.get("copy_lines"), limit=2)),
            normalize_text_list(cover_plan.get("layout_advice"), limit=1)[0] if normalize_text_list(cover_plan.get("layout_advice"), limit=1) else "",
            normalize_text_value(cover_plan.get("color_scheme")),
        ]
        if part
    )
    content_suggestions = merge_text_lists(
        [f"开头钩子：{item}" for item in remake_script_structure.get("opening_hooks", [])[:1]],
        [f"中段节奏：{item}" for item in remake_script_structure.get("middle_rhythm", [])[:2]],
        [f"结尾互动：{item}" for item in remake_script_structure.get("ending_interactions", [])[:1]],
        [f"发布时间：{' / '.join(publish_strategy.get('best_publish_windows') or [])}"],
        [f"评论引导：{(publish_strategy.get('suggested_comment_guides') or [''])[0]}"],
        optimize_result.get("content_suggestions"),
        limit=5,
    )
    analysis_points = merge_text_lists(
        performance.get("reasons"),
        [
            f"同赛道参考标题更常见的写法是「{(benchmark_analysis.get('common_title_formulas') or [''])[0]}」。",
            f"更稳的内容节奏通常是「{(remake_script_structure.get('opening_hooks') or [''])[0]} -> {(remake_script_structure.get('middle_rhythm') or [''])[0]} -> {(remake_script_structure.get('ending_interactions') or [''])[0]}」。",
        ],
        reusable_hit_points[:2],
        limit=8,
    )
    analysis_payload = {
        "analysis_points": analysis_points,
        "benchmark_analysis": benchmark_analysis,
        "remake_script_structure": remake_script_structure,
        "advanced_title_sets": title_sets,
        "cover_plan": cover_plan,
        "tag_strategy": tag_strategy,
        "publish_strategy": publish_strategy,
        "reusable_hit_points": reusable_hit_points,
        "title_suggestions": title_suggestions,
        "cover_suggestion": cover_suggestion,
        "content_suggestions": content_suggestions,
    }
    followup_topics = normalize_analysis_topics(topic_result, resolved.get("title", ""), limit=3)
    if performance.get("is_hot"):
        analysis_payload["followup_topics"] = followup_topics
    else:
        analysis_payload["next_topics"] = followup_topics
    return analysis_payload


def normalize_module_performance_payload(performance: object, resolved: dict) -> dict:
    baseline = classify_video_performance(resolved)
    if not isinstance(performance, dict):
        return baseline

    normalized = normalize_performance_payload(performance)
    normalized_reasons = merge_text_lists(normalized.get("reasons"), baseline.get("reasons"), limit=8)
    normalized_summary = normalize_text_value(normalized.get("summary")) or normalize_text_value(baseline.get("summary"))
    normalized_label = normalize_text_value(normalized.get("label")) or normalize_text_value(baseline.get("label"))

    is_default_pending = (
        safe_int(normalized.get("score")) <= 50
        and not normalize_text_list(normalized.get("reasons"))
        and not normalize_text_value(normalized.get("summary"))
    )
    if is_default_pending or bool(normalized.get("is_hot")) != bool(baseline.get("is_hot")):
        trusted = dict(baseline)
        trusted["reasons"] = normalized_reasons
        trusted["summary"] = normalized_summary or baseline.get("summary", "")
        trusted["label"] = normalized_label or baseline.get("label", "")
        return trusted

    normalized["reasons"] = normalized_reasons
    normalized["summary"] = normalized_summary
    normalized["label"] = normalized_label
    return normalized


def normalize_module_analysis_payload(
    result: dict,
    *,
    resolved: dict,
    performance: dict,
    topic_result: dict,
    optimize_result: dict,
    reference_videos: list[dict],
) -> dict:
    analysis_raw = result.get("analysis")
    analysis = normalize_object_payload(analysis_raw)
    defaults = build_default_analysis_payload(resolved, performance, topic_result, optimize_result, reference_videos)
    benchmark_defaults = defaults.get("benchmark_analysis") or {}
    script_defaults = defaults.get("remake_script_structure") or {}
    title_defaults = defaults.get("advanced_title_sets") or {}
    cover_defaults = defaults.get("cover_plan") or {}
    tag_defaults = defaults.get("tag_strategy") or {}
    publish_defaults = defaults.get("publish_strategy") or {}

    benchmark_analysis = normalize_named_list_payload(analysis.get("benchmark_analysis"), "common_structure_formulas", limit=3)
    benchmark_analysis["benchmark_videos"] = reference_videos[:3]
    benchmark_analysis["common_title_formulas"] = merge_text_lists(
        benchmark_analysis.get("common_title_formulas"),
        benchmark_defaults.get("common_title_formulas"),
        limit=3,
    )
    benchmark_analysis["common_rhythm_formulas"] = merge_text_lists(
        benchmark_analysis.get("common_rhythm_formulas"),
        benchmark_defaults.get("common_rhythm_formulas"),
        limit=3,
    )
    benchmark_analysis["common_structure_formulas"] = merge_text_lists(
        benchmark_analysis.get("common_structure_formulas"),
        benchmark_defaults.get("common_structure_formulas"),
        limit=3,
    )

    remake_script_structure = normalize_named_list_payload(analysis.get("remake_script_structure"), "middle_rhythm", limit=3)
    remake_script_structure["opening_hooks"] = merge_text_lists(
        remake_script_structure.get("opening_hooks"),
        script_defaults.get("opening_hooks"),
        limit=3,
    )
    remake_script_structure["middle_rhythm"] = merge_text_lists(
        remake_script_structure.get("middle_rhythm"),
        script_defaults.get("middle_rhythm"),
        limit=3,
    )
    remake_script_structure["ending_interactions"] = merge_text_lists(
        remake_script_structure.get("ending_interactions"),
        script_defaults.get("ending_interactions"),
        limit=3,
    )

    advanced_title_sets = normalize_named_list_payload(analysis.get("advanced_title_sets"), "short_titles", limit=3)
    advanced_title_sets["short_titles"] = merge_text_lists(
        advanced_title_sets.get("short_titles"),
        title_defaults.get("short_titles"),
        limit=3,
    )
    advanced_title_sets["long_titles"] = merge_text_lists(
        advanced_title_sets.get("long_titles"),
        title_defaults.get("long_titles"),
        limit=3,
    )
    advanced_title_sets["conflict_titles"] = merge_text_lists(
        advanced_title_sets.get("conflict_titles"),
        title_defaults.get("conflict_titles"),
        limit=3,
    )

    cover_plan = normalize_named_list_payload(analysis.get("cover_plan"), "copy_lines", limit=3)
    cover_plan["copy_lines"] = merge_text_lists(cover_plan.get("copy_lines"), cover_defaults.get("copy_lines"), limit=3)
    cover_plan["layout_advice"] = merge_text_lists(
        cover_plan.get("layout_advice"),
        cover_defaults.get("layout_advice"),
        limit=3,
    )
    cover_plan["color_scheme"] = normalize_text_value(cover_plan.get("color_scheme")) or normalize_text_value(
        cover_defaults.get("color_scheme")
    )
    cover_plan["highlight_elements"] = merge_text_lists(
        cover_plan.get("highlight_elements"),
        cover_defaults.get("highlight_elements"),
        limit=4,
    )

    tag_strategy = normalize_named_list_payload(analysis.get("tag_strategy"), "recommended_tags", limit=8)
    tag_strategy["core_traffic_tags"] = merge_text_lists(
        tag_strategy.get("core_traffic_tags"),
        tag_defaults.get("core_traffic_tags"),
        limit=4,
    )
    tag_strategy["vertical_tags"] = merge_text_lists(
        tag_strategy.get("vertical_tags"),
        tag_defaults.get("vertical_tags"),
        limit=4,
    )
    tag_strategy["hot_tags"] = merge_text_lists(tag_strategy.get("hot_tags"), tag_defaults.get("hot_tags"), limit=4)
    tag_strategy["recommended_tags"] = merge_text_lists(
        tag_strategy.get("recommended_tags"),
        tag_strategy.get("core_traffic_tags"),
        tag_strategy.get("vertical_tags"),
        tag_strategy.get("hot_tags"),
        limit=8,
    )

    publish_strategy = normalize_named_list_payload(analysis.get("publish_strategy"), "suggested_comment_guides", limit=3)
    publish_strategy["best_publish_windows"] = merge_text_lists(
        publish_strategy.get("best_publish_windows"),
        publish_defaults.get("best_publish_windows"),
        limit=3,
    )
    publish_strategy["should_ask_for_coin"] = normalize_bool_flag(
        publish_strategy.get("should_ask_for_coin"),
        default=normalize_bool_flag(publish_defaults.get("should_ask_for_coin")),
    )
    publish_strategy["coin_call_to_action"] = normalize_text_value(
        publish_strategy.get("coin_call_to_action")
    ) or normalize_text_value(publish_defaults.get("coin_call_to_action"))
    publish_strategy["suggested_comment_guides"] = merge_text_lists(
        publish_strategy.get("suggested_comment_guides"),
        publish_defaults.get("suggested_comment_guides"),
        limit=3,
    )

    reusable_hit_points = merge_text_lists(analysis.get("reusable_hit_points"), defaults.get("reusable_hit_points"), limit=5)
    title_suggestions = merge_text_lists(
        analysis.get("title_suggestions"),
        advanced_title_sets.get("short_titles"),
        advanced_title_sets.get("conflict_titles"),
        optimize_result.get("optimized_titles"),
        limit=3,
    )
    cover_suggestion = normalize_text_value(analysis.get("cover_suggestion")) or defaults.get("cover_suggestion", "")
    content_suggestions = merge_text_lists(
        analysis.get("content_suggestions"),
        defaults.get("content_suggestions"),
        limit=5,
    )
    analysis_points = merge_text_lists(
        analysis.get("analysis_points"),
        normalize_text_list(analysis_raw, limit=3) if not analysis else [],
        defaults.get("analysis_points"),
        limit=8,
    )

    normalized_analysis = {
        "analysis_points": analysis_points,
        "benchmark_analysis": benchmark_analysis,
        "remake_script_structure": remake_script_structure,
        "advanced_title_sets": advanced_title_sets,
        "cover_plan": cover_plan,
        "tag_strategy": tag_strategy,
        "publish_strategy": publish_strategy,
        "reusable_hit_points": reusable_hit_points,
        "title_suggestions": title_suggestions,
        "cover_suggestion": cover_suggestion,
        "content_suggestions": content_suggestions,
    }
    if performance.get("is_hot"):
        normalized_analysis["followup_topics"] = merge_text_lists(
            analysis.get("followup_topics"),
            defaults.get("followup_topics"),
            limit=3,
        )
    else:
        normalized_analysis["next_topics"] = merge_text_lists(
            analysis.get("next_topics"),
            defaults.get("next_topics"),
            limit=3,
        )
    return normalized_analysis


def build_reference_videos_notice(reference_videos: list[dict], market_snapshot: dict) -> str:
    if reference_videos:
        return ""

    peer_samples = market_snapshot.get("peer_samples") if isinstance(market_snapshot, dict) else []
    source_count = safe_int((market_snapshot or {}).get("source_count")) if isinstance(market_snapshot, dict) else 0
    if peer_samples or source_count:
        return "当前题材公开可用的对标样本不足，暂未整理出可直接展示的参考视频。"
    return "暂时无法获取对标样本，请稍后重试。"


def get_prefetched_market_snapshot(
    market_snapshot_future,
    resolved: dict,
    timeout_seconds: float | None,
) -> tuple[dict, str]:
    fallback_snapshot = build_empty_market_snapshot(resolved.get("partition", ""))
    if not market_snapshot_future:
        return fallback_snapshot, ""
    try:
        if timeout_seconds is None:
            snapshot = market_snapshot_future.result()
        else:
            snapshot = market_snapshot_future.result(timeout=max(float(timeout_seconds or 0.0), 0.0))
    except FutureTimeoutError:
        return fallback_snapshot, "pending"
    except Exception:
        return fallback_snapshot, "error"
    if isinstance(snapshot, dict):
        return snapshot, ""
    return fallback_snapshot, "error"


# 从 Agent 工具调用记录里提取可直接展示的参考视频链接。
def extract_reference_links_from_tool_observations(
    observations: list[dict],
    exclude_bvid: str = "",
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    sources: list[dict] = []
    query_parts = [query_text]
    for item in observations or []:
        observation = item.get("observation") if isinstance(item, dict) else {}
        if not isinstance(observation, dict):
            continue
        query_parts.append(extract_reference_query_from_observation(observation))
        if isinstance(observation.get("market_snapshot"), dict):
            sources.extend(
                (
                    observation.get("market_snapshot", {}).get("peer_samples") or []
                )
                + (
                    observation.get("market_snapshot", {}).get("partition_samples") or []
                )
                + (
                    observation.get("market_snapshot", {}).get("hot_board") or []
                )
            )
        for key in ("hot_board", "peer_samples", "partition_samples"):
            value = observation.get(key)
            if isinstance(value, list):
                sources.extend(value)
    return app_exports().select_reference_videos(
        sources,
        exclude_bvid=exclude_bvid,
        limit=6,
        query_text=" ".join(part for part in query_parts if part),
        resolved=resolved,
    )


# 把视频详情整理成更适合 LLM 分析的视频输入结构。
