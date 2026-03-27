"""选题 Agent：抓取 B 站公开数据并生成选题建议。"""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict
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
        words = [w for w in clean.split() if len(w) >= 2]
        return words[:5]

    def _competition_scores(self, videos: List[VideoMetrics]) -> None:
        bucket: Dict[str, List[VideoMetrics]] = defaultdict(list)
        for video in videos:
            tags = self._extract_keywords(video.title)
            key = tags[0] if tags else "通用"
            bucket[key].append(video)
        for _, group in bucket.items():
            total_view = sum(v.view for v in group) or 1
            competition = len(group) / total_view
            for v in group:
                v.competition_score = competition

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
        }
        for keyword, style in mapping.items():
            if keyword in title:
                return style
        return "干货"

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
                    pseudo_item = {
                        "bvid": f"tag-{name}",
                        "title": f"{name} 教程/趋势",
                        "owner": {"name": "知识分区热点", "mid": 0},
                        "stat": {
                            "view": int(search_data.get("archive_view", 0) / max(len(data), 1)),
                            "like": int(search_data.get("archive_view", 0) * 0.04 / max(len(data), 1)),
                            "coin": int(search_data.get("archive_view", 0) * 0.01 / max(len(data), 1)),
                            "favorite": int(search_data.get("archive_view", 0) * 0.02 / max(len(data), 1)),
                            "reply": int(search_data.get("archive", 0) * 3),
                            "share": int(search_data.get("archive", 0)),
                        },
                        "duration": 300,
                    }
                    results.append(self._build_metrics(pseudo_item, f"分区热榜:{partition_name or CONFIG.default_partition}"))
        return results[:10]

    def fetch_peer_up_videos(self, up_ids: Iterable[int] | None = None) -> List[VideoMetrics]:
        up_ids = list(up_ids or CONFIG.default_peer_ups)
        videos: List[VideoMetrics] = []
        for up_id in up_ids:
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
                        "duration": int(item.get("length", "0:00").split(":")[-1]) if item.get("length") else 0,
                    }
                    videos.append(self._build_metrics(payload, f"同类UP:{up_id}"))
            except Exception:
                continue
            finally:
                self._sleep()
        return videos

    def _score_video(self, video: VideoMetrics) -> float:
        traffic = math.log10(max(video.view, 1))
        interaction = video.like_rate * 100 + video.completion_rate * 10
        competition_penalty = 1 / max(video.competition_score * 100000 + 1, 1)
        return traffic + interaction + competition_penalty

    def _generate_topics(self, videos: List[VideoMetrics]) -> List[TopicIdea]:
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
            if len(ideas) >= 3:
                break
        return ideas

    def run(self, partition_name: str | None = None, up_ids: Iterable[int] | None = None) -> Dict[str, Any]:
        hot_videos = self.fetch_hot_videos()
        partition_videos = self.fetch_partition_videos(partition_name)
        peer_videos = self.fetch_peer_up_videos(up_ids)
        all_videos = hot_videos + partition_videos + peer_videos
        ideas = self._generate_topics(all_videos)
        return {
            "ideas": ideas,
            "source_count": len(all_videos),
            "videos": all_videos,
        }
