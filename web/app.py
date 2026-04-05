"""Flask web entry for the Bilibili content ideation and analysis workspace."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
import json
import re
import sys
import threading
import time
import zlib
from base64 import b64decode
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Flask, jsonify, render_template, request

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bilibili_api import sync, video

from agents.copywriting_agent import CopywritingAgent
from agents.llm_workspace_agent import AgentTool, LLMWorkspaceAgent, RetrievalTool
from agents.optimization_agent import OptimizationAgent
from agents.topic_agent import TopicAgent
from knowledge_base import Document, KnowledgeBase, sample as kb_sample
from knowledge_sync import ingest_uploaded_file, update_chroma_knowledge_base
from config import CONFIG
from llm_client import LLMClient, format_llm_error, llm_error_http_status, should_skip_same_provider_fallback
from main import run_copy, run_operate, run_optimize, run_pipeline, run_topic
from models import to_plain_data
from observability import configure_langsmith, traceable
from tools.search_tool import SearchTool

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
    static_folder=str(Path(__file__).resolve().parent / "static"),
)
LANGSMITH_RUNTIME = configure_langsmith("web")

SHORT_LINK_HOSTS = ("b23.tv", "bili2233.cn")
TRACKING_LINK_HOSTS = ("cm.bilibili.com",)
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
KNOWLEDGE_SEARCH_CATEGORY_RULES = {
    "番剧": {"keywords": ("番剧", "新番", "追番", "番剧解说"), "partitions": ("番剧",), "broad_partitions": ("ent",)},
    "国创": {"keywords": ("国创", "国漫", "国产动画"), "partitions": ("国创", "国漫"), "broad_partitions": ("ent",)},
    "纪录片": {"keywords": ("纪录片", "纪实", "人文", "自然", "历史纪录"), "partitions": ("纪录片",), "broad_partitions": ("ent",)},
    "电影": {"keywords": ("电影", "影评", "院线", "幕后"), "partitions": ("电影", "影视"), "broad_partitions": ("ent",)},
    "电视剧": {"keywords": ("电视剧", "国产剧", "海外剧", "追剧"), "partitions": ("电视剧", "影视"), "broad_partitions": ("ent",)},
    "综艺": {"keywords": ("综艺", "真人秀", "名场面"), "partitions": ("综艺",), "broad_partitions": ("ent",)},
    "动画": {"keywords": ("动画", "mad", "amv", "特摄", "手办", "模玩"), "partitions": ("动画", "特摄", "手办", "模玩"), "broad_partitions": ("ent",)},
    "游戏": {"keywords": ("游戏", "电竞", "手游", "端游"), "partitions": ("游戏", "电竞"), "broad_partitions": ("game",), "allow_broad_match": True},
    "鬼畜": {"keywords": ("鬼畜", "音mad", "人力vocaloid", "鬼畜调教", "鬼畜剧场"), "partitions": ("鬼畜", "音mad", "人力vocaloid"), "broad_partitions": ("ent",)},
    "音乐": {"keywords": ("音乐", "翻唱", "演奏", "原创音乐", "乐器", "乐理", "说唱", "唱歌"), "partitions": ("翻唱", "演奏", "原创音乐", "音乐"), "broad_partitions": ("ent",)},
    "舞蹈": {"keywords": ("舞蹈", "宅舞", "街舞", "热舞", "编舞", "翻跳", "手势舞", "舞见", "跳舞"), "partitions": ("舞蹈", "宅舞", "街舞", "中国舞", "舞见"), "broad_partitions": ("ent",)},
    "科技数码": {"keywords": ("科技", "数码", "手机", "电脑", "ai", "软件", "开箱"), "partitions": ("科技", "数码", "手机", "电脑"), "broad_partitions": ("tech",), "allow_broad_match": True},
    "汽车": {"keywords": ("汽车", "新车", "试驾", "用车", "驾驶", "保养", "车型", "自驾"), "partitions": ("汽车",), "broad_partitions": ("tech",)},
    "时尚美妆": {"keywords": ("美妆", "护肤", "穿搭", "彩妆", "时尚", "妆容"), "partitions": ("美妆", "穿搭", "时尚"), "broad_partitions": ("ent", "life")},
    "体育运动": {"keywords": ("体育", "运动", "健身", "训练", "赛事", "篮球", "足球"), "partitions": ("体育", "运动", "健身"), "broad_partitions": ("life",)},
    "动物": {"keywords": ("动物", "萌宠", "宠物", "猫", "狗"), "partitions": ("动物", "萌宠", "宠物"), "broad_partitions": ("life",)},
    "生活": {"keywords": ("生活", "日常", "vlog", "探店", "做饭", "家居", "好物", "体验"), "partitions": ("生活", "日常", "vlog", "美食"), "broad_partitions": ("life",), "allow_broad_match": True},
    "知识科普": {"keywords": ("知识", "科普", "社科", "历史", "学习", "医学", "健康", "人文"), "partitions": ("知识", "科普", "学习", "校园"), "broad_partitions": ("knowledge",), "allow_broad_match": True},
    "娱乐热点": {"keywords": ("娱乐", "热点", "八卦", "明星", "热搜", "争议", "影视解说"), "partitions": ("娱乐", "影视"), "broad_partitions": ("ent",)},
    "职场成长": {"keywords": ("职场", "求职", "面试", "简历", "打工", "副业", "考研", "考证"), "partitions": ("职场", "校园", "学习"), "broad_partitions": ("knowledge",)},
    "情感婚恋": {"keywords": ("情感", "婚恋", "婚姻", "情侣", "暧昧", "前任", "脱单", "相亲"), "partitions": ("情感", "婚恋"), "broad_partitions": ("life", "knowledge")},
    "两性心理": {"keywords": ("两性", "心理", "男女", "情绪共鸣", "高情商", "安全感", "人性"), "partitions": ("心理", "情感"), "broad_partitions": ("knowledge", "life")},
    "通用爆款": {"keywords": ("爆款", "标题", "脚本", "互动", "标签", "发布时间", "置顶评论", "封面"), "match_all": True},
}
LIFE_CONTENT_KEYWORDS = (
    "生活",
    "美食",
    "日常",
    "家居",
    "赶海",
    "海货",
    "海鲜",
    "潮水",
    "滩涂",
    "退潮",
    "海边",
    "渔村",
    "赶山",
    "蛤",
    "蛤蜊",
    "花甲",
    "毛蛤",
    "蛏",
    "海螺",
    "螃蟹",
    "生蚝",
    "海蛎",
)
SEA_HARVEST_KEYWORDS = (
    "赶海",
    "海货",
    "海鲜",
    "潮水",
    "滩涂",
    "退潮",
    "海边",
    "蛤",
    "蛤蜊",
    "花甲",
    "毛蛤",
    "蛏",
    "海螺",
    "螃蟹",
    "生蚝",
    "海蛎",
)
SEA_HARVEST_TARGET_KEYWORDS = (
    "大毛蛤",
    "毛蛤",
    "蛏王",
    "蛏子",
    "蛤蜊",
    "花甲",
    "海螺",
    "螃蟹",
    "生蚝",
    "海蛎",
    "青口",
    "扇贝",
    "海胆",
    "海参",
    "八爪鱼",
    "章鱼",
    "海兔",
    "海货",
)
ANALYSIS_TOPIC_BAD_TAILS = (
    "换成",
    "结果先出",
    "新版本",
    "连续更新",
    "三条",
    "系列版",
    "轻量更新",
    "切口测试",
    "起步版",
    "更清楚",
)
VIDEO_KEYWORD_STOPWORDS = {
    "哔哩哔哩",
    "bilibili",
    "b站",
    "视频",
    "原创",
    "弹幕",
}
MUSIC_REFERENCE_KEYWORDS = (
    "音乐",
    "原创音乐",
    "演奏",
    "翻唱",
    "翻弹",
    "弹唱",
    "cover",
    "remix",
    "配乐",
    "转调",
    "和声",
    "乐器",
    "钢琴",
    "钢琴曲",
    "吉他",
    "贝斯",
    "小提琴",
    "大提琴",
    "古筝",
    "古琴",
    "琵琶",
    "二胡",
    "萨克斯",
    "长笛",
    "架子鼓",
)
VIDEO_BENCHMARK_QUERY_STOPWORDS = {
    *VIDEO_KEYWORD_STOPWORDS,
    "爆款",
    "热门",
    "高播放",
    "高点赞",
    "高赞",
    "高播放量",
    "分区",
    "赛道",
    "样本",
    "对标",
    "短视频",
}
VIDEO_BENCHMARK_WEAK_TERMS = {
    "记录",
    "练习",
    "分享",
    "日常",
    "生活",
    "娱乐",
    "知识",
    "科技",
    "游戏",
}
CREATOR_PARTITION_ANGLES = {
    "knowledge": ["问题拆解", "保姆级步骤", "避坑清单", "实测对比"],
    "tech": ["结果对比", "真实实测", "省钱替代", "新手避坑"],
    "life": ["低成本切口", "真实体验", "前后对比", "情绪共鸣"],
    "game": ["版本答案", "新手路线", "实战复盘", "高光片段"],
    "ent": ["3秒反差开场", "热门动作切口", "评论区互动点", "系列人设"],
}
CREATOR_REASON_FALLBACKS = {
    "knowledge": "问题意识、信息增量、真实案例",
    "tech": "实测差异、使用门槛、取舍成本",
    "life": "真实体验、关系细节、情绪共鸣",
    "game": "版本理解、实战反馈、操作细节",
    "ent": "反差瞬间、真实反应、互动情绪",
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

RAW_TOPIC_AGENT = TopicAgent()
RAW_COPY_AGENT = CopywritingAgent()
KNOWLEDGE_BASE = KnowledgeBase(persist_directory=CONFIG.vector_db_path)
RUNTIME_TOOL_KNOWLEDGE_BASE = KnowledgeBase(
    persist_directory=CONFIG.vector_db_path,
    collection_name="workspace_runtime_knowledge",
)
LONG_TERM_MEMORY = None
WEB_SEARCH = SearchTool()
LLM_WORKSPACE_AGENT: LLMWorkspaceAgent | None = None
LLM_WORKSPACE_SIGNATURE: tuple[str, ...] | None = None
LLM_VIDEO_ANALYZE_AGENT: LLMWorkspaceAgent | None = None
LLM_VIDEO_ANALYZE_SIGNATURE: tuple[str, ...] | None = None
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
RESOLVED_PAYLOAD_VERSION = 3
VIDEO_TNAME_HINTS = {
    214: "田园美食",
    255: "颜值·网红舞",
}
SUPPORTED_KNOWLEDGE_UPLOAD_SUFFIXES = {".txt", ".md", ".docx", ".pdf"}
KNOWLEDGE_UPDATE_JOB_LOCK = threading.Lock()
KNOWLEDGE_UPDATE_EXECUTION_LOCK = threading.Lock()
KNOWLEDGE_UPDATE_JOBS: dict[str, dict] = {}
KNOWLEDGE_UPDATE_ACTIVE_JOB_ID: str | None = None
KNOWLEDGE_UPDATE_JOB_TTL_SECONDS = 60 * 60
LLM_SCENE_ALLOWED_TOOLS = {
    "module_create": ["retrieval", "web_search"],
    "module_analyze": ["retrieval", "web_search"],
    "workspace_chat": ["retrieval", "web_search", "video_briefing", "hot_board_snapshot"],
}
CREATOR_BRIEFING_TRIGGER_KEYWORDS = ("选题", "赛道", "分区", "热点", "竞品", "爆款")


# 判断当前是否已经保存了可用于启用 LLM Agent 的运行时配置。
def get_long_term_memory():
    global LONG_TERM_MEMORY
    if LONG_TERM_MEMORY is None:
        from memory.long_term_memory import LongTermMemory

        LONG_TERM_MEMORY = LongTermMemory(persist_directory=CONFIG.vector_db_path)
    return LONG_TERM_MEMORY


VIDEO_ANALYZE_RETRIEVAL_FILTER = {"data_type": "static_hot_case", "source": "knowledge_base"}
VIDEO_ANALYZE_DIRTY_SOURCES = {"creator_briefing", "video_briefing", "hot_board_snapshot"}
VIDEO_ANALYZE_REQUIRED_TOOLS = ("retrieval",)
VIDEO_ANALYZE_REQUIRED_FINAL_KEYS = ("resolved", "performance", "topic_result", "optimize_result", "analysis", "copy_result")
VIDEO_ANALYZE_HOT_PEER_RECENT_DAYS = 30
VIDEO_ANALYZE_HOT_PEER_MIN_VIEW = 50000
VIDEO_ANALYZE_HOT_PEER_MIN_LIKE = 1000
VIDEO_ANALYZE_HOT_PEER_LIMIT = 6
VIDEO_ANALYZE_TASK_GOAL = (
    "针对当前单个视频完成独立分析；当前视频结构化信息和同方向爆款对标样本已由后端预加载；"
    "你必须先检索同赛道静态爆款样本；"
    "仅当样本不足时才联网补充，并输出爆款/低表现判断、评分、优化文案、脚本、题材方向和直接对标样本。"
)
VIDEO_ANALYZE_RESPONSE_CONTRACT = (
    "返回一个 JSON 对象，字段必须包含：\n"
    "- resolved: 对象，包含 bv_id, title, up_name, tname, partition, partition_label, stats\n"
    "- performance: 对象，包含 label, is_hot, score, reasons, summary\n"
    "- topic_result: 对象，至少包含 ideas(长度 3 的数组，每项包含 topic, reason, video_type, keywords)\n"
    "- optimize_result: 对象，包含 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions\n"
    "- copy_result: 对象或 null；低表现视频必须给出可直接使用的新标题、脚本、简介、标签、置顶评论\n"
    "- analysis: 对象，必须包含 analysis_points, benchmark_analysis, remake_script_structure, advanced_title_sets, "
    "cover_plan, tag_strategy, publish_strategy, reusable_hit_points，并根据判断补充 followup_topics 或 next_topics、"
    "title_suggestions、cover_suggestion、content_suggestions\n"
)
VIDEO_ANALYZE_SYSTEM_PROMPT = (
    "【最高优先级强制规则】\n"
    "1. 本模块为单次独立视频分析任务，与内容创作、智能问答模块完全隔离。\n"
    "2. 全程绝对禁用任何长期记忆：严禁读取、调用、复用 user_long_term_memory 中的任何数据，严禁读取历史任务记录、"
    "历史分析记录、agent_trace。\n"
    "3. 所有判断与输出只基于本次请求传入的单条视频信息，以及本次工具调用实时检索到的同赛道静态爆款样本。\n"
    "4. 任务结束后严禁将本次分析结果写入任何向量库或记忆库。\n"
    "5. 绝对禁止调用 hot_board_snapshot。\n\n"
    "【任务目标】\n"
    "你要对当前单个 B 站视频完成闭环分析：判断它是爆款还是低表现，给出评分，并输出可直接落地的优化结果。\n"
    "如果是爆款：拆解核心优点、可复用流量亮点、可延伸选题方向。\n"
    "如果是低表现：对标同赛道样本，给出标题、封面、内容结构、标签、发布策略等全维度改进建议，并生成可直接使用的新文案与脚本。\n\n"
    "【工具调用链路】\n"
    "1. 当前输入里的 parsed_video 与 preloaded_context.video 已经是后端预加载的当前视频真实信息，不允许再重新解析当前视频，不允许调用 video_briefing。\n"
    "2. 第一步必须调用 retrieval，检索同垂类、同赛道的静态爆款样本。query 必须优先参考 "
    "preloaded_context.video.benchmark_queries 和 benchmark_terms，优先使用具体方向词，不要退化成“生活 / 记录 / 短视频”这类泛词。\n"
    "3. preloaded_context.market_snapshot.peer_samples 是代码预抓的同方向爆款对标视频，不是同 UP 样本，也不是 LLM 生成内容；"
    "如果这里非空，analysis.benchmark_analysis.benchmark_videos 必须优先引用这些样本。\n"
    "4. 禁止输出任何“先重新解析当前视频 / 调 video_briefing / 再看一次当前视频详情”之类的计划，因为当前视频已解析完成。\n"
    "5. 只有当 retrieval 返回样本不足时，才允许调用 web_search 搜索最新赛道规则或补充案例。\n"
    "6. 所有工具调用完成后直接输出 final 结构化结果，禁止无意义循环。\n\n"
    "【判定要求】\n"
    "1. 必须明确输出 performance.score，并给出爆款/低表现结论。\n"
    "2. analysis.benchmark_analysis.benchmark_videos 必须尽量给出同赛道直接对标参考视频。\n"
    "3. 必须生成符合当前视频内容的新标题、优化文案脚本、下一批优先题材、具体封面建议和内容建议。\n"
    "4. 仅输出 JSON 对象，不要输出 markdown 和解释。"
)


def has_saved_runtime_llm_config() -> bool:
    return bool((RUNTIME_LLM_CONFIG or {}).get("api_key", "").strip())


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def snapshot_knowledge_update_job(job: dict | None) -> dict | None:
    if not job:
        return None
    payload = deepcopy(job)
    payload.pop("updated_at_ts", None)
    return payload


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


def active_knowledge_update_job_locked() -> dict | None:
    if not KNOWLEDGE_UPDATE_ACTIVE_JOB_ID:
        return None
    job = KNOWLEDGE_UPDATE_JOBS.get(KNOWLEDGE_UPDATE_ACTIVE_JOB_ID)
    if not job:
        return None
    if str(job.get("status") or "") not in {"queued", "running"}:
        return None
    return job


def get_active_knowledge_update_job() -> dict | None:
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        cleanup_knowledge_update_jobs_locked()
        return snapshot_knowledge_update_job(active_knowledge_update_job_locked())


def get_knowledge_update_job(job_id: str) -> dict | None:
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        cleanup_knowledge_update_jobs_locked()
        return snapshot_knowledge_update_job(KNOWLEDGE_UPDATE_JOBS.get(job_id))


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


def clear_active_knowledge_update_job(job_id: str) -> None:
    global KNOWLEDGE_UPDATE_ACTIVE_JOB_ID
    with KNOWLEDGE_UPDATE_JOB_LOCK:
        if KNOWLEDGE_UPDATE_ACTIVE_JOB_ID == job_id:
            KNOWLEDGE_UPDATE_ACTIVE_JOB_ID = None


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


def normalize_knowledge_search_category(value: object) -> str:
    clean = str(value or "").strip()
    return clean if clean in KNOWLEDGE_SEARCH_CATEGORY_RULES else ""


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


def knowledge_chunk_index(item: dict) -> int:
    metadata = item.get("metadata") or {}
    value = metadata.get("chunk_index")
    return safe_int(value) if value is not None else 10**9


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
    with urlopen(request_obj, timeout=timeout) as response:
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
        with urlopen(request_obj, timeout=8) as response:
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
        html_info = fetch_video_info_via_html(url, bvid)
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
        return enrich_video_info_with_html_hints(base_info, url, bvid)
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
    errors: list[str] = []

    # 先走结构化程度最高的来源，失败后再逐级回退到库调用和 HTML 解析。
    try:
        info = fetch_video_info_via_public_api(bvid)
        return enrich_video_info_best_effort(info, url, bvid)
    except Exception as exc:
        errors.append(f"public api: {exc}")

    try:
        info = sync(video.Video(bvid=bvid).get_info())
        return enrich_video_info_best_effort(info, url, bvid)
    except Exception as exc:
        errors.append(f"bilibili_api: {exc}")

    try:
        return fetch_video_info_via_html(url, bvid)
    except Exception as exc:
        errors.append(f"html: {exc}")

    raise ValueError("；".join(errors))


# 仅用于前端预览的快速解析链路；优先返回足够展示基础信息的结果，避免被慢接口超时拖住。
def fetch_video_preview_info(url: str, bvid: str) -> dict:
    errors: list[str] = []

    try:
        return fetch_video_info_via_html(url, bvid)
    except Exception as exc:
        errors.append(f"html fast path: {exc}")

    try:
        return fetch_video_info_via_public_api(bvid)
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
    latin_phrases = extract_latin_reference_phrases(" ".join([title, topic]))
    combined_text = " ".join([title, topic, tname, partition_label, *raw_keywords])
    effective_partition = str(resolved.get("partition") or "").strip()
    lane_label = tname or partition_label
    if looks_like_music_reference(combined_text):
        effective_partition = "ent"
        lane_label = "音乐"
        if "钢琴" in combined_text and "钢琴" not in keywords:
            keywords.insert(0, "钢琴")

    terms: list[str] = []
    for term in keywords + latin_phrases + title_terms:
        clean = re.sub(r"\s+", " ", str(term or "").strip()).strip(" ，,。.;；:-_|")
        marker = clean.lower()
        if (
            len(clean) < 2
            or marker in VIDEO_BENCHMARK_QUERY_STOPWORDS
            or clean in VIDEO_BENCHMARK_WEAK_TERMS
            or clean in terms
        ):
            continue
        if any(marker != existing.lower() and marker in existing.lower() for existing in terms):
            continue
        terms.append(clean)
        if len(terms) >= 6:
            break

    queries: list[str] = []
    seen_queries: set[str] = set()
    append_benchmark_query(queries, seen_queries, [lane_label, *terms[:2]])
    append_benchmark_query(queries, seen_queries, terms[:3])
    append_benchmark_query(queries, seen_queries, [terms[0], terms[1], terms[2] if len(terms) > 2 else ""] if terms else [])
    if title:
        append_benchmark_query(queries, seen_queries, [title[:32]])
    if not queries:
        append_benchmark_query(queries, seen_queries, [lane_label or partition_label, title[:20]])

    return {
        "effective_partition": effective_partition or str(resolved.get("partition") or "").strip(),
        "effective_partition_label": PARTITION_LABELS.get(effective_partition, effective_partition) if effective_partition else partition_label,
        "lane_label": lane_label or partition_label,
        "terms": terms[:6],
        "queries": queries[:4],
    }


# 为视频分析模块构造“同方向爆款”检索词，避免退化成同 UP 样本。
def build_video_benchmark_queries(resolved: dict) -> list[str]:
    return list(build_video_benchmark_profile(resolved).get("queries") or [])


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
        raw_samples = RAW_TOPIC_AGENT.fetch_hot_peer_videos(
            queries,
            exclude_bvid=resolved.get("bv_id", ""),
            limit=VIDEO_ANALYZE_HOT_PEER_LIMIT,
            recent_days=VIDEO_ANALYZE_HOT_PEER_RECENT_DAYS,
            min_view=VIDEO_ANALYZE_HOT_PEER_MIN_VIEW,
            min_like=VIDEO_ANALYZE_HOT_PEER_MIN_LIKE,
        )
        peer_samples = []
        for item in raw_samples[: VIDEO_ANALYZE_HOT_PEER_LIMIT * 2]:
            candidate = {
                **serialize_video_metric(item),
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
        keywords = extract_video_keywords(resolved.get("keywords"))
        semantic_values: list[str] = []

        for key in ("topic", "tname", "partition_label"):
            value = (resolved.get(key) or "").strip()
            if value:
                semantic_values.append(value)

        if keywords:
            semantic_values.append(" ".join(keywords[:3]))

        title = (resolved.get("title") or "").strip()
        if title and has_semantic_reference_title(title, keywords=keywords):
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
        base_topic = (resolved.get("topic") or resolved.get("title") or "").strip()
        partition_label = (resolved.get("partition_label") or resolved.get("tname") or "").strip()
        keywords = extract_video_keywords(resolved.get("keywords"))

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
    strict_related_only = has_strict_reference_signal(resolved, query_text)
    if isinstance(resolved, dict):
        try:
            combined.extend(fetch_direct_related_reference_videos(resolved.get("bv_id", "")))
        except Exception:
            pass
    combined.extend(list(sources or []))
    for query in build_reference_search_queries(query_text=query_text, resolved=resolved):
        try:
            combined.extend(fetch_search_reference_videos(query, limit=6 if strict_related_only else 8))
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

    detail = fetch_reference_video_detail(enriched.get("bvid", ""), enriched.get("url", ""))
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
    query_terms = extract_reference_terms(query_text)
    matched_terms: list[str] = []

    for term in query_terms:
        if term in title_terms or term in semantic_terms or term in normalized_semantic_text:
            matched_terms.append(term)

    resolved = resolved or {}
    target_keywords = extract_video_keywords(resolved.get("keywords"))
    matched_keywords: list[str] = []
    for keyword in target_keywords:
        normalized_keyword = normalize_reference_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_semantic_text:
            matched_keywords.append(keyword)

    overlap_score = sum(len(term) * len(term) for term in matched_terms)
    strong_match_count = sum(1 for term in matched_terms if len(term) >= 4)
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
    partition_aligned = not strict_signal or not target_partition or not item_partition or same_partition == 1
    keyword_aligned = not strict_signal or not target_keywords or bool(matched_keywords)
    is_related = (bool(matched_terms) and partition_aligned and keyword_aligned) or bool(same_up) or bool(same_author)
    rank_key = (
        1 if is_related else 0,
        len(matched_keywords),
        strong_match_count,
        overlap_score,
        same_partition,
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

    for pool in pools:
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
    reference_videos = build_module_analyze_reference_videos(
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
def run_llm_module_analyze_fallback(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    llm = build_runtime_llm_client()
    llm.require_available()
    baseline_performance = classify_video_performance(resolved)
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
    return finalize_module_analyze_result(result, resolved, market_snapshot)


# 运行视频分析模块，让 LLM Agent 按既定工具链完成单次独立分析。
@traceable(run_type="chain", name="web.run_llm_module_analyze", tags=["web", "llm", "rag", "module_analyze"])
def run_llm_module_analyze(data: dict, resolved: dict, market_snapshot: dict) -> dict:
    agent = get_video_analyze_agent()
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
                "market_snapshot": market_snapshot,
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
        return finalize_module_analyze_result(result, resolved, market_snapshot)
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


@app.get("/api/knowledge/status")
# 返回当前 Chroma 知识库状态，供页面展示和排查使用。
def api_knowledge_status():
    return jsonify({"success": True, "data": build_knowledge_base_status()})


@app.get("/api/knowledge/sample")
# 返回知识库中的部分原始文档内容，便于页面直接查看当前库存。
def api_knowledge_sample():
    limit = max(1, min(safe_int(request.args.get("limit") or 10), 20))
    offset = max(0, safe_int(request.args.get("offset") or 0))
    try:
        result = kb_sample(limit=limit, offset=offset)
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": f"读取知识库内容失败：{exc}"}), 500


@app.get("/api/knowledge/search")
# 根据关键词检索知识库中的命中文档，供知识库管理页查看。
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


@app.post("/api/knowledge/upload")
# 上传文件并自动写入 Chroma 知识库。
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


@app.post("/api/knowledge/update")
# 重新抓取 B 站热门榜数据并追加写入 Chroma 知识库。
def api_knowledge_update():
    data = request.get_json(silent=True) or {}
    limit = max(1, min(safe_int(data.get("limit") or 10), 20))
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


@app.post("/api/knowledge/update/start")
# 启动热门知识库异步更新任务，并返回任务 ID 供前端轮询。
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


@app.get("/api/knowledge/update/<job_id>")
# 返回热门知识库异步更新任务的实时状态。
def api_knowledge_update_job(job_id: str):
    job = get_knowledge_update_job(job_id.strip())
    if not job:
        return jsonify({"success": False, "error": "未找到对应的知识库更新任务。"}), 404
    return jsonify({"success": True, "data": job})


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
        info = fetch_video_preview_info(url, bvid)
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
        market_snapshot = build_hot_peer_market_snapshot(resolved)
        try:
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
