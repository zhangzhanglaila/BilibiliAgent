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

from agents.copywriting_agent import CopywritingAgent
from agents.llm_workspace_agent import AgentTool, LLMWorkspaceAgent
from agents.optimization_agent import OptimizationAgent
from agents.topic_agent import TopicAgent
from config import CONFIG
from llm_client import LLMClient, format_llm_error, llm_error_http_status, should_skip_same_provider_fallback
from main import run_copy, run_operate, run_optimize, run_pipeline, run_topic
from models import to_plain_data

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
CREATOR_KEYWORD_NOISE_FRAGMENTS = (
    "第一条视频",
    "第1条",
    "第2条",
    "第3条",
    "做成系列内容时",
    "做系列内容时",
    "先做什么",
    "先拍什么",
    "先跳什么",
    "更容易起量",
    "更容易进推荐",
    "分别拍什么",
    "分别跳什么",
    "别直接硬拍",
    "别一上来就",
    "先做哪种切口",
    "哪种切口",
    "开场动作",
    "前三秒先放什么",
    "镜头顺序",
)
REFERENCE_STOPWORDS = CREATOR_STOPWORDS | {
    "这个",
    "那个",
    "这条",
    "本期",
    "今天",
    "最近",
    "系列",
    "合集",
    "完整版",
    "原创",
    "更新",
    "日常",
    "记录",
    "分享",
    "推荐",
    "实拍",
    "作品",
    "内容",
    "视频",
    "高表现",
    "爆款",
    "参考",
}
QUESTION_TOKENS = ("怎么", "如何", "为什么", "应该", "什么", "哪种", "哪类", "能不能")
RUNTIME_MODE_LABELS = {
    "rules": "无 Key 逻辑模式",
    "llm_agent": "LLM Agent 模式",
}

RAW_TOPIC_AGENT = TopicAgent()
RAW_COPY_AGENT = CopywritingAgent()
LLM_WORKSPACE_AGENT: LLMWorkspaceAgent | None = None
LLM_WORKSPACE_SIGNATURE: tuple[str, ...] | None = None
RUNTIME_LLM_ENABLED = CONFIG.llm_enabled()
RUNTIME_LLM_CONFIG: dict[str, str] | None = (
    {
        "provider": (CONFIG.llm_provider or "openai").strip() or "openai",
        "base_url": (CONFIG.llm_base_url or "").strip(),
        "api_key": (CONFIG.llm_api_key or "").strip(),
        "model": (CONFIG.llm_model or "gpt-5.4").strip() or "gpt-5.4",
        "source": "env",
    }
    if CONFIG.llm_enabled()
    else None
)
REFERENCE_VIDEO_DETAIL_CACHE: dict[str, dict] = {}


# 判断当前是否已经保存了可用于启用 LLM Agent 的运行时配置。
def has_saved_runtime_llm_config() -> bool:
    return bool((RUNTIME_LLM_CONFIG or {}).get("api_key", "").strip())


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
    global LLM_WORKSPACE_AGENT, LLM_WORKSPACE_SIGNATURE
    LLM_WORKSPACE_AGENT = None
    LLM_WORKSPACE_SIGNATURE = None


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
    fallback = build_fallback_copy_payload(clean_topic, clean_style)

    if not isinstance(copy_result, dict):
        return fallback

    titles = RAW_COPY_AGENT._normalize_titles(copy_result.get("titles"), fallback["titles"])

    script_raw = copy_result.get("script")
    script: list[dict] = []
    if isinstance(script_raw, list):
        for item in script_raw:
            if not isinstance(item, dict):
                continue
            content = clean_copy_text(item.get("content", ""))
            if not content:
                continue
            script.append(
                {
                    "section": clean_copy_text(item.get("section", "")) or "片段",
                    "duration": clean_copy_text(item.get("duration", "")),
                    "content": content,
                }
            )
    if len(script) < 4:
        script = fallback["script"]

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


# 发起 HTTP 请求并返回文本响应内容。
def fetch_text(url: str, timeout: int = 10) -> str:
    request_obj = Request(url, headers=DEFAULT_HEADERS)
    with urlopen(request_obj, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


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
        with urlopen(request_obj, timeout=8) as response:
            return response.geturl()
    except Exception:
        return url


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


# 根据标题和分区特征猜测更适合的内容风格。
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


# 从视频标题里提炼出更适合作为分析主题的短文本。
def build_topic(title: str) -> str:
    cleaned = re.sub(r"[【\[].*?[】\]]", "", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
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


def clean_creator_keyword(keyword: str) -> str:
    clean = normalize_creator_text(keyword)
    clean = re.sub(r"^[的地得把被让跟与和在从向给将]+", "", clean)
    clean = re.sub(r"^(一段|一处|一个|一条|一种|一版|新的|固定更新的)", "", clean)
    clean = re.sub(r"(里面|当中|相关|这种|这一条|这条)$", "", clean)
    return normalize_creator_text(clean)


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


def build_creator_context_keywords(*texts: str) -> list[str]:
    return merge_creator_keywords(*[extract_creator_keywords(text) for text in texts if text])[:8]


def infer_creator_topic_focus(topic: str) -> str:
    text = normalize_creator_text(topic)
    if any(token in text for token in ["拆成", "三条", "三段", "连续", "固定更新", "系列"]):
        return "series"
    if any(token in text for token in ["细节", "小事", "瞬间", "情绪", "共鸣"]):
        return "detail"
    if any(token in text for token in ["日常", "片段", "一段", "记录"]):
        return "scene"
    if any(token in text for token in ["对比", "前后", "变化"]):
        return "contrast"
    return "general"


def select_creator_topic_cue(topic: str) -> str:
    focus = infer_creator_topic_focus(topic)
    mapping = {
        "series": "连续更新",
        "detail": "细节放大",
        "scene": "日常片段",
        "contrast": "前后对比",
        "general": "具体场景",
    }
    return mapping.get(focus, "具体场景")


def keyword_matches_creator_context(keyword: str, context_keywords: list[str]) -> bool:
    if not context_keywords:
        return True
    return any(keyword == context or keyword in context or context in keyword for context in context_keywords)


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
    keyword_text = "、".join(trending_keywords[:3]) if trending_keywords else "开场反差、结果感、互动点"
    focus = infer_creator_topic_focus(topic)
    lead_templates = [
        f"从当前{partition_label}分区的 {source_count} 条热点 / 样本看，「{keyword_text}」这类结构最近更容易把人留住。",
        f"这一批{partition_label}题材里，跑得更好的内容大多都带着「{keyword_text}」这类表达特征。",
        f"按当前{partition_label}分区样本回看，观众更容易停留在「{keyword_text}」这种信息组织方式上。",
    ]
    focus_lines = {
        "scene": f"这一条适合先抓一个最容易代入的生活片段，把场景立住，比一上来讲完整关系更顺。",
        "series": f"这一条更适合拆成连续更新，先用第一条把核心场景立住，后面再补变化和互动。",
        "detail": f"这一条重点不在铺大逻辑，而是把一个会被反复提起的小细节单独放大。",
        "contrast": f"这一条更适合把变化感直接摆出来，让观众一眼看到前后差异。",
        "general": f"这一条先把一个最具体的场景讲透，会比大而泛的总结更容易起步。",
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
    errors: list[str] = []

    # 先走结构化程度最高的来源，失败后再逐级回退到库调用和 HTML 解析。
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
    tname = info.get("tname", "")
    stats = extract_video_stats(info)
    partition = map_partition(tname, tid)
    topic = build_topic(title)
    style = guess_style(title, partition, tname)
    partition_label = PARTITION_LABELS.get(partition, partition)

    # 不管上游信息来自哪个渠道，这里都整理成前端和两条分析链路共用的统一结构。
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


# 从链接出发完成 BV 提取、视频信息解析和统一结构构建。
def resolve_video_payload(url: str) -> dict:
    bvid = extract_bvid(url)
    info = fetch_video_info(url, bvid)
    return build_resolved_payload(info, bvid)


# 判断前端缓存的 resolved 结果是否还能用于当前链接。
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
        seen.add(topic_norm)
        result.append(topic)
        if len(result) >= limit:
            break
    return result


# 构建当前运行模式信息，供前端初始化页面状态。
def build_runtime_payload() -> dict:
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


# 把 VideoMetrics 或同结构对象展开成普通字典。
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
    return re.sub(r"\s+", " ", value).strip().lower()


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


# 组合视频上下文和额外输入，生成参考视频检索文本。
def build_reference_query_text(resolved: dict | None = None, extra_text: str = "") -> str:
    parts: list[str] = []
    if isinstance(resolved, dict):
        for key in ("title", "topic", "tname", "partition_label"):
            value = (resolved.get(key) or "").strip()
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
                "topic": observation["video"].get("title", ""),
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

    if isinstance(resolved, dict):
        base_topic = (resolved.get("topic") or resolved.get("title") or "").strip()
        partition_label = (resolved.get("partition_label") or resolved.get("tname") or "").strip()
        if base_topic:
            queries.append(base_topic[:50])
            if partition_label and partition_label not in base_topic:
                queries.append(f"{base_topic[:40]} {partition_label}")

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
    return deduped[:5]


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
    combined: list[dict] = []
    if isinstance(resolved, dict):
        try:
            combined.extend(fetch_direct_related_reference_videos(resolved.get("bv_id", "")))
        except Exception:
            pass
    combined.extend(list(sources or []))
    for query in build_reference_search_queries(query_text=query_text, resolved=resolved):
        try:
            combined.extend(fetch_search_reference_videos(query))
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
    try:
        info = fetch_video_info_via_public_api(clean_bvid)
    except Exception:
        if url:
            try:
                info = fetch_video_info_via_html(url, clean_bvid)
            except Exception:
                info = None

    if not isinstance(info, dict) or not info:
        return None

    owner = info.get("owner") or {}
    stats = extract_video_stats(info)
    detail = {
        "bvid": clean_bvid,
        "title": info.get("title", ""),
        "author": owner.get("name") or owner.get("uname") or "",
        "cover": info.get("pic") or info.get("cover") or "",
        "mid": safe_int(owner.get("mid") or owner.get("mid_id")),
        "view": stats.get("view"),
        "like": stats.get("like"),
        "coin": stats.get("coin"),
        "favorite": stats.get("favorite"),
        "reply": stats.get("reply"),
        "share": stats.get("share"),
        "duration": safe_int(info.get("duration")),
        "like_rate": float(stats.get("like_rate") or 0.0),
        "url": url or f"https://www.bilibili.com/video/{clean_bvid}",
    }
    REFERENCE_VIDEO_DETAIL_CACHE[cache_key] = detail
    return dict(detail)


# 判断当前候选是否还缺少前端展示所需的明确指标。
def reference_video_needs_metric_refresh(item: dict) -> bool:
    view = safe_optional_int(item.get("view"))
    like = safe_optional_int(item.get("like"))
    like_rate = float(item.get("like_rate") or 0.0)
    return view is None or view <= 0 or like is None or (like <= 0 and like_rate <= 0.0)


# 为最终展示前的参考视频补齐播放、点赞和基础信息。
def enrich_reference_video_for_display(item: dict) -> dict:
    enriched = dict(item or {})
    if not reference_video_needs_metric_refresh(enriched):
        return enriched

    detail = fetch_reference_video_detail(enriched.get("bvid", ""), enriched.get("url", ""))
    if not detail:
        return enriched

    for key in ("title", "author", "cover", "mid", "view", "like", "coin", "favorite", "reply", "share", "duration", "url"):
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
    normalized_title = normalize_reference_text(item.get("title", ""))
    title_terms = set(extract_reference_terms(item.get("title", "")))
    query_terms = extract_reference_terms(query_text)
    matched_terms: list[str] = []

    for term in query_terms:
        if term in title_terms or term in normalized_title:
            matched_terms.append(term)

    overlap_score = sum(len(term) * len(term) for term in matched_terms)
    strong_match_count = sum(1 for term in matched_terms if len(term) >= 4)
    same_up = 1 if resolved and safe_int(item.get("mid")) and safe_int(item.get("mid")) == safe_int(resolved.get("mid")) else 0
    same_author = 1 if resolved and (item.get("author") or "").strip() == (resolved.get("up_name") or "").strip() else 0
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

    is_related = bool(matched_terms) or bool(same_up) or bool(same_author)
    rank_key = (
        1 if is_related else 0,
        strong_match_count,
        overlap_score,
        same_up,
        same_author,
        source_priority,
        float(item.get("like_rate") or 0.0),
        safe_int(item.get("view")),
        -(float(item.get("competition_score") or 0.0)),
        item.get("title", ""),
    )
    return rank_key, {
        "is_related": is_related,
        "matched_terms": matched_terms,
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
    entries = []
    view_floor = build_reference_view_floor(resolved)
    soft_view_floor = 50000
    for item in sources:
        if not is_real_reference_video(item):
            continue
        rank_key, meta = build_reference_rank_entry(item, query_text=query_text, resolved=resolved)
        entries.append((rank_key, meta, item))

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

    for pool in (strong_related_pool, medium_related_pool, fallback_high_pool, related_pool, fallback_pool):
        for item in pool:
            item = enrich_reference_video_for_display(item)
            bvid = (item.get("bvid") or "").strip()
            url = (item.get("url") or "").strip()
            identity_keys = build_reference_identity_keys(item)
            if not url or any(key in seen for key in identity_keys):
                continue
            if exclude_bvid and bvid.lower() == exclude_bvid.lower():
                continue
            if not has_complete_reference_display_metrics(item):
                continue
            for key in identity_keys:
                seen.add(key)
            result.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "author": item.get("author", ""),
                    "cover": item.get("cover", ""),
                    "view": safe_int(item.get("view")),
                    "like": safe_optional_int(item.get("like")),
                    "like_rate": float(item.get("like_rate") or 0.0),
                    "source": item.get("source", ""),
                }
            )
            if len(result) >= limit:
                return result
    return result


# 从市场快照里提炼并筛出一组最终参考视频。
def build_reference_videos_from_market_snapshot(
    market_snapshot: dict,
    exclude_bvid: str = "",
    query_text: str = "",
    resolved: dict | None = None,
) -> list[dict]:
    sources = (
        (market_snapshot.get("hot_board") or [])
        + (market_snapshot.get("peer_samples") or [])
        + (market_snapshot.get("partition_samples") or [])
    )
    return select_reference_videos(
        sources,
        exclude_bvid=exclude_bvid,
        limit=6,
        query_text=query_text,
        resolved=resolved,
    )


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
    return select_reference_videos(
        sources,
        exclude_bvid=exclude_bvid,
        limit=6,
        query_text=" ".join(part for part in query_parts if part),
        resolved=resolved,
    )


# 把视频详情整理成更适合 LLM 分析的视频输入结构。
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
    video_payload = build_llm_video_payload(info, bvid, url)
    market_snapshot = build_market_snapshot(video_payload.get("retrieval_partition", "knowledge"), video_payload.get("up_ids"))
    return {
        "video": video_payload,
        "market_snapshot": market_snapshot,
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
        LLM_WORKSPACE_SIGNATURE = signature
    return LLM_WORKSPACE_AGENT


# 在 LLM Agent 模式下执行内容创作模块的完整生成流程。
def run_llm_module_create(data: dict) -> dict:
    agent = get_llm_workspace_agent()
    default_style = (data.get("style") or "干货").strip() or "干货"
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
def run_llm_module_analyze(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    agent = get_llm_workspace_agent()
    reference_query = build_reference_query_text(resolved)
    response_contract = (
        "返回一个 JSON 对象，字段必须包含：\n"
        "- resolved: 对象，包含 bv_id, title, up_name, tname, partition, partition_label, stats\n"
        "- performance: 对象，包含 label, is_hot, score, reasons, summary\n"
        "- topic_result: 对象，至少包含 ideas(长度 3 的数组)，每项包含 topic, reason, video_type, keywords；topic 必须是具体的新方向，不要提问句，不要把原视频标题后面机械接模板尾巴\n"
        "- optimize_result: 对象，包含 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions\n"
        "- copy_result: 对象或 null；如果你判断视频表现偏低，则必须返回一套新的标题/脚本/简介/标签/置顶评论，其中 titles 要用陈述型、叙事型、生活化表达，不要提问句和教学口吻\n"
        "- analysis: 对象，包含 analysis_points，并根据判断补充 followup_topics 或 next_topics、title_suggestions、cover_suggestion、content_suggestions；followup_topics / next_topics 也必须是新的具体方向，不要提问句\n"
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
    result["performance"] = normalize_performance_payload(result.get("performance"))
    if result.get("copy_result") is not None:
        copy_topic = (
            clean_copy_text((result.get("copy_result") or {}).get("topic", ""))
            or clean_copy_text(((result.get("topic_result") or {}).get("ideas") or [{}])[0].get("topic", ""))
            or resolved.get("topic")
            or resolved.get("title")
            or "视频优化"
        )
        result["copy_result"] = normalize_copy_result_payload(
            result.get("copy_result"),
            copy_topic,
            resolved.get("style", "干货"),
        )
    result["reference_videos"] = build_reference_videos_from_market_snapshot(
        market_snapshot,
        exclude_bvid=resolved.get("bv_id", ""),
        query_text=reference_query,
        resolved=resolved,
    )
    return result


# 当分析 Agent 中枢不可用时，直接用单次 LLM 调用回退生成分析结果。
def run_llm_module_analyze_fallback(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    llm = build_runtime_llm_client()
    llm.require_available()
    reference_query = build_reference_query_text(resolved)
    compact_snapshot = compact_market_snapshot_for_llm(market_snapshot)
    system_prompt = (
        "你是 B 站视频分析助手。"
        "当前已经拿到后端解析出的真实视频信息和同类样本，请直接完成爆款/低表现判断、原因拆解、优化建议和后续选题。"
        "不要输出解释性废话，只返回 JSON。"
    )
    user_prompt = (
        "请根据下面的数据直接输出 JSON，对象字段必须包含："
        "resolved, performance, topic_result, optimize_result, copy_result, analysis。\n\n"
        f"当前视频真实信息：{json.dumps(resolved, ensure_ascii=False)}\n\n"
        f"市场样本：{json.dumps(compact_snapshot, ensure_ascii=False)}\n\n"
        "要求：\n"
        "1. resolved 直接复用当前视频真实信息，不要改 BV、标题、播放等字段。\n"
        "2. performance 必须包含 label, is_hot, score, reasons, summary。\n"
        "3. topic_result.ideas 输出 3 个后续选题，每项包含 topic, reason, video_type, keywords；topic 必须是新的具体方向，不要提问句。\n"
        "4. optimize_result 输出 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions。\n"
        "5. 如果你判断 is_hot=true，则 copy_result 返回 null，analysis 重点输出 analysis_points 和 followup_topics。\n"
        "6. 如果你判断 is_hot=false，则 copy_result 必须输出一版新文案，analysis 重点输出 analysis_points, next_topics, title_suggestions, cover_suggestion, content_suggestions。\n"
        "7. copy_result.titles 必须是陈述型、叙事型、生活化标题，不要提问句，不要教学口吻，不要出现“为什么 / 怎么 / 哪种 / 更容易起量 / 更容易进推荐”这类模板。\n"
        "8. analysis 里的 followup_topics / next_topics 也必须是具体新方向，不要把原视频标题后面机械加问题后缀。"
    )
    result = llm.invoke_json_required(system_prompt, user_prompt)
    if not isinstance(result, dict):
        raise ValueError("LLM fallback 返回格式无效")
    result["resolved"] = resolved
    result["performance"] = normalize_performance_payload(result.get("performance"))
    if result.get("copy_result") is not None:
        copy_topic = (
            clean_copy_text((result.get("copy_result") or {}).get("topic", ""))
            or clean_copy_text(((result.get("topic_result") or {}).get("ideas") or [{}])[0].get("topic", ""))
            or resolved.get("topic")
            or resolved.get("title")
            or "视频优化"
        )
        result["copy_result"] = normalize_copy_result_payload(
            result.get("copy_result"),
            copy_topic,
            resolved.get("style", "干货"),
        )
    result.setdefault(
        "reference_videos",
        build_reference_videos_from_market_snapshot(
            market_snapshot,
            exclude_bvid=resolved.get("bv_id", ""),
            query_text=reference_query,
            resolved=resolved,
        ),
    )
    result.setdefault("runtime_mode", "llm_agent")
    result.setdefault("agent_trace", ["llm_direct_fallback"])
    return result


# 运行聊天助手，让 LLM Agent 按需调工具后返回自然语言答复。
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


@app.get("/api/runtime-info")
# 返回当前运行模式、聊天可用性和页面展示所需状态。
def api_runtime_info():
    return jsonify({"success": True, "data": build_runtime_payload()})


@app.post("/api/runtime-mode")
# 切换运行模式开关；开启时优先复用已保存配置，没有配置则提示前端展示表单。
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


@app.post("/api/runtime-llm-config")
# 保存前端填写的运行时 LLM 配置，并立即切换到 LLM Agent 模式。
def api_runtime_llm_config():
    data = request.get_json(silent=True) or {}
    try:
        save_runtime_llm_config(data)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    payload = build_runtime_payload()
    payload["requires_config"] = False
    return jsonify({"success": True, "data": payload})


@app.get("/")
# 渲染工作台首页。
def index():
    return render_template("index.html", initial_runtime=build_runtime_payload())


@app.post("/api/resolve-bili-link")
# 解析视频链接并返回前端预览所需的统一视频信息。
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
# 执行内容创作模块，根据运行模式返回规则或 LLM 生成结果。
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


@app.post("/api/module-analyze")
# 执行视频分析模块，根据运行模式返回规则或 LLM 分析结果。
def api_module_analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "请先输入 B 站视频链接"}), 400

    try:
        resolved = data.get("resolved") if is_resolved_payload_usable(data.get("resolved"), url) else resolve_video_payload(url)
    except Exception as exc:
        return jsonify({"success": False, "error": f"链接解析失败：{exc}"}), 400

    if runtime_llm_enabled():
        try:
            market_snapshot = build_market_snapshot(resolved.get("partition"), resolved.get("up_ids"))
            return jsonify({"success": True, "data": run_llm_module_analyze(data, resolved, market_snapshot)})
        except Exception as exc:
            if should_skip_same_provider_fallback(exc):
                message = (
                    f"LLM Agent 分析失败：{format_llm_error(exc)} "
                    "当前不会继续尝试同 provider 的 fallback，请稍后重试。"
                )
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
            try:
                market_snapshot = build_market_snapshot(resolved.get("partition"), resolved.get("up_ids"))
                fallback_result = run_llm_module_analyze_fallback(data, resolved, market_snapshot)
                fallback_result["llm_warning"] = f"Agent 中枢执行失败，已切换到 LLM 直出分析：{format_llm_error(exc)}"
                return jsonify({"success": True, "data": fallback_result})
            except Exception as fallback_exc:
                message = (
                    f"LLM Agent 分析失败：{format_llm_error(exc)}；"
                    f"LLM fallback 也失败：{format_llm_error(fallback_exc)}"
                )
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": message,
                            "data": build_llm_runtime_reconfigure_data(message),
                        }
                    ),
                    llm_error_http_status(fallback_exc),
                )

    topic_result = run_topic(
        partition_name=resolved.get("partition"),
        up_ids=resolved.get("up_ids"),
        seed_topic=resolved.get("topic"),
    )
    performance = classify_video_performance(resolved)

    copy_result = None
    optimize_result: dict = {}
    analysis = {}
    if performance["is_hot"]:
        analysis = build_hot_analysis(resolved, performance, topic_result)
    else:
        optimize_result = to_plain_data(build_rule_optimization_agent().run(resolved.get("bv_id", "BV1Demo411111")))
        copy_result = to_plain_data(
            build_rule_copy_agent().run(
                topic=resolved.get("topic") or resolved.get("title") or "视频优化",
                style=resolved.get("style", "干货"),
            )
        )
        analysis = build_low_performance_analysis(resolved, performance, optimize_result, topic_result)

    reference_videos = select_reference_videos(
        topic_result.get("videos", []),
        exclude_bvid=resolved.get("bv_id", ""),
        limit=6,
        query_text=build_reference_query_text(resolved),
        resolved=resolved,
    )

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
# 处理聊天助手请求，仅在 LLM Agent 模式下开放。
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


@app.post("/api/topic")
# 提供独立的选题接口，便于单模块调用和调试。
def api_topic():
    data = request.get_json(silent=True) or {}
    result = run_topic(
        partition_name=data.get("partition", "knowledge"),
        up_ids=data.get("up_ids"),
        seed_topic=data.get("topic"),
    )
    return jsonify({"success": True, "data": result})


@app.post("/api/copy")
# 提供独立的文案接口，便于单模块调用和调试。
def api_copy():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "B站内容提效")
    style = data.get("style", "干货")
    result = run_copy(topic=topic, style=style)
    return jsonify({"success": True, "data": result})


@app.post("/api/operate")
# 提供独立的运营建议接口，便于单模块调用和调试。
def api_operate():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    dry_run = bool(data.get("dry_run", True))
    result = run_operate(bv_id=bv_id, dry_run=dry_run)
    return jsonify({"success": True, "data": result})


@app.post("/api/optimize")
# 提供独立的优化接口，便于单模块调用和调试。
def api_optimize():
    data = request.get_json(silent=True) or {}
    bv_id = data.get("bv_id", "BV1Demo411111")
    result = run_optimize(bv_id=bv_id)
    return jsonify({"success": True, "data": result})


@app.post("/api/pipeline")
# 提供完整流水线接口，一次性返回多阶段结果。
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
# 兜底捕获未处理异常，并以统一 JSON 结构返回给前端。
def handle_error(exc):
    return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
