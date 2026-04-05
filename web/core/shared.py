"""Flask web entry for the Bilibili content ideation and analysis workspace."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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

from flask import Response, jsonify, render_template, request, stream_with_context

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
    "蜘蛛蟹",
    "海货",
)
OVERSEAS_REFERENCE_LOCATION_TERMS = (
    "海外",
    "国外",
    "法国",
    "英国",
    "德国",
    "意大利",
    "西班牙",
    "葡萄牙",
    "日本",
    "韩国",
    "泰国",
    "越南",
    "马来西亚",
    "新加坡",
    "印尼",
    "美国",
    "加拿大",
    "澳大利亚",
    "新西兰",
    "挪威",
    "冰岛",
    "俄罗斯",
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
NARRATIVE_REFERENCE_KEYWORDS = (
    "网络喷子",
    "网暴",
    "蹲守",
    "跟踪",
    "喷子",
    "人性",
    "潜伏",
    "线下见面",
    "线下",
    "见面",
    "曝光",
    "反转",
    "真实故事",
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
MODULE_ANALYZE_JOB_LOCK = threading.Lock()
MODULE_ANALYZE_JOBS: dict[str, dict] = {}
MODULE_ANALYZE_JOB_TTL_SECONDS = 60 * 60
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
VIDEO_ANALYZE_HOT_PEER_RELAXED_RECENT_DAYS = 90
VIDEO_ANALYZE_HOT_PEER_RELAXED_MIN_VIEW = 10000
VIDEO_ANALYZE_HOT_PEER_RELAXED_MIN_LIKE = 300
VIDEO_ANALYZE_MARKET_SNAPSHOT_PREFETCH_WAIT_SECONDS = 0.8
VIDEO_ANALYZE_TASK_GOAL = (
    "针对当前单个视频完成独立分析；当前视频结构化信息已由后端预加载，同方向爆款对标样本可能已预抓完成，也可能仍在异步加载；"
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
    "2. 第一步必须调用 retrieval，检索同垂类、同赛道的静态爆款样本。query 必须优先直接复用 "
    "preloaded_context.video.benchmark_queries 里的短查询，并参考 benchmark_terms；不要把原标题拆成十几个碎词，也不要退化成“生活 / 记录 / 短视频”这类泛词。\n"
    "3. preloaded_context.market_snapshot.peer_samples 是代码预抓的同方向爆款对标视频，不是同 UP 样本，也不是 LLM 生成内容；"
    "如果这里非空，analysis.benchmark_analysis.benchmark_videos 必须优先引用这些样本；如果这里为空，允许先基于 retrieval 完成分析，但绝对禁止编造不存在的视频、BV 号或 UP 主。\n"
    "4. 禁止输出任何“先重新解析当前视频 / 调 video_briefing / 再看一次当前视频详情”之类的计划，因为当前视频已解析完成。\n"
    "5. 只有当 retrieval 返回样本不足时，才允许调用 web_search 搜索最新赛道规则或补充案例。\n"
    "6. 所有工具调用完成后直接输出 final 结构化结果，禁止无意义循环。\n\n"
    "【判定要求】\n"
    "1. 必须明确输出 performance.score，并给出爆款/低表现结论。\n"
    "2. analysis.benchmark_analysis.benchmark_videos 只能填写真实、可打开、可核验的同赛道参考视频；拿不到就返回空数组。\n"
    "3. 必须生成符合当前视频内容的新标题、优化文案脚本、下一批优先题材、具体封面建议和内容建议。\n"
    "4. 仅输出 JSON 对象，不要输出 markdown 和解释。"
)

