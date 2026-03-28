"""Topic agent: fetch public Bilibili signals and produce topic ideas."""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from statistics import mean
from typing import Any, Dict, Iterable, List

from bilibili_api import hot, sync, user, video_zone

from config import CONFIG
from models import TopicIdea, VideoMetrics


class TopicAgent:
    def __init__(self, request_interval: float | None = None) -> None:
        self.request_interval = request_interval or CONFIG.request_interval

    def _sleep(self) -> None:
        time.sleep(self.request_interval)

    def _safe_sync(self, coro, default):
        try:
            return sync(coro)
        except Exception:
            return default

    def _estimate_avg_view_duration(self, duration: int, view: int, like: int, favorite: int, reply: int) -> float:
        if duration <= 0:
            return 0.0
        engagement = (like * 1.0 + favorite * 1.5 + reply * 2.0) / max(view, 1)
        estimated_ratio = min(0.92, 0.25 + engagement * 8)
        return duration * estimated_ratio

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

    def _extract_keywords(self, title: str) -> List[str]:
        clean = re.sub(r"[^\w\u4e00-\u9fff]", " ", title.lower())
        words = [word for word in clean.split() if len(word) >= 2]
        unique_words: List[str] = []
        for word in words:
            if word not in unique_words:
                unique_words.append(word)
        return unique_words[:6]

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

    def _score_video(self, video: VideoMetrics) -> float:
        traffic = math.log10(max(video.view, 1))
        interaction = video.like_rate * 100 + video.completion_rate * 10
        competition_penalty = 1 / max(video.competition_score * 100000 + 1, 1)
        return traffic + interaction + competition_penalty

    def fetch_hot_videos(self) -> List[VideoMetrics]:
        data = self._safe_sync(hot.get_hot_videos(), [])
        self._sleep()
        items = data if isinstance(data, list) else data.get("list", [])
        return [self._build_metrics(item, "全站热榜") for item in items[:20]]

    def fetch_partition_videos(self, partition_name: str | None = None) -> List[VideoMetrics]:
        tid = CONFIG.partition_tid(partition_name)
        data = self._safe_sync(video_zone.get_zone_hot_tags(tid), [])
        self._sleep()
        results: List[VideoMetrics] = []
        if isinstance(data, list):
            for tag in data[:5]:
                name = tag.get("tag_name")
                if not name:
                    continue
                search_data = self._safe_sync(video_zone.get_zone_videos_count_today(tid), {})
                self._sleep()
                if isinstance(search_data, dict):
                    archive_view = int(search_data.get("archive_view", 0))
                    archive_count = int(search_data.get("archive", 0))
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
                    results.append(self._build_metrics(pseudo_item, f"分区热榜:{partition_name or CONFIG.default_partition}"))
        return results[:10]

    def fetch_peer_up_videos(self, up_ids: Iterable[int] | None = None) -> List[VideoMetrics]:
        target_up_ids = list(up_ids or CONFIG.default_peer_ups)
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
        return videos

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

    def _clean_seed_topic(self, seed_topic: str) -> str:
        cleaned = re.sub(r"[【\[].*?[】\]]", "", seed_topic or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
        return cleaned[:60]

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

    def _build_seed_candidates(self, cleaned: str) -> List[tuple[str, str]]:
        mode = self._seed_topic_mode(cleaned)
        subject = self._seed_topic_subject(cleaned)
        base_subject = subject or cleaned or "这类内容"

        if mode == "dance_first_video":
            return [
                ("第一条起号", f"{base_subject}第一条视频先跳什么更容易起量"),
                ("开场动作", f"{base_subject}别一上来就硬跳：先做哪种开场动作更容易进推荐"),
                ("系列规划", f"{base_subject}做系列内容时，第1条、第2条、第3条分别跳什么"),
            ]
        if mode == "opening_hook":
            return [
                ("开场动作", f"{base_subject}前三秒先放什么，更容易把人留下来"),
                ("镜头顺序", f"{base_subject}别一上来就铺满信息：镜头顺序怎么排更容易进推荐"),
                ("系列规划", f"{base_subject}想做成系列时，哪一条最适合先发"),
            ]
        if mode == "series_plan":
            return [
                ("系列规划", f"{base_subject}做成系列内容时，第1条、第2条、第3条分别拍什么"),
                ("起量切口", f"{base_subject}做系列别乱发，先从哪一条开始最容易起量"),
                ("互动放大", f"{base_subject}做系列时，哪一类互动点最容易带下一条"),
            ]
        if mode == "first_video":
            return [
                ("第一条起号", f"{base_subject}第一条视频先做什么更容易起量"),
                ("切口测试", f"别直接硬拍 {base_subject}：先做哪种切口更容易进推荐"),
                ("系列规划", f"{base_subject}做成系列内容时，第1条、第2条、第3条分别拍什么"),
            ]
        return [
            ("起量切口", f"{cleaned}先做哪种切口更容易起量"),
            ("表达角度", f"同样是 {cleaned}，哪种表达更容易被点进来"),
            ("系列规划", f"{cleaned}如果做成系列，下一条最适合拍什么"),
        ]

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
        ideas = self._merge_ideas(preferred, trending, limit=3)

        return {
            "ideas": ideas,
            "source_count": len(all_videos),
            "videos": all_videos,
            "seed_topic": seed_topic or "",
        }
