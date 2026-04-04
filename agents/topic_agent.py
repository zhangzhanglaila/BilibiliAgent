"""Topic agent: fetch public Bilibili signals and produce topic ideas."""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from copy import deepcopy
from statistics import mean
from typing import Any, Dict, Iterable, List
from urllib.parse import urlencode

from bilibili_api import hot, sync, user, video_zone
import requests

from config import CONFIG
from models import TopicIdea, VideoMetrics

PUBLIC_BILIBILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

LIFE_SEED_TOKENS = (
    "异地恋",
    "报备",
    "情侣",
    "恋爱",
    "日常",
    "生活",
    "vlog",
    "记录",
    "下班",
    "回家",
    "通勤",
    "碎碎念",
    "赶海",
    "海货",
    "海鲜",
    "潮水",
    "滩涂",
    "退潮",
    "海边",
)
SEA_HARVEST_TOKENS = (
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
SEA_HARVEST_TARGET_TOKENS = (
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
APPEARANCE_TOKENS = (
    "颜值",
    "变装",
    "卡点",
    "变速",
    "氛围感",
    "妆容",
    "穿搭",
    "特写",
)
APPEARANCE_CARDPOINT_TOKENS = ("卡点", "变速", "踩点")
APPEARANCE_TRANSFORM_TOKENS = ("变装", "换装", "前后对比", "反差")
APPEARANCE_DAILY_TOKENS = ("日常", "vlog", "生活", "通勤", "出门", "街拍")
RELATIONSHIP_SEED_TOKENS = (
    "两性",
    "情感",
    "恋爱",
    "情侣",
    "夫妻",
    "婚姻",
    "伴侣",
    "对象",
    "坦白局",
    "私密",
    "亲密",
    "相处",
    "吵架",
    "冷战",
    "分手",
    "复合",
    "前任",
    "男生",
    "女生",
    "男人",
    "女人",
    "男女",
)
RELATIONSHIP_CONFESSION_TOKENS = ("坦白局", "私密", "亲密", "秘密", "不好意思", "不敢说", "真实经历")
RELATIONSHIP_DIFFERENCE_TOKENS = ("差异", "区别", "不同", "体验", "感受", "男生", "女生", "男女")
RELATIONSHIP_CONFLICT_TOKENS = ("相处", "吵架", "冷战", "沟通", "婚姻", "夫妻", "伴侣", "边界", "安全感")
KNOWLEDGE_SEED_TOKENS = ("科普", "知识", "原理", "为什么", "误区", "区别", "差异", "解析", "解读", "真相")
REVIEW_SEED_TOKENS = ("测评", "评测", "开箱", "实测", "对比", "推荐", "避坑", "值不值", "体验")
FUN_SEED_TOKENS = ("搞笑", "整活", "吐槽", "沙雕", "抽象", "盘点", "挑战", "名场面", "翻车")


class TopicAgent:
    # 初始化选题 Agent，设置请求节流间隔。
    _sample_cache: Dict[tuple[Any, ...], tuple[float, List[VideoMetrics]]] = {}

    def __init__(self, request_interval: float | None = None) -> None:
        self.request_interval = request_interval or CONFIG.request_interval
        self.cache_ttl_seconds = max(int(CONFIG.topic_cache_ttl_seconds), 0)

    # 在连续请求外部接口之间休眠，避免请求过快。
    def _sleep(self) -> None:
        time.sleep(self.request_interval)

    # 安全执行同步接口调用，失败时返回兜底值。
    def _safe_sync(self, coro, default):
        try:
            return sync(coro)
        except Exception:
            return default

    # 返回缓存命中的样本副本，避免后续打分逻辑污染缓存里的原始对象。
    def _get_cached_videos(self, cache_key: tuple[Any, ...]) -> List[VideoMetrics] | None:
        if self.cache_ttl_seconds <= 0:
            return None
        cached = self._sample_cache.get(cache_key)
        if not cached:
            return None
        expires_at, videos = cached
        if expires_at <= time.time():
            self._sample_cache.pop(cache_key, None)
            return None
        return deepcopy(videos)

    # 写入共享短时缓存，供 Web / CLI 多次请求复用同一批样本。
    def _set_cached_videos(self, cache_key: tuple[Any, ...], videos: List[VideoMetrics]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._sample_cache[cache_key] = (time.time() + self.cache_ttl_seconds, deepcopy(videos))

    # 根据互动强度估算平均观看时长。
    def _estimate_avg_view_duration(self, duration: int, view: int, like: int, favorite: int, reply: int) -> float:
        if duration <= 0:
            return 0.0
        # 这里拿不到真实观看时长，只能用点赞/收藏/评论这些更强的互动信号做启发式估算，
        # 同时把比例限制住，避免推导出离谱的完播率。
        engagement = (like * 1.0 + favorite * 1.5 + reply * 2.0) / max(view, 1)
        estimated_ratio = min(0.92, 0.25 + engagement * 8)
        return duration * estimated_ratio

    # 把不同来源的视频数据整理成统一的 VideoMetrics 结构。
    def _build_metrics(self, item: Dict[str, Any], source: str) -> VideoMetrics:
        stat = item.get("stat", {})
        duration = int(item.get("duration") or item.get("duration_seconds") or 0)
        view = int(stat.get("view") or item.get("play") or 0)
        like = int(stat.get("like") or 0)
        favorite = int(stat.get("favorite") or 0)
        coin = int(stat.get("coin") or 0)
        reply = int(stat.get("reply") or 0)
        share = int(stat.get("share") or 0)
        avg_view_duration = self._estimate_avg_view_duration(duration, view, like, favorite, reply)
        completion_rate = min(1.0, avg_view_duration / max(duration, 1))
        like_rate = like / max(view, 1)
        owner = item.get("owner") or {}
        bvid = item.get("bvid") or item.get("short_link_v2", "")
        return VideoMetrics(
            bvid=bvid,
            title=item.get("title", "未知标题"),
            author=owner.get("name") or item.get("author", "未知UP"),
            cover=item.get("pic") or item.get("cover") or item.get("thumbnail") or "",
            mid=int(owner.get("mid") or item.get("mid") or 0),
            view=view,
            like=like,
            coin=coin,
            favorite=favorite,
            reply=reply,
            share=share,
            duration=duration,
            avg_view_duration=avg_view_duration,
            like_rate=like_rate,
            completion_rate=completion_rate,
            source=source,
            pubdate=int(item.get("pubdate") or 0),
            url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
            extra={"estimated": True},
        )

    # 从标题里提取一组简化后的关键词。
    def _extract_keywords(self, title: str) -> List[str]:
        clean = re.sub(r"[^\w\u4e00-\u9fff]", " ", title.lower())
        words = [word for word in clean.split() if len(word) >= 2]
        unique_words: List[str] = []
        for word in words:
            if word not in unique_words:
                unique_words.append(word)
        return unique_words[:6]

    # 按关键词粗略估算每类题材的竞争强度。
    def _competition_scores(self, videos: List[VideoMetrics]) -> None:
        bucket: Dict[str, List[VideoMetrics]] = defaultdict(list)
        for video in videos:
            tags = self._extract_keywords(video.title)
            key = tags[0] if tags else "通用"
            bucket[key].append(video)
        for group in bucket.values():
            total_view = sum(video.view for video in group) or 1
            competition = len(group) / total_view
            for video in group:
                video.competition_score = competition

    # 根据标题关键词猜测这条视频更像哪种创作风格。
    def _pick_video_type(self, title: str) -> str:
        mapping = {
            "教程": "教学",
            "入门": "教学",
            "原理": "干货",
            "实战": "干货",
            "搞笑": "搞笑",
            "整活": "搞笑",
            "混剪": "混剪",
            "盘点": "混剪",
            "复盘": "干货",
            "测评": "干货",
        }
        for keyword, style in mapping.items():
            if keyword in title:
                return style
        return "干货"

    # 把搜索接口里可能出现的“10万”“1.2亿”之类文本转成整数。
    def _safe_metric_int(self, value: Any) -> int:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)

        text = str(value).strip().lower().replace(",", "")
        if not text:
            return 0

        multiplier = 1
        if text.endswith("万"):
            multiplier = 10000
            text = text[:-1]
        elif text.endswith("亿"):
            multiplier = 100000000
            text = text[:-1]

        match = re.search(r"\d+(?:\.\d+)?", text)
        if not match:
            return 0
        try:
            return int(float(match.group(0)) * multiplier)
        except Exception:
            return 0

    # 调公开 Web API 拉取 JSON，便于补同方向爆款对标样本。
    def _fetch_public_json(self, url: str) -> Dict[str, Any]:
        response = requests.get(url, headers=PUBLIC_BILIBILI_HEADERS, timeout=12)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("B站公开接口返回格式无效")
        return payload

    # 从搜索结果里抽取一批候选视频。
    def _fetch_hot_peer_candidates(self, query: str, page_size: int) -> List[Dict[str, Any]]:
        params = {
            "search_type": "video",
            "keyword": query,
            "order": "click",
            "page": 1,
            "page_size": max(1, min(page_size, 20)),
        }
        payload = self._fetch_public_json(f"https://api.bilibili.com/x/web-interface/search/type?{urlencode(params)}")
        if int(payload.get("code") or -1) != 0:
            raise ValueError(payload.get("message") or "B站搜索接口失败")

        data = payload.get("data") or {}
        items = data.get("result") or []
        results: List[Dict[str, Any]] = []
        for item in items[:page_size]:
            if not isinstance(item, dict):
                continue
            bvid = str(item.get("bvid") or "").strip()
            if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bvid, flags=re.IGNORECASE):
                continue
            results.append(
                {
                    "bvid": bvid,
                    "title": re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip(),
                    "author": re.sub(r"<[^>]+>", "", str(item.get("author") or "")).strip(),
                    "cover": item.get("pic") or item.get("cover") or "",
                    "mid": int(item.get("mid") or 0),
                    "view": self._safe_metric_int(item.get("play")),
                    "like": self._safe_metric_int(item.get("like")),
                    "favorite": self._safe_metric_int(item.get("favorites")),
                    "reply": self._safe_metric_int(item.get("review")),
                    "duration": self._parse_duration(str(item.get("duration") or "")),
                    "pubdate": int(item.get("pubdate") or 0),
                    "query": query,
                }
            )
        return results

    # 用公开视频详情接口补齐真实指标，避免只依赖搜索结果里的粗略数据。
    def _fetch_hot_peer_detail(self, bvid: str) -> Dict[str, Any] | None:
        clean_bvid = str(bvid or "").strip()
        if not re.fullmatch(r"BV[0-9A-Za-z]{10}", clean_bvid, flags=re.IGNORECASE):
            return None
        payload = self._fetch_public_json(
            f"https://api.bilibili.com/x/web-interface/view?{urlencode({'bvid': clean_bvid})}"
        )
        if int(payload.get("code") or -1) != 0:
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    # 综合流量、互动和竞争度给视频打一个排序分数。
    def _score_video(self, video: VideoMetrics) -> float:
        traffic = math.log10(max(video.view, 1))
        interaction = video.like_rate * 100 + video.completion_rate * 10
        competition_penalty = 1 / max(video.competition_score * 100000 + 1, 1)
        return traffic + interaction + competition_penalty

    # 拉取全站热点视频样本。
    def fetch_hot_videos(self) -> List[VideoMetrics]:
        cached = self._get_cached_videos(("hot",))
        if cached is not None:
            return cached

        data = self._safe_sync(hot.get_hot_videos(), [])
        self._sleep()
        items = data if isinstance(data, list) else data.get("list", [])
        results = [self._build_metrics(item, "全站热榜") for item in items[:20]]
        self._set_cached_videos(("hot",), results)
        return deepcopy(results)

    # 拉取指定分区的热点样本，并转换成可比较的指标结构。
    def fetch_partition_videos(self, partition_name: str | None = None) -> List[VideoMetrics]:
        normalized_partition = CONFIG.normalize_partition(partition_name)
        cache_key = ("partition", normalized_partition)
        cached = self._get_cached_videos(cache_key)
        if cached is not None:
            return cached

        tid = CONFIG.partition_tid(normalized_partition)
        data = self._safe_sync(video_zone.get_zone_hot_tags(tid), [])
        self._sleep()
        results: List[VideoMetrics] = []
        if isinstance(data, list):
            summary_data = self._safe_sync(video_zone.get_zone_videos_count_today(tid), {})
            self._sleep()
            archive_view = int(summary_data.get("archive_view", 0)) if isinstance(summary_data, dict) else 0
            archive_count = int(summary_data.get("archive", 0)) if isinstance(summary_data, dict) else 0
            for tag in data[:5]:
                name = tag.get("tag_name")
                if not name:
                    continue
                # 分区接口更偏聚合数据，没有完整的热榜视频明细，所以这里按热词构造一条
                # 可比较的伪样本，供后面的统一打分逻辑复用。
                pseudo_item = {
                    "bvid": f"tag-{name}",
                    "title": f"{name} 教程/趋势",
                    "owner": {"name": "分区热点", "mid": 0},
                    "stat": {
                        "view": int(archive_view / max(len(data), 1)),
                        "like": int(archive_view * 0.04 / max(len(data), 1)),
                        "coin": int(archive_view * 0.01 / max(len(data), 1)),
                        "favorite": int(archive_view * 0.02 / max(len(data), 1)),
                        "reply": archive_count * 3,
                        "share": archive_count,
                    },
                    "duration": 300,
                }
                results.append(self._build_metrics(pseudo_item, f"分区热榜:{normalized_partition}"))
        trimmed = results[:10]
        self._set_cached_videos(cache_key, trimmed)
        return deepcopy(trimmed)

    # 拉取同类 UP 主的近期视频样本。
    def fetch_peer_up_videos(self, up_ids: Iterable[int] | None = None) -> List[VideoMetrics]:
        target_up_ids = list(up_ids or CONFIG.default_peer_ups)
        cache_key = ("peer", tuple(target_up_ids))
        cached = self._get_cached_videos(cache_key)
        if cached is not None:
            return cached

        videos: List[VideoMetrics] = []
        for up_id in target_up_ids:
            try:
                up = user.User(up_id)
                data = self._safe_sync(up.get_videos(ps=5), {})
                items = data.get("list", {}).get("vlist", []) if isinstance(data, dict) else []
                for item in items[:5]:
                    stat = {
                        "view": int(item.get("play") or 0),
                        "like": int(item.get("comment") or 0) * 3,
                        "coin": int(item.get("play") or 0) // 100,
                        "favorite": int(item.get("play") or 0) // 80,
                        "reply": int(item.get("comment") or 0),
                        "share": int(item.get("play") or 0) // 200,
                    }
                    payload = {
                        "bvid": item.get("bvid", ""),
                        "title": item.get("title", ""),
                        "owner": {"name": item.get("author", "同类UP"), "mid": up_id},
                        "stat": stat,
                        "duration": self._parse_duration(item.get("length", "")),
                    }
                    videos.append(self._build_metrics(payload, f"同类UP:{up_id}"))
            except Exception:
                continue
            finally:
                self._sleep()
        self._set_cached_videos(cache_key, videos)
        return deepcopy(videos)

    # 按“同方向爆款”而不是“同UP近期视频”拉取对标样本。
    def fetch_hot_peer_videos(
        self,
        queries: Iterable[str] | None = None,
        exclude_bvid: str = "",
        limit: int = 6,
        recent_days: int = 30,
        min_view: int = 50000,
        min_like: int = 1000,
    ) -> List[VideoMetrics]:
        normalized_queries = [str(query or "").strip() for query in (queries or []) if str(query or "").strip()]
        if not normalized_queries:
            return []

        clean_exclude_bvid = str(exclude_bvid or "").strip().lower()
        normalized_limit = max(1, min(limit, 10))
        normalized_recent_days = max(int(recent_days or 0), 0)
        normalized_min_view = max(int(min_view or 0), 0)
        normalized_min_like = max(int(min_like or 0), 0)
        cache_key = (
            "hot_peer",
            tuple(normalized_queries[:4]),
            clean_exclude_bvid,
            normalized_limit,
            normalized_recent_days,
            normalized_min_view,
            normalized_min_like,
        )
        cached = self._get_cached_videos(cache_key)
        if cached is not None:
            return cached[:normalized_limit]

        cutoff_ts = int(time.time()) - normalized_recent_days * 86400 if normalized_recent_days else 0
        candidate_map: Dict[str, Dict[str, Any]] = {}

        for query in normalized_queries[:4]:
            try:
                candidates = self._fetch_hot_peer_candidates(query, page_size=max(normalized_limit * 3, 10))
            except Exception:
                candidates = []
            finally:
                self._sleep()

            for candidate in candidates:
                bvid = str(candidate.get("bvid") or "").strip()
                if not bvid or (clean_exclude_bvid and bvid.lower() == clean_exclude_bvid):
                    continue
                pubdate = int(candidate.get("pubdate") or 0)
                if cutoff_ts and pubdate and pubdate < cutoff_ts:
                    continue
                candidate_score = (
                    int(candidate.get("view") or 0),
                    int(candidate.get("like") or 0),
                    int(candidate.get("favorite") or 0),
                    pubdate,
                )
                current = candidate_map.get(bvid)
                if current is None or candidate_score > current.get("_score", (0, 0, 0, 0)):
                    candidate_copy = dict(candidate)
                    candidate_copy["_score"] = candidate_score
                    candidate_map[bvid] = candidate_copy

        ordered_candidates = sorted(
            candidate_map.values(),
            key=lambda item: item.get("_score", (0, 0, 0, 0)),
            reverse=True,
        )

        videos: List[VideoMetrics] = []
        for candidate in ordered_candidates[: max(normalized_limit * 3, 12)]:
            detail: Dict[str, Any] | None = None
            try:
                detail = self._fetch_hot_peer_detail(str(candidate.get("bvid") or ""))
            except Exception:
                detail = None
            finally:
                self._sleep()

            if isinstance(detail, dict) and detail:
                pubdate = int(detail.get("pubdate") or candidate.get("pubdate") or 0)
                if cutoff_ts and pubdate and pubdate < cutoff_ts:
                    continue
                metric = self._build_metrics(detail, f"同方向爆款:{candidate.get('query', '')}")
                metric.pubdate = pubdate
                metric.url = f"https://www.bilibili.com/video/{metric.bvid}" if metric.bvid else ""
                metric.extra["estimated"] = False
            else:
                pubdate = int(candidate.get("pubdate") or 0)
                if cutoff_ts and pubdate and pubdate < cutoff_ts:
                    continue
                fallback_payload = {
                    "bvid": candidate.get("bvid", ""),
                    "title": candidate.get("title", ""),
                    "owner": {
                        "name": candidate.get("author", ""),
                        "mid": int(candidate.get("mid") or 0),
                    },
                    "stat": {
                        "view": int(candidate.get("view") or 0),
                        "like": int(candidate.get("like") or 0),
                        "favorite": int(candidate.get("favorite") or 0),
                        "reply": int(candidate.get("reply") or 0),
                        "coin": 0,
                        "share": 0,
                    },
                    "duration": int(candidate.get("duration") or 0),
                    "pubdate": pubdate,
                    "pic": candidate.get("cover", ""),
                }
                metric = self._build_metrics(fallback_payload, f"同方向爆款搜索:{candidate.get('query', '')}")
                metric.pubdate = pubdate

            if metric.view < normalized_min_view or metric.like < normalized_min_like:
                continue
            videos.append(metric)
            if len(videos) >= max(normalized_limit, 8):
                break

        self._competition_scores(videos)
        ranked = sorted(
            videos,
            key=lambda item: (item.view, item.like, item.favorite, item.like_rate, item.pubdate),
            reverse=True,
        )
        trimmed = ranked[:normalized_limit]
        self._set_cached_videos(cache_key, trimmed)
        return deepcopy(trimmed)

    # 把视频时长文本解析成秒数。
    def _parse_duration(self, raw: str) -> int:
        if not raw:
            return 0
        parts = [int(part) for part in raw.split(":") if part.isdigit()]
        if not parts:
            return 0
        if len(parts) == 1:
            return parts[0]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    # 基于热点样本生成一组趋势选题建议。
    def _generate_trending_topics(self, videos: List[VideoMetrics]) -> List[TopicIdea]:
        self._competition_scores(videos)
        enriched = sorted(videos, key=self._score_video, reverse=True)
        ideas: List[TopicIdea] = []
        seen = set()
        for video in enriched:
            keywords = self._extract_keywords(video.title)
            if not keywords:
                continue
            core = " / ".join(keywords[:2])
            # 用前两个关键词做一层去重，避免前三个选题都挤在同一类近似主题里。
            if core in seen:
                continue
            seen.add(core)
            ideas.append(
                TopicIdea(
                    topic=f"{core} 的高效做法",
                    reason=(
                        f"播放 {video.view:,}、点赞率 {video.like_rate:.2%}、"
                        f"估算完播率 {video.completion_rate:.2%}、竞争度 {video.competition_score:.6f}，"
                        f"说明该题材有流量且竞争相对可控。"
                    ),
                    video_type=self._pick_video_type(video.title),
                    keywords=keywords,
                    score=self._score_video(video),
                )
            )
            if len(ideas) >= 6:
                break
        return ideas

    # 清洗用户输入的种子主题文本。
    def _clean_seed_topic(self, seed_topic: str) -> str:
        cleaned = re.sub(r"[【\[].*?[】\]]", "", seed_topic or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
        return cleaned[:60]

    # 从样本里筛出和种子主题更相关的视频。
    def _find_related_videos(self, seed_topic: str, videos: List[VideoMetrics]) -> List[VideoMetrics]:
        keywords = self._extract_keywords(seed_topic)
        if not keywords:
            return []
        related = []
        for video in videos:
            title = video.title.lower()
            if any(keyword in title for keyword in keywords[:4]):
                related.append(video)
        return sorted(related, key=self._score_video, reverse=True)

    # 为种子主题变体生成解释文案，说明为什么值得做。
    def _build_seed_reason(
        self,
        seed_topic: str,
        variant: str,
        related_videos: List[VideoMetrics],
        partition_name: str | None,
        up_ids: Iterable[int] | None,
    ) -> str:
        if related_videos:
            sample_count = len(related_videos)
            avg_views = int(mean(video.view for video in related_videos))
            avg_like_rate = mean(video.like_rate for video in related_videos)
            avg_completion_rate = mean(video.completion_rate for video in related_videos)
            return (
                f"围绕当前链接主题《{seed_topic}》做 {variant}，命中 {sample_count} 条相关样本；"
                f"样本平均播放 {avg_views:,}、平均点赞率 {avg_like_rate:.2%}、"
                f"估算完播率 {avg_completion_rate:.2%}，适合继续做延展内容。"
            )

        partition_text = partition_name or CONFIG.default_partition
        peer_count = len(list(up_ids or CONFIG.default_peer_ups))
        return (
            f"围绕当前链接主题《{seed_topic}》做 {variant}，优先结合分区 {partition_text} "
            f"和 {peer_count} 个同类 UP 样本做延展，避免直接跟全站热榜撞题。"
        )

    # 识别种子主题属于哪种创作场景。
    def _seed_topic_mode(self, cleaned: str) -> str:
        if any(token in cleaned for token in ["第1条、第2条、第3条", "做系列内容时", "做成系列内容时"]):
            return "series_plan"
        if any(token in cleaned for token in ["开场动作", "进推荐", "别一上来就"]):
            return "opening_hook"
        if any(token in cleaned for token in ["第一条视频跳什么", "先跳什么", "跳什么"]):
            return "dance_first_video"
        if any(token in cleaned for token in ["第一条视频先做什么", "第一条视频先拍什么", "第一条该怎么做", "先拍什么"]):
            return "first_video"
        return "general"

    # 从种子主题里提取更稳定的主体描述。
    def _seed_topic_subject(self, cleaned: str) -> str:
        markers = [
            "第一条视频",
            "第一条",
            "做系列内容时",
            "做成系列内容时",
            "别一上来就",
            "先做什么",
            "先拍什么",
            "先跳什么",
            "更容易起量",
            "更容易进推荐",
        ]
        for marker in markers:
            index = cleaned.find(marker)
            if index > 0:
                return cleaned[:index].strip(" ：，,。")

        if "：" in cleaned:
            return cleaned.split("：", 1)[0].replace("别直接硬拍", "").strip(" ：，,。")
        return cleaned

    # 把策略型种子主题整理成更适合扩写新方向的主体表达。
    def _seed_title_subject(self, cleaned: str) -> str:
        base = self._seed_topic_subject(cleaned)
        patterns = [
            r"第一条(?:视频)?",
            r"第[1一二三123]+条",
            r"做成系列内容时",
            r"做系列内容时",
            r"别一上来就",
            r"先做哪种切口",
            r"哪种切口",
            r"先做",
            r"先(?:做|拍|跳)什么",
            r"更容易(?:起量|进推荐)",
            r"更容易被点进来",
            r"下一条最适合拍什么",
            r"同样是",
            r"怎么(?:排|拍|做)?",
            r"如何",
            r"为什么",
            r"哪(?:种|类|一条)",
            r"切口",
            r"表达",
            r"镜头顺序",
            r"开场动作",
            r"前三秒",
            r"教程",
            r"攻略",
        ]
        for pattern in patterns:
            base = re.sub(pattern, " ", base, flags=re.IGNORECASE)
        base = re.sub(r"(视频|内容|账号)$", "", base)
        base = re.sub(r"\s+", " ", base).strip(" ：，,。")
        if base in {"先做", "切口", "表达", "先做 切口"}:
            return ""
        return base

    # 把长句型标题压成更适合继续扩写的主体，避免把整句原题机械拼进后续题材。
    def _compact_seed_subject(self, text: str) -> str:
        clean = self._clean_seed_topic(text)
        if not clean:
            return ""
        parts = re.split(r"[，,。！？!？；;]", clean)
        for part in parts:
            value = self._clean_seed_topic(part)
            if len(value) >= 2:
                return value
        return clean

    # 判断当前主题是否属于赶海 / 海货收获这类记录内容。
    def _is_sea_harvest_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in SEA_HARVEST_TOKENS)

    # 从赶海标题里提取更适合落地成选题的收获对象。
    def _extract_sea_harvest_target(self, cleaned: str) -> str:
        text = cleaned.lower()
        for token in SEA_HARVEST_TARGET_TOKENS:
            if token in text:
                return token
        return ""

    # 判断当前主题是否属于颜值展示 / 卡点 / 变装赛道。
    def _is_appearance_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in APPEARANCE_TOKENS)

    # 基于题材词判断颜值内容更接近哪种延展结构。
    def _appearance_seed_mode(self, cleaned: str) -> str:
        text = cleaned.lower()
        if any(token in text for token in APPEARANCE_CARDPOINT_TOKENS):
            return "cardpoint"
        if any(token in text for token in APPEARANCE_TRANSFORM_TOKENS):
            return "transform"
        if any(token in text for token in APPEARANCE_DAILY_TOKENS):
            return "daily"
        return "general"

    # 为颜值类标题生成同赛道、无建议口吻的系列化延展方向。
    def _build_appearance_seed_candidates(self, cleaned: str) -> List[tuple[str, str]]:
        mode = self._appearance_seed_mode(cleaned)
        if mode == "cardpoint":
            return [
                ("颜值特写", "近景颜值特写卡点"),
                ("场景切换", "场景切换颜值卡点"),
                ("前后反差", "前后反差变装卡点"),
            ]
        if mode == "transform":
            return [
                ("变装开场", "开场素颜到成片的变装卡点"),
                ("场景衔接", "不同场景衔接的变装切换"),
                ("特写反差", "近景特写和全身切换的变装片段"),
            ]
        if mode == "daily":
            return [
                ("氛围日常", "通勤路上的颜值氛围感片段"),
                ("场景特写", "室内外切换的颜值日常特写"),
                ("出门反差", "出门前后衔接的颜值反差记录"),
            ]
        return [
            ("颜值特写", "镜头贴脸的颜值特写片段"),
            ("场景切换", "场景切换衔接的氛围感展示"),
            ("前后反差", "前后状态反差的颜值展示片段"),
        ]

    # 判断当前种子主题是否更接近日常记录型内容。
    def _is_life_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in LIFE_SEED_TOKENS)

    # 为生活区 / 日常型主题构造更自然的新方向。
    def _build_life_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        base = self._compact_seed_subject(subject) or "日常"
        text = f"{cleaned} {base}"
        if "异地恋" in text and "报备" in text:
            return [
                ("关系细节", "异地恋报备里那些看起来普通、却最能给人安全感的回应"),
                ("情绪变化", "从早安到晚安的报备语气变化，为什么会让异地恋感受完全不同"),
                ("相处延伸", "异地恋里真正让人反复想起的，往往不是大事，而是这些陪伴感很强的小瞬间"),
            ]
        if "异地恋" in text:
            return [
                ("情绪切口", "异地恋最熬人的时刻，往往不是见不到面，而是这些没法同步的小瞬间"),
                ("回应细节", "异地恋里真正能把人安抚下来的，通常只是一个很小但很及时的回应"),
                ("见面反差", "从见面倒计时到分别之后，异地恋情绪起伏最大的那几天"),
            ]
        if "报备" in text:
            return [
                ("边界讨论", "报备为什么有人觉得安心，有人却会觉得压力越来越大"),
                ("时段差异", "情侣一天里的不同报备时刻，真正传递出来的情绪完全不一样"),
                ("相处平衡", "把报备放回真实关系里，陪伴感和边界感到底该怎么拿捏"),
            ]
        return [
            ("真实场景", f"{base}里最能说明关系状态的那个日常瞬间"),
            ("情绪变化", f"把{base}放进一天的真实节奏里，情绪变化会特别明显"),
            ("细节共鸣", f"{base}看起来普通，但真正让人有共鸣的往往是那些没被明说的小反应"),
        ]

    # 为赶海 / 海货收获类标题生成更贴近内容本身的后续方向。
    def _build_sea_harvest_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        lead = self._compact_seed_subject(subject) or "这次赶海"
        target = self._extract_sea_harvest_target(cleaned)
        if target and target != "海货":
            return [
                ("潮位追货", f"跟着下一波潮水继续找{target}的一次赶海记录"),
                ("收获特写", f"把这次赶海碰到的{target}单独拍一条近景收获记录"),
                ("同滩复拍", "同一片滩涂换个潮位再去一次，看看还能碰到哪些海货"),
            ]
        return [
            ("潮位追货", f"{lead}之后继续跟着潮水找海货的一次赶海记录"),
            ("收获特写", "把今天赶海最有画面感的一样收获单独拍一条近景"),
            ("同滩复拍", "同一片海边隔一天再去一次，看看下一次能捡到什么"),
        ]

    # 判断当前主题是否属于两性 / 亲密关系 / 夫妻相处类内容。
    def _is_relationship_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in RELATIONSHIP_SEED_TOKENS)

    # 基于题材词判断两性关系内容更适合哪种延展方向。
    def _relationship_seed_mode(self, cleaned: str) -> str:
        text = cleaned.lower()
        if any(token in text for token in RELATIONSHIP_CONFESSION_TOKENS):
            return "confession"
        if any(token in text for token in RELATIONSHIP_DIFFERENCE_TOKENS):
            return "difference"
        if any(token in text for token in RELATIONSHIP_CONFLICT_TOKENS):
            return "interaction"
        return "general"

    # 从两性 / 关系话题中提炼更适合直接做视频标题的主轴。
    def _relationship_seed_anchor(self, cleaned: str, subject: str) -> str:
        text = f"{cleaned} {subject}".lower()
        if "夫妻" in text and "坦白局" in text:
            return "夫妻坦白局"
        if "坦白局" in text:
            return "亲密关系坦白局"
        if any(token in text for token in ["两性", "男女"]) and any(token in text for token in ["差异", "区别", "体验"]):
            return "男女体验差异"
        if "夫妻" in text or "婚姻" in text:
            return "夫妻相处"
        if any(token in text for token in ["情侣", "恋爱", "对象", "伴侣"]):
            return "亲密关系相处"
        base = self._compact_seed_subject(subject)
        return base or "亲密关系话题"

    # 为两性 / 夫妻坦白局这类主题生成更贴近 B 站生态的延展题材。
    def _build_relationship_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        anchor = self._relationship_seed_anchor(cleaned, subject)
        mode = self._relationship_seed_mode(cleaned)
        if mode == "confession":
            return [
                ("坦白视角", f"{anchor}：那些平时不会主动聊、但每对伴侣迟早都会碰到的尴尬瞬间"),
                ("体验差异", "同一个亲密话题，男生和女生的真实感受到底差在哪"),
                ("相处细节", "关系里最消耗亲密感的，往往不是大矛盾，而是这些说不出口的小事"),
            ]
        if mode == "difference":
            return [
                ("体验对照", f"{anchor}里最常见的一种错位感受"),
                ("误区拆解", "为什么明明是同一件事，男生和女生的体感会完全不同"),
                ("沟通落点", "那些看起来是小问题，最后却最容易变成关系内耗的差异"),
            ]
        if mode == "interaction":
            return [
                ("冲突根源", f"{anchor}里最容易反复卡住关系的一类沟通瞬间"),
                ("双方视角", "同一场争执里，男生视角和女生视角到底各自在意什么"),
                ("修复细节", "真正让关系缓和下来的，往往是那些很少被认真说开的细节"),
            ]
        return [
            ("话题延伸", f"{anchor}背后那些观众更想继续听下去的真实处境"),
            ("视角补充", "站到另一半视角之后，很多看法为什么会完全变掉"),
            ("细节深挖", "亲密关系里最难开口的，常常不是原则问题，而是这些细微感受"),
        ]

    # 判断当前主题是否更接近知识拆解 / 科普说明类内容。
    def _is_knowledge_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in KNOWLEDGE_SEED_TOKENS)

    # 为知识 / 科普型标题构造更像 B 站内容的后续题材。
    def _build_knowledge_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        base = self._compact_seed_subject(subject) or "这个知识点"
        text = cleaned.lower()
        if any(token in text for token in ["差异", "区别", "不同", "对比"]):
            return [
                ("核心差异", f"{base}真正拉开体验差距的，往往不是表面上那一点区别"),
                ("误区拆解", f"很多人以为自己懂了{base}，其实最容易理解反的是这一步"),
                ("场景落地", f"把{base}放进真实场景里，结论为什么会和想象中不一样"),
            ]
        if any(token in text for token in ["误区", "谣言", "真相"]):
            return [
                ("误区来源", f"{base}为什么总会被讲偏，问题通常出在最前面的那个判断"),
                ("真实情况", f"围绕{base}最容易被忽略的真实前提到底是什么"),
                ("延伸追问", f"如果把{base}继续往下讲一步，评论区最容易追问的会是哪层"),
            ]
        return [
            ("问题拆解", f"{base}最值得单独讲清楚的，不是结论本身，而是中间那层逻辑"),
            ("场景验证", f"把{base}放进真实案例里，很多人的理解会立刻发生变化"),
            ("延伸问题", f"围绕{base}继续往前追问一步，才是观众真正想知道的后半段"),
        ]

    # 判断当前主题是否更接近测评 / 对比 / 消费决策内容。
    def _is_review_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in REVIEW_SEED_TOKENS)

    # 为测评 / 对比类主题生成更具体的后续题材。
    def _build_review_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        base = self._compact_seed_subject(subject) or "这个东西"
        return [
            ("真实体验", f"{base}放进真实使用场景后，优缺点会比参数表里明显得多"),
            ("取舍重点", f"围绕{base}最容易买错的，不是贵和便宜，而是没想清楚自己的使用取舍"),
            ("对比延伸", f"同预算继续看{base}，真正拉开差距的往往是这些不显眼的小体验"),
        ]

    # 判断当前主题是否更接近搞笑 / 整活 / 反应类内容。
    def _is_fun_seed_topic(self, cleaned: str) -> bool:
        text = f"{cleaned} {self._seed_title_subject(cleaned)}".lower()
        return any(token in text for token in FUN_SEED_TOKENS)

    # 为整活 / 搞笑类标题生成更自然的延展题材。
    def _build_fun_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        base = self._compact_seed_subject(subject) or "这类整活内容"
        return [
            ("反应延伸", f"{base}放进真实反应里，最容易把效果拉满的往往是第二个人的表情"),
            ("场景升级", f"同样的{base}换到更具体的场景里，笑点会比原版更集中"),
            ("评论区接梗", f"{base}最适合接着拍的一条，通常就是评论区已经开始帮你补完的那个梗"),
        ]

    # 为未命中特定赛道的主题生成较通顺的通用延展题材。
    def _build_general_seed_candidates(self, cleaned: str, subject: str) -> List[tuple[str, str]]:
        base = self._compact_seed_subject(subject) or self._compact_seed_subject(cleaned) or "这类内容"
        return [
            ("核心切口", f"{base}背后最容易被忽略的那层真实原因"),
            ("场景补充", f"把{base}放回具体场景里，很多人的反应会和嘴上说的完全不一样"),
            ("延伸话题", f"和{base}相关的下一层问题，往往才是观众更想继续听下去的部分"),
        ]

    # 围绕种子主题构造几种可执行的选题变体。
    def _build_seed_candidates(self, cleaned: str) -> List[tuple[str, str]]:
        subject = self._seed_topic_subject(cleaned)
        title_subject = self._seed_title_subject(cleaned) or subject

        if self._is_appearance_seed_topic(cleaned):
            return self._build_appearance_seed_candidates(cleaned)

        if self._is_sea_harvest_seed_topic(cleaned):
            return self._build_sea_harvest_seed_candidates(cleaned, title_subject or subject)

        if self._is_relationship_seed_topic(cleaned):
            return self._build_relationship_seed_candidates(cleaned, title_subject or subject)

        if self._is_life_seed_topic(cleaned):
            return self._build_life_seed_candidates(cleaned, title_subject or subject)

        if self._is_knowledge_seed_topic(cleaned):
            return self._build_knowledge_seed_candidates(cleaned, title_subject or subject)

        if self._is_review_seed_topic(cleaned):
            return self._build_review_seed_candidates(cleaned, title_subject or subject)

        if self._is_fun_seed_topic(cleaned):
            return self._build_fun_seed_candidates(cleaned, title_subject or subject)

        return self._build_general_seed_candidates(cleaned, title_subject or subject)

    # 基于用户种子主题生成优先级更高的选题结果。
    def _generate_seed_topics(
        self,
        seed_topic: str | None,
        videos: List[VideoMetrics],
        partition_name: str | None,
        up_ids: Iterable[int] | None,
    ) -> List[TopicIdea]:
        if not seed_topic:
            return []

        cleaned = self._clean_seed_topic(seed_topic)
        if not cleaned:
            return []

        # 用户已经给了明确方向时，优先保留这个意图，只借市场样本去扩展相邻变体，
        # 而不是直接被热点结果覆盖掉。
        related_videos = self._find_related_videos(cleaned, videos)
        keywords = self._extract_keywords(cleaned) or [cleaned[:12]]
        base_style = self._pick_video_type(cleaned)
        if related_videos:
            related_style = self._pick_video_type(related_videos[0].title)
            if related_style:
                base_style = related_style

        candidates = self._build_seed_candidates(cleaned)

        ideas: List[TopicIdea] = []
        for index, (variant, topic) in enumerate(candidates):
            idea_keywords = list(dict.fromkeys(keywords + [variant]))[:6]
            score = 100 - index * 3
            ideas.append(
                TopicIdea(
                    topic=topic,
                    reason=self._build_seed_reason(cleaned, variant, related_videos, partition_name, up_ids),
                    video_type=base_style,
                    keywords=idea_keywords,
                    score=score,
                )
            )
        return ideas

    # 合并优先结果和补充结果，并按顺序去重截断。
    def _merge_ideas(self, preferred: List[TopicIdea], fallback: List[TopicIdea], limit: int = 3) -> List[TopicIdea]:
        result: List[TopicIdea] = []
        seen = set()
        for idea in preferred + fallback:
            key = idea.topic.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(idea)
            if len(result) >= limit:
                break
        return result

    # 执行完整选题流程，汇总热点、分区和同类账号样本后产出最终建议。
    def run(
        self,
        partition_name: str | None = None,
        up_ids: Iterable[int] | None = None,
        seed_topic: str | None = None,
    ) -> Dict[str, Any]:
        hot_videos = self.fetch_hot_videos()
        partition_videos = self.fetch_partition_videos(partition_name)
        peer_videos = self.fetch_peer_up_videos(up_ids)
        all_videos = hot_videos + partition_videos + peer_videos

        preferred = self._generate_seed_topics(seed_topic, all_videos, partition_name, up_ids)
        trending = self._generate_trending_topics(all_videos)
        # 先用用户种子主题的结果，数量不够时再拿趋势结果补齐前三个建议。
        ideas = self._merge_ideas(preferred, trending, limit=3)

        return {
            "ideas": ideas,
            "source_count": len(all_videos),
            "videos": all_videos,
            "seed_topic": seed_topic or "",
        }
