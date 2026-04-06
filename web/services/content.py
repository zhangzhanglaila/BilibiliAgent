from __future__ import annotations

from importlib import import_module

from web.services.runtime import *


# 延迟加载 web.app 模块，用于解决循环导入问题。
def app_exports():
    return import_module("web.app")


# 对音乐参考类文本做清洗，去除特殊符号并转小写，便于关键词匹配。
def _normalize_reference_text_for_music(text: str) -> str:
    value = re.sub(r"[【】\[\]（）()<>《》\"'`~!@#$%^&*_+=|\\/:;,.?？！，。、“”·-]+", " ", text or "")
    return re.sub(r"\s+", " ", value).strip().lower()


# 判断给定文本是否像是音乐类内容引用（包含音乐相关关键词）。
def looks_like_music_reference(text: str) -> bool:
    normalized = _normalize_reference_text_for_music(text)
    return bool(normalized) and any(token in normalized for token in MUSIC_REFERENCE_KEYWORDS)


# 解码 HTTP 响应体，支持 gzip/deflate 解压，返回 UTF-8 文本。
def decode_http_response_body(raw_body: bytes, content_encoding: str = "", charset: str = "utf-8") -> str:
    body = raw_body or b""
    encoding = str(content_encoding or "").lower().strip()
    try:
        if "gzip" in encoding or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        elif "deflate" in encoding:
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception:
        pass
    return body.decode(charset or "utf-8", errors="ignore")


# 发起 HTTP 请求并返回文本响应内容。
def fetch_text(url: str, timeout: int = 10) -> str:
    request_obj = Request(url, headers=DEFAULT_HEADERS)
    with app_exports().urlopen(request_obj, timeout=timeout) as response:
        charset = ""
        try:
            charset = response.headers.get_content_charset() or ""
        except Exception:
            charset = ""
        return decode_http_response_body(
            response.read(),
            content_encoding=response.headers.get("Content-Encoding", ""),
            charset=charset or "utf-8",
        )


# 请求 JSON 接口并校验返回体是字典结构。
def fetch_json(url: str) -> dict:
    payload = json.loads(fetch_text(url))
    if not isinstance(payload, dict):
        raise ValueError("B站接口返回了无效数据")
    return payload


# 展开 b23.tv 这类短链，得到最终跳转后的完整链接。
def resolve_short_link(url: str) -> str:
    if not url or not any(host in url for host in SHORT_LINK_HOSTS):
        return url

    request_obj = Request(url, headers=DEFAULT_HEADERS)
    try:
        with app_exports().urlopen(request_obj, timeout=8) as response:
            return response.geturl()
    except Exception:
        return url


# 从一段文本里直接提取可用的 B 站视频 URL。
def extract_embedded_bili_video_url(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    match = re.search(r"https?://(?:www\.)?bilibili\.com/video/[^\s\"'<>]+", raw, flags=re.IGNORECASE)
    return match.group(0) if match else ""


# 从一段查询参数文本里递归拆出被编码的 B 站视频 URL。
def decode_embedded_bili_video_url(text: str, depth: int = 0) -> str:
    if depth > 2:
        return ""

    raw = str(text or "")
    if not raw:
        return ""

    direct_url = extract_embedded_bili_video_url(raw)
    if direct_url:
        return direct_url

    decoded = unquote(raw)
    if decoded != raw:
        nested_url = decode_embedded_bili_video_url(decoded, depth + 1)
        if nested_url:
            return nested_url

    for token in re.findall(r"[A-Za-z0-9+/=]{24,}", raw):
        for suffix in ("", "=", "==", "==="):
            try:
                decoded_text = b64decode(token + suffix, validate=False).decode("utf-8", errors="ignore")
            except Exception:
                continue
            nested_url = decode_embedded_bili_video_url(decoded_text, depth + 1)
            if nested_url:
                return nested_url
    return ""


# 从追踪类链接的查询参数里提取真实视频地址。
def resolve_embedded_bili_video_url(url: str) -> str:
    raw_url = (url or "").strip()
    if not raw_url:
        return ""

    parsed = urlparse(raw_url)
    host = (parsed.netloc or "").lower()
    if not host:
        return ""

    values = [value for _, value in parse_qsl(parsed.query, keep_blank_values=True)]
    if parsed.fragment:
        values.append(parsed.fragment)

    for value in values:
        nested_url = decode_embedded_bili_video_url(value)
        if nested_url:
            return nested_url

    if any(host.endswith(tracking_host) for tracking_host in TRACKING_LINK_HOSTS):
        nested_url = decode_embedded_bili_video_url(raw_url)
        if nested_url:
            return nested_url
    return ""


# 根据 av 号调用公开视频接口换取标准 BV 号。
def resolve_bvid_from_aid(aid: int) -> str:
    if aid <= 0:
        raise ValueError("B站 av 链接解析失败: 无效 av 号")

    try:
        bvid = str(video.aid2bvid(aid)).strip()
        if re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
            return "BV" + bvid[2:]
    except Exception:
        pass

    payload = fetch_json(f"https://api.bilibili.com/x/web-interface/view?{urlencode({'aid': aid})}")
    if payload.get("code") != 0:
        message = payload.get("message") or payload.get("msg") or "official api failed"
        raise ValueError(f"B站 av 链接解析失败: {message}")
    data = payload.get("data") or {}
    bvid = str(data.get("bvid") or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
        raise ValueError("B站 av 链接解析失败: 未返回有效 BV 号")
    return "BV" + bvid[2:]


# 从任意 B 站视频链接里提取标准 BV 号；支持 BV、av 和短链。
def extract_bvid(url: str) -> str:
    raw_url = (url or "").strip()
    candidate = resolve_short_link(raw_url)

    embedded_url = resolve_embedded_bili_video_url(candidate)
    if embedded_url and embedded_url != candidate:
        return extract_bvid(embedded_url)

    match = re.search(r"(BV[0-9A-Za-z]{10})", candidate, flags=re.IGNORECASE)
    if match:
        value = match.group(1)
        return "BV" + value[2:]

    av_match = re.search(r"(?:^|/)(?:av)(\d+)(?:/|$|[?#])", candidate, flags=re.IGNORECASE)
    if av_match:
        return resolve_bvid_from_aid(safe_int(av_match.group(1)))

    aid_match = re.search(r"[?&]aid=(\d+)", candidate, flags=re.IGNORECASE)
    if aid_match:
        return resolve_bvid_from_aid(safe_int(aid_match.group(1)))

    if any(host in raw_url for host in SHORT_LINK_HOSTS):
        raise ValueError("短链接展开失败，请改用包含 BV 号或 av 号的完整视频链接重试")
    raise ValueError("未识别到有效的 B 站视频 BV 号或 av 号")


# 把 B 站原始分区信息映射成项目内部统一使用的分区标识。
def map_partition(tname: str, tid: int, context_text: str = "") -> str:
    text = f"{tname or ''} {context_text or ''}".lower()
    if any(keyword in text for keyword in ["知识", "科普", "学习", "校园", "职业"]):
        return "knowledge"
    if any(keyword in text for keyword in ["科技", "数码", "软件", "计算机", "程序"]):
        return "tech"
    if any(keyword in text for keyword in ["游戏", "电竞"]):
        return "game"
    if looks_like_music_reference(text):
        return "ent"
    if any(keyword in text for keyword in ["舞蹈", "卡点", "变速卡点", "热舞", "变装", "颜值", "美女", "身材", "小姐姐"]):
        return "ent"
    if any(keyword in text for keyword in LIFE_CONTENT_KEYWORDS):
        return "life"
    if any(keyword in text for keyword in ["娱乐", "影视", "综艺", "明星", "音乐"]):
        return "ent"

    if tid in {36, 201, 208, 209, 229}:
        return "knowledge"
    if tid in {95, 122, 124}:
        return "tech"
    if tid in {4, 17, 65, 136, 172}:
        return "game"
    if tid in {21, 76, 138, 160, 214}:
        return "life"
    if tid in {5, 71, 137, 181, 255}:
        return "ent"
    return "knowledge"


# 根据标题和分区特征猜测更适合的内容风格。
def guess_style(title: str, partition: str, tname: str, context_text: str = "") -> str:
    text = f"{title} {tname} {context_text}".lower()
    if any(keyword in text for keyword in ["教程", "教学", "保姆级", "入门", "攻略", "怎么", "如何"]):
        return "教学"
    if any(keyword in text for keyword in ["搞笑", "整活", "沙雕", "鬼畜", "吐槽", "抽象"]):
        return "搞笑"
    if any(keyword in text for keyword in ["混剪", "高燃", "踩点", "mad", "剪辑", "卡点", "变速卡点", "舞蹈", "热舞"]):
        return "混剪"
    if partition == "game" and "攻略" in text:
        return "教学"
    return "干货"


# 统一补齐公开接口里偶尔缺失的 tname，避免后续分区判断和参考视频召回过度依赖原标题。
def normalize_video_tname(tname: str, tid: int, keywords: object = None, title: str = "") -> str:
    clean_tname = normalize_creator_text(tname or "")
    if clean_tname:
        return clean_tname

    hinted_tname = VIDEO_TNAME_HINTS.get(safe_int(tid), "")
    if hinted_tname:
        return hinted_tname

    keyword_list = extract_video_keywords(keywords)
    text = f"{title} {' '.join(keyword_list)}".lower()
    if looks_like_music_reference(text):
        return "音乐"
    if any(token in text for token in SEA_HARVEST_KEYWORDS):
        return "赶海"
    if any(token in text for token in ["颜值", "美女", "身材", "小姐姐", "变装"]):
        return "颜值"
    if any(token in text for token in ["变速卡点", "卡点", "舞蹈", "热舞"]):
        return "舞蹈"
    return clean_tname


# 从视频标题里提炼出更适合作为分析主题的短文本。
def build_topic(title: str, keywords: list[str] | None = None, tname: str = "", tid: int = 0) -> str:
    cleaned = re.sub(r"[【\[].*?[】\]]", "", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    keyword_list = extract_video_keywords(keywords)
    normalized_tname = normalize_video_tname(tname, tid, keyword_list, cleaned or title or "")
    text = f"{cleaned} {normalized_tname} {' '.join(keyword_list)}".lower()
    sea_target = next((token for token in SEA_HARVEST_TARGET_KEYWORDS if token in text), "")

    if any(keyword in text for keyword in ["颜值", "美女", "身材", "小姐姐", "变装"]) and any(
        keyword in text for keyword in ["变速卡点", "卡点", "舞蹈", "热舞"]
    ):
        return "颜值卡点展示"

    if any(keyword in text for keyword in ["变速卡点", "舞蹈", "热舞"]):
        return "变速卡点舞蹈展示"
    if any(keyword in text for keyword in ["颜值", "美女", "身材", "小姐姐", "变装"]):
        return "颜值展示视频"
    if any(keyword in text for keyword in SEA_HARVEST_KEYWORDS):
        return f"赶海捡到{sea_target}" if sea_target and sea_target != "海货" else "赶海收获记录"
    if any(keyword in text for keyword in ["异地恋", "情侣", "约会", "vlog", "日常"]):
        return cleaned or "情侣日常记录"
    return (cleaned or title or "B站内容选题拆解").strip()


# 把创作者输入的领域、方向和想法合成为一个种子主题。
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


# 统一清洗创作者输入文本，去掉多余分隔符和空白。
def normalize_creator_text(text: str) -> str:
    value = re.sub(r"[/|｜]+", " ", text or "")
    value = re.sub(r"\s+", " ", value).strip(" -_|，,。.;；:")
    return value


# 对创作方向做额外归一化，处理一些项目里常见的表述别名。
def normalize_creator_direction(direction: str, idea: str) -> str:
    value = normalize_creator_text(direction)
    combined = f"{value} {idea}"
    if "擦边" in value and any(token in combined for token in ["跳", "舞", "舞蹈"]):
        value = value.replace("美女擦边", "颜值舞蹈").replace("擦边", "颜值向")
    elif "擦边" in value:
        value = value.replace("美女擦边", "颜值向内容").replace("擦边", "高点击表达")
    return normalize_creator_text(value)


# 把领域和方向合并成一个更稳定的创作者画像描述。
def merge_creator_profile(field_name: str, direction: str) -> str:
    if field_name and direction:
        if field_name in direction:
            return direction
        if direction in field_name:
            return field_name
        return f"{field_name}{direction}"
    return field_name or direction


# 进一步修正创作者画像，处理颜值向、舞蹈向等特殊场景。
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


# 从文本前缀中剥离已经在上下文里表达过的重复信息。
def strip_leading_context(text: str, contexts: list[str]) -> str:
    result = text
    for context in contexts:
        if not context:
            continue
        if result.startswith(context):
            result = result[len(context):].strip(" ，,。.;；:")
    return result


# 判断创作者关键词是否为噪音词（太短、是停用词、含数字等）。
def is_creator_keyword_noise(keyword: str, strict: bool = False) -> bool:
    if not keyword or len(keyword) < 2:
        return True
    if keyword in REFERENCE_STOPWORDS:
        return True
    if any(char.isdigit() for char in keyword):
        return True
    if strict and re.fullmatch(r"[\u4e00-\u9fff]+", keyword) and len(keyword) > 6:
        return True
    if strict and re.fullmatch(r"[a-z]+", keyword) and len(keyword) <= 3:
        return True
    return False


# 从创作者输入中抽取关键词，供趋势聚合和题目拼装使用。
def extract_creator_keywords(text: str, strict: bool = False) -> list[str]:
    clean = normalize_creator_text(text).lower()
    for fragment in CREATOR_KEYWORD_NOISE_FRAGMENTS:
        clean = clean.replace(fragment, " ")
    clean = re.sub(r"(?<=[A-Za-z])(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])(?=[A-Za-z])", " ", clean)
    clean = re.sub(r"(?<=[0-9])(?=[A-Za-z\u4e00-\u9fff])|(?<=[A-Za-z\u4e00-\u9fff])(?=[0-9])", " ", clean)
    words = re.findall(r"[\u4e00-\u9fff]{2,12}|[A-Za-z]{2,16}", clean)
    keywords: list[str] = []
    for word in words:
        if is_creator_keyword_noise(word, strict=strict):
            continue
        if word not in keywords:
            keywords.append(word)
    return keywords


# 清洗单个创作者关键词，去除冗余修饰词（助词、量词前缀、句尾词），返回干净文本。
def clean_creator_keyword(keyword: str) -> str:
    clean = normalize_creator_text(keyword)
    clean = re.sub(r"^[的地得把被让跟与和在从向给将]+", "", clean)
    clean = re.sub(r"^(一段|一处|一个|一条|一种|一版|新的|固定更新的)", "", clean)
    clean = re.sub(r"(里面|当中|相关|这种|这一条|这条)$", "", clean)
    return normalize_creator_text(clean)


# 合并多组创作者关键词列表，去重后返回（最多8个）。
def merge_creator_keywords(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for keyword in group:
            clean = clean_creator_keyword(keyword)
            if clean and clean not in merged:
                merged.append(clean)
    pruned: list[str] = []
    for keyword in merged:
        if any(existing != keyword and existing in keyword for existing in pruned):
            continue
        pruned = [existing for existing in pruned if not (keyword != existing and keyword in existing)]
        pruned.append(keyword)
    return pruned


# 从多段文本中提取创作者上下文关键词，合并去重后最多返回8个。
def build_creator_context_keywords(*texts: str) -> list[str]:
    return merge_creator_keywords(*[extract_creator_keywords(text) for text in texts if text])[:8]


# 从给定主题文本推断创作者的核心方向（情感/知识/细节/日常等）。
def infer_creator_topic_focus(topic: str) -> str:
    text = normalize_creator_text(topic)
    if any(token in text for token in ["两性", "情感", "恋爱", "情侣", "夫妻", "婚姻", "坦白局", "私密", "伴侣", "相处"]):
        return "emotion"
    if any(token in text for token in ["科普", "原理", "误区", "解析", "解读", "真相", "为什么"]):
        return "knowledge"
    if any(token in text for token in ["细节", "小事", "瞬间", "情绪", "共鸣"]):
        return "detail"
    if any(token in text for token in ["日常", "片段", "一段", "记录", "场景"]):
        return "scene"
    if any(token in text for token in ["对比", "前后", "变化", "差异"]):
        return "contrast"
    return "general"


# 根据主题方向返回适合的选题切入视角描述（如"关系视角"、"问题拆解"等）。
def select_creator_topic_cue(topic: str) -> str:
    focus = infer_creator_topic_focus(topic)
    mapping = {
        "emotion": "关系视角",
        "knowledge": "问题拆解",
        "detail": "细节放大",
        "scene": "真实场景",
        "contrast": "体验对照",
        "general": "核心切口",
    }
    return mapping.get(focus, "核心切口")


# 判断单个关键词是否匹配创作者上下文（完全相等或包含关系）。
def keyword_matches_creator_context(keyword: str, context_keywords: list[str]) -> bool:
    if not context_keywords:
        return True
    return any(keyword == context or keyword in context or context in keyword for context in context_keywords)


# 判断视频标题是否匹配创作者上下文（标题或其关键词是否与上下文相关）。
def title_matches_creator_context(title: str, title_keywords: list[str], context_keywords: list[str]) -> bool:
    if not context_keywords:
        return bool(title_keywords)
    normalized_title = normalize_creator_text(title).lower()
    if any(context in normalized_title for context in context_keywords):
        return True
    return any(keyword_matches_creator_context(keyword, context_keywords) for keyword in title_keywords)


# 从样本视频标题里聚合出当前方向更常见的趋势关键词。
def collect_creator_trending_keywords(
    videos: list[dict],
    partition_name: str,
    context_keywords: list[str] | None = None,
) -> list[str]:
    context_keywords = context_keywords or []
    counts: dict[str, int] = {}
    for item in videos[:18]:
        title = item.get("title", "")
        normalized_title = normalize_creator_text(title).lower()
        for context_keyword in context_keywords:
            if context_keyword and context_keyword not in REFERENCE_STOPWORDS and context_keyword in normalized_title:
                counts[context_keyword] = counts.get(context_keyword, 0) + 1
        title_keywords = extract_creator_keywords(title, strict=True)
        if not title_matches_creator_context(title, title_keywords, context_keywords):
            continue
        for keyword in title_keywords:
            if context_keywords and not keyword_matches_creator_context(keyword, context_keywords):
                continue
            counts[keyword] = counts.get(keyword, 0) + 1

    if counts:
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        filtered = [
            keyword
            for keyword, count in ranked
            if count > 1 or keyword_matches_creator_context(keyword, context_keywords)
        ]
        if filtered:
            return filtered[:4]
    return CREATOR_PARTITION_ANGLES.get(partition_name, CREATOR_PARTITION_ANGLES["knowledge"])[:3]


# 为创作者选题结果生成一段“为什么推荐这个方向”的解释。
def build_creator_reason(
    topic: str,
    partition_name: str,
    source_count: int,
    trending_keywords: list[str],
    angle_label: str,
    index: int = 0,
) -> str:
    partition_label = PARTITION_LABELS.get(partition_name, partition_name)
    keyword_text = "、".join(trending_keywords[:3]) if trending_keywords else CREATOR_REASON_FALLBACKS.get(
        partition_name, "真实体验、信息增量、互动讨论"
    )
    focus = infer_creator_topic_focus(topic)
    lead_templates = [
        f"从当前{partition_label}分区的 {source_count} 条热点 / 样本回看，「{keyword_text}」这类切口最近更容易被点开和讨论。",
        f"这一批{partition_label}题材里，表现更稳的内容大多都在抓「{keyword_text}」这类观众感知很强的点。",
        f"按当前{partition_label}分区样本回看，观众停留得更久的内容，通常都能把「{keyword_text}」讲得更具体。",
    ]
    focus_lines = {
        "emotion": "这一条更适合从双方感受、关系边界或相处卡点切进去，观众会更容易把自己的经历代进去。",
        "knowledge": "这一条适合把一个常见误区拆开讲，再用真实场景把结论落地，信息价值会更清楚。",
        "scene": "这一条适合先把场景和人物状态立住，让观众先看懂发生了什么，再自然进入后面的情绪或观点。",
        "detail": "这一条重点不在铺大逻辑，而是把一个会被反复提起的小细节单独讲透。",
        "contrast": "这一条更适合把两种感受或两种结果直接并排摆出来，观众更容易立刻形成判断。",
        "general": "这一条先抓住一个明确问题或具体处境，会比泛泛总结更容易建立讨论氛围。",
    }
    angle_templates = [
        f"表达上先往「{angle_label}」去组织，会比平铺直叙更容易测出反馈。",
        f"第一轮测试建议把重点压在「{angle_label}」这层，不用一次塞太多信息。",
        f"如果先做规则模式测试，这条更建议往「{angle_label}」这个方向去落。",
    ]
    lead = lead_templates[index % len(lead_templates)]
    angle = angle_templates[index % len(angle_templates)]
    return f"{lead}{focus_lines.get(focus, focus_lines['general'])}{angle}"


# 把基础选题结果改写成更贴近创作者输入语境的结果结构。
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
    angle_labels = CREATOR_PARTITION_ANGLES.get(normalized_partition, CREATOR_PARTITION_ANGLES["knowledge"])
    question_topic = seed_topic or profile or normalize_creator_text(idea) or "这类内容第一条该怎么做"
    context_keywords = build_creator_context_keywords(field_name, direction, idea, profile, seed_topic, question_topic)
    trending_keywords = collect_creator_trending_keywords(videos, normalized_partition, context_keywords=context_keywords)

    raw_ideas = [item for item in (base_topic_result.get("ideas") or []) if isinstance(item, dict)]
    if not raw_ideas and question_topic:
        raw_ideas = [
            {
                "topic": topic,
                "reason": "",
                "video_type": style or "干货",
                "keywords": [],
                "score": 100 - index * 3,
            }
            for index, (_, topic) in enumerate(RAW_TOPIC_AGENT._build_seed_candidates(question_topic))
        ]

    ideas = []
    for index, raw_idea in enumerate(raw_ideas[:3]):
        topic = normalize_creator_text(str(raw_idea.get("topic") or "")) or question_topic
        angle_label = angle_labels[index % len(angle_labels)]
        topic_cue = select_creator_topic_cue(topic)
        idea_keywords = merge_creator_keywords(
            context_keywords[:2],
            [topic_cue],
            trending_keywords[:1],
            [angle_label],
        )[:5]
        ideas.append(
            {
                "topic": topic,
                "reason": build_creator_reason(topic, normalized_partition, source_count, trending_keywords, angle_label, index),
                "video_type": raw_idea.get("video_type") or style or "干货",
                "keywords": idea_keywords,
                "score": float(raw_idea.get("score") or (100 - index * 3)),
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


# 在大段文本里找到某个标记后面的 JSON 对象片段。
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


# 从 HTML 的 meta 标签里提取指定属性的 content。
def extract_meta(html: str, attr_name: str, attr_value: str) -> str:
    pattern = rf'<meta[^>]+{attr_name}="{re.escape(attr_value)}"[^>]+content="([^"]*)"'
    match = re.search(pattern, html, flags=re.IGNORECASE)
    return unescape(match.group(1)).strip() if match else ""


# 把关键词输入统一展开成字符串片段，避免列表被直接序列化成 "['xx']" 这种脏文本。
def iter_video_keyword_fragments(raw: object) -> list[str]:
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        fragments: list[str] = []
        for key in ("tag_name", "keyword", "name", "title", "text"):
            value = raw.get(key)
            if value not in (None, ""):
                fragments.extend(iter_video_keyword_fragments(value))
        if fragments:
            return fragments
        return [str(value) for value in raw.values() if value not in (None, "")]
    if isinstance(raw, (list, tuple, set)):
        fragments: list[str] = []
        for item in raw:
            fragments.extend(iter_video_keyword_fragments(item))
        return fragments
    return [str(raw)]


# 从页面关键词或标签文本里抽取可用于内容判断的一组关键词。
def extract_video_keywords(raw: object) -> list[str]:
    keywords: list[str] = []
    for fragment in iter_video_keyword_fragments(raw):
        parts = re.split(r"[,，/|｜]+", str(fragment or ""))
        for part in parts:
            clean = normalize_creator_text(part).strip("[](){}'\"")
            clean = normalize_creator_text(clean)
            if len(clean) < 2:
                continue
            lowered = clean.lower()
            if lowered in VIDEO_KEYWORD_STOPWORDS or clean in keywords:
                continue
            keywords.append(clean)
            if len(keywords) >= 8:
                return keywords
    return keywords[:8]


# 按正则提取第一个匹配结果并做 HTML 反转义。
def extract_first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return unescape(match.group(1)).strip() if match else ""


# 从 B 站页面源码里提取 __INITIAL_STATE__ 初始化数据。
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


# 把 HTML 页面和初始化状态里的信息整理成统一视频信息结构。
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
        "keywords": extract_video_keywords(extract_meta(html, "name", "keywords")),
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


# 通过公开视频标签接口补充题材关键词。
def fetch_video_tags(bvid: str) -> list[str]:
    clean_bvid = (bvid or "").strip()
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", clean_bvid, flags=re.IGNORECASE):
        return []

    payload = fetch_json(f"https://api.bilibili.com/x/tag/archive/tags?{urlencode({'bvid': clean_bvid})}")
    if safe_int(payload.get("code")) != 0:
        return []

    data = payload.get("data")
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    tags: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        tag_name = normalize_creator_text(item.get("tag_name") or "")
        if len(tag_name) < 2 or tag_name in tags:
            continue
        tags.append(tag_name)
    return tags[:8]


# 当公开视频接口缺少有效分区线索时，再从页面关键词补一层语义信息。
def enrich_video_info_with_html_hints(info: dict, url: str, bvid: str) -> dict:
    enriched = dict(info or {})
    existing_keywords = extract_video_keywords(enriched.get("keywords"))
    try:
        tag_keywords = fetch_video_tags(bvid)
    except Exception:
        tag_keywords = []
    merged_keywords: list[str] = []
    for keyword in existing_keywords + tag_keywords:
        if keyword not in merged_keywords:
            merged_keywords.append(keyword)
    if str(enriched.get("tname") or "").strip():
        enriched["keywords"] = merged_keywords[:8]
        return enriched

    try:
        html_info = app_exports().fetch_video_info_via_html(url, bvid)
    except Exception:
        enriched["keywords"] = merged_keywords[:8]
        return enriched

    for keyword in extract_video_keywords(html_info.get("keywords")):
        if keyword not in merged_keywords:
            merged_keywords.append(keyword)

    enriched["keywords"] = merged_keywords[:8]
    if not enriched.get("tname") and html_info.get("tname"):
        enriched["tname"] = html_info.get("tname")
    return enriched


# 公开视频主信息已拿到时，补充字段失败不能反过来拖垮整个解析。
def enrich_video_info_best_effort(info: dict, url: str, bvid: str) -> dict:
    base_info = dict(info or {})
    try:
        return app_exports().enrich_video_info_with_html_hints(base_info, url, bvid)
    except Exception:
        return base_info


# 通过 B 站公开视频接口拉取视频详情。
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


# 在 API 不可用时，通过页面源码解析视频详情。
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


# 按多级回退策略拉取视频信息，尽量保证解析成功。
def fetch_video_info(url: str, bvid: str) -> dict:
    exports = app_exports()
    errors: list[str] = []

    # 先走结构化程度最高的来源，失败后再逐级回退到库调用和 HTML 解析。
    try:
        info = exports.fetch_video_info_via_public_api(bvid)
        return enrich_video_info_best_effort(info, url, bvid)
    except Exception as exc:
        errors.append(f"public api: {exc}")

    try:
        info = sync(video.Video(bvid=bvid).get_info())
        return enrich_video_info_best_effort(info, url, bvid)
    except Exception as exc:
        errors.append(f"bilibili_api: {exc}")

    try:
        return exports.fetch_video_info_via_html(url, bvid)
    except Exception as exc:
        errors.append(f"html: {exc}")

    raise ValueError("；".join(errors))


# 仅用于前端预览的快速解析链路；优先返回足够展示基础信息的结果，避免被慢接口超时拖住。
def fetch_video_preview_info(url: str, bvid: str) -> dict:
    exports = app_exports()
    errors: list[str] = []

    try:
        return exports.fetch_video_info_via_html(url, bvid)
    except Exception as exc:
        errors.append(f"html fast path: {exc}")

    try:
        return exports.fetch_video_info_via_public_api(bvid)
    except Exception as exc:
        errors.append(f"public api: {exc}")

    try:
        return sync(video.Video(bvid=bvid).get_info())
    except Exception as exc:
        errors.append(f"bilibili_api: {exc}")

    raise ValueError("；".join(errors))


# 从视频详情里抽取前端和分析链路要用的核心指标。
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


# 把原始视频详情整理成项目内部统一的 resolved 结构。
def build_resolved_payload(info: dict, bvid: str) -> dict:
    owner = info.get("owner", {})
    mid = safe_int(owner.get("mid"))
    up_name = owner.get("name") or owner.get("uname") or ""
    title = info.get("title", "")
    tid = safe_int(info.get("tid"))
    keywords = extract_video_keywords(info.get("keywords"))
    tname = normalize_video_tname(info.get("tname", ""), tid, keywords, title)
    context_text = " ".join([title, tname, *keywords])
    stats = extract_video_stats(info)
    partition = map_partition(tname, tid, context_text=context_text)
    topic = build_topic(title, keywords=keywords, tname=tname, tid=tid)
    style = guess_style(title, partition, tname, context_text=" ".join(keywords))
    partition_label = PARTITION_LABELS.get(partition, partition)

    # 不管上游信息来自哪个渠道，这里都整理成前端和两条分析链路共用的统一结构。
    return {
        "resolved_version": RESOLVED_PAYLOAD_VERSION,
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
        "keywords": keywords,
        "topic": topic,
        "style": style,
        "duration": safe_int(info.get("duration")),
        "stats": stats,
        "summary": f"{up_name or '未知UP'} · {tname or partition_label}",
    }


# 从链接出发完成 BV 提取、视频信息解析和统一结构构建。
def resolve_video_payload(url: str) -> dict:
    bvid = extract_bvid(url)
    info = fetch_video_info(url, bvid)
    return build_resolved_payload(info, bvid)


# 判断前端缓存的 resolved 结果是否还能用于当前链接。
def is_resolved_payload_usable(payload: object, url: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if safe_int(payload.get("resolved_version")) < RESOLVED_PAYLOAD_VERSION:
        return False
    bv_id = str(payload.get("bv_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    stats = payload.get("stats")
    partition = str(payload.get("partition") or "").strip()
    topic = str(payload.get("topic") or "").strip()
    style = str(payload.get("style") or "").strip()
    if not bv_id or not title or not isinstance(stats, dict) or not partition or not topic or not style:
        return False

    try:
        expected_bv = extract_bvid(url)
    except Exception:
        expected_bv = ""

    # 前端会缓存一次解析结果，用户继续改链接时要及时丢掉已经过期的 resolved 数据。
    return not expected_bv or bv_id.upper() == expected_bv.upper()


# 从标题文本本身提炼出几个强弱点分析结论。
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


# 把不同链路产出的分数统一映射到前端展示使用的分数区间。
def uplift_performance_score(raw_score: object, is_hot: bool) -> int:
    try:
        value = float(raw_score or 0)
    except Exception:
        value = 0.0

    value = max(0.0, value)
    floor = 82 if is_hot else 50

    # 某些链路可能还会返回 0-5 这种粗粒度分数，这里统一抬到前端使用的分数带。
    if value <= 5:
        step = 3 if is_hot else 4
        return min(96, floor + int(round(value * step)))

    # 也兼容 0-10 这类中间分数带。
    if value <= 10:
        step = 1.4 if is_hot else 2.0
        return min(96, floor + int(round(value * step)))

    # 如果已经是 0-100，就直接沿用，但仍然保留热门/低表现各自的下限语义。
    return min(96, max(floor, int(round(value))))


# 统一整理视频表现判断结果，补齐默认字段。
def normalize_performance_payload(performance: object) -> dict:
    if not isinstance(performance, dict):
        return {
            "label": "待判断",
            "is_hot": False,
            "score": 50,
            "reasons": [],
            "summary": "",
        }

    is_hot = bool(performance.get("is_hot"))
    normalized = dict(performance)
    normalized["score"] = uplift_performance_score(performance.get("score"), is_hot)
    normalized.setdefault("label", "热门爆款" if is_hot else "播放偏低")
    normalized.setdefault("reasons", [])
    normalized.setdefault("summary", "")
    return normalized


# 用规则方式根据播放、点赞、投币、收藏等指标判断视频表现。
def classify_video_performance(resolved: dict) -> dict:
    stats = resolved.get("stats", {})
    view = safe_int(stats.get("view"))
    favorite = safe_int(stats.get("favorite"))
    like_rate = float(stats.get("like_rate") or 0.0)
    coin_rate = float(stats.get("coin_rate") or 0.0)
    favorite_rate = float(stats.get("favorite_rate") or 0.0)

    score = 50
    reasons: list[str] = []

    if view >= 500000:
        score += 18
        reasons.append(f"当前播放 {view:,}，已经是明显爆款量级。")
    elif view >= 200000:
        score += 16
        reasons.append(f"当前播放 {view:,}，已经具备很强的自然放大能力。")
    elif view >= 100000:
        score += 14
        reasons.append(f"当前播放 {view:,}，已经达到明显起量水平。")
    elif view >= 50000:
        score += 11
        reasons.append(f"当前播放 {view:,}，处于比较健康的流量区间。")
    elif view >= 20000:
        score += 8
        reasons.append(f"当前播放 {view:,}，有一定自然流量基础。")
    elif view >= 10000:
        score += 5
        reasons.append(f"当前播放 {view:,}，已经有基础曝光，但离更大放量还有距离。")
    elif view >= 3000:
        score += 2
        reasons.append(f"当前播放 {view:,}，还处于早期验证阶段。")
    else:
        reasons.append(f"当前播放 {view:,}，整体曝光偏弱，仍有明显提升空间。")

    if favorite_rate >= 0.03 or favorite >= 5000:
        score += 16
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，说明内容留存价值非常强。")
    elif favorite_rate >= 0.02 or favorite >= 2000:
        score += 13
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，内容具备较强复用价值。")
    elif favorite_rate >= 0.012 or favorite >= 800:
        score += 10
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，收藏表现已经不错。")
    elif favorite_rate >= 0.008 or favorite >= 300:
        score += 7
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，内容开始体现留存价值。")
    elif favorite_rate >= 0.004 or favorite >= 100:
        score += 4
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，有一定收藏价值，但还不够强。")
    elif favorite_rate >= 0.002 or favorite >= 30:
        score += 2
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，留存价值偏弱。")
    else:
        reasons.append(f"收藏 {favorite:,}、收藏率 {favorite_rate:.2%}，说明内容的留存价值还不够突出。")

    if like_rate >= 0.08:
        score += 8
        reasons.append(f"点赞率 {like_rate:.2%}，互动质量很高。")
    elif like_rate >= 0.05:
        score += 6
        reasons.append(f"点赞率 {like_rate:.2%}，互动质量较高。")
    elif like_rate >= 0.03:
        score += 4
        reasons.append(f"点赞率 {like_rate:.2%}，基本达到可继续放大的水平。")
    elif like_rate >= 0.015:
        score += 2
        reasons.append(f"点赞率 {like_rate:.2%}，基础互动尚可。")
    else:
        reasons.append(f"点赞率 {like_rate:.2%}，说明内容共鸣还不够强。")

    if coin_rate >= 0.008:
        score += 4
        reasons.append(f"投币率 {coin_rate:.2%}，用户认可度较高。")
    elif coin_rate >= 0.005:
        score += 3
        reasons.append(f"投币率 {coin_rate:.2%}，有一定深度认可。")
    elif coin_rate >= 0.002:
        score += 1
        reasons.append(f"投币率 {coin_rate:.2%}，有少量高意愿互动。")
    else:
        reasons.append(f"投币率 {coin_rate:.2%}，深度认可仍然偏弱。")

    score = min(96, max(50, score))
    is_hot = score >= 82
    return normalize_performance_payload(
        {
        "label": "热门爆款" if is_hot else "播放偏低",
        "is_hot": is_hot,
        "score": score,
        "reasons": reasons,
        "summary": "这条视频更接近热门爆款，可重点拆解它为什么能火。"
        if is_hot
        else "这条视频当前更像播放偏低的视频，优先做针对性优化。",
        }
    )


# 为热门视频生成“为什么会火”和“还能怎么延展”的分析结果。
def build_hot_analysis(resolved: dict, performance: dict, topic_result: dict) -> dict:
    stats = resolved.get("stats", {})
    followup_topics = normalize_analysis_topics(topic_result, resolved.get("title", ""), limit=3)
    analysis_points = performance["reasons"] + inspect_title_strength(resolved.get("title", ""))
    analysis_points.append(
        f"当前分区为 {resolved.get('partition_label', resolved.get('partition', '未知分区'))}，"
        f"说明视频题材与该分区受众存在较高匹配度。"
    )
    if followup_topics:
        analysis_points.append("围绕当前视频继续延展，仍然有可继续放大的选题空间。")
    return {
        "analysis_points": analysis_points,
        "followup_topics": followup_topics,
    }


# 为低表现视频生成“哪里弱”和“下一步怎么改”的分析结果。
def build_low_performance_analysis(resolved: dict, performance: dict, optimize_result: dict, topic_result: dict) -> dict:
    next_topics = normalize_analysis_topics(topic_result, resolved.get("title", ""), limit=3)
    return {
        "analysis_points": performance["reasons"] + [optimize_result.get("diagnosis", "")],
        "next_topics": next_topics,
        "title_suggestions": optimize_result.get("optimized_titles", [])[:2],
        "cover_suggestion": optimize_result.get("cover_suggestion", ""),
        "content_suggestions": optimize_result.get("content_suggestions", [])[:5],
    }


# 对分析页里展示的后续题材做去重和基础清洗，避免重复项或与原题完全相同。
def normalize_analysis_topics(topic_result: dict, current_title: str = "", limit: int = 3) -> list[str]:
    ideas = topic_result.get("ideas", []) if isinstance(topic_result, dict) else []
    current_norm = normalize_creator_text(current_title).lower()
    result: list[str] = []
    seen: set[str] = set()

    for item in ideas:
        if not isinstance(item, dict):
            continue
        topic = normalize_creator_text(str(item.get("topic") or ""))
        if not topic:
            continue
        topic_norm = topic.lower()
        if topic_norm in seen:
            continue
        if current_norm and topic_norm == current_norm:
            continue
        if current_norm and current_norm in topic_norm and any(token in topic_norm for token in ANALYSIS_TOPIC_BAD_TAILS):
            continue
        seen.add(topic_norm)
        result.append(topic)
        if len(result) >= limit:
            break
    return result


# 构建当前运行模式信息，供前端初始化页面状态。
