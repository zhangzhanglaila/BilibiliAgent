"""数据优化 Agent：拉取视频数据、入库并输出优化建议。"""
from __future__ import annotations

from statistics import mean
from typing import Dict, List

from bilibili_api import sync, video

from db import SQLiteStore
from llm_client import LLMClient
from models import OptimizationSuggestion, VideoMetrics


class OptimizationAgent:
    # 初始化优化 Agent，准备持久化快照的存储层和可选的 LLM 增强能力。
    def __init__(self, store: SQLiteStore | None = None, llm_client: LLMClient | None = None) -> None:
        self.store = store or SQLiteStore()
        self.llm = llm_client or LLMClient()

    # 用公开互动数据估算一个近似完播率，供规则诊断和优化建议参考。
    def _estimate_completion_rate(self, duration: int, view: int, like: int, coin: int, favorite: int) -> float:
        if duration <= 0:
            return 0.0
        weighted = (like * 1.0 + coin * 1.2 + favorite * 1.4) / max(view, 1)
        return min(0.95, 0.22 + weighted * 10)

    # 拉取目标视频指标并写入本地快照，失败时返回可比较的兜底样本。
    def fetch_video_metrics(self, bv_id: str) -> VideoMetrics:
        try:
            target = video.Video(bvid=bv_id)
            info = sync(target.get_info())
            stat = info.get("stat", {})
            duration = int(info.get("duration") or 0)
            completion_rate = self._estimate_completion_rate(
                duration=duration,
                view=int(stat.get("view") or 0),
                like=int(stat.get("like") or 0),
                coin=int(stat.get("coin") or 0),
                favorite=int(stat.get("favorite") or 0),
            )
            avg_view_duration = duration * completion_rate
            metrics = VideoMetrics(
                bvid=bv_id,
                title=info.get("title", "未知视频"),
                author=info.get("owner", {}).get("name", "未知UP"),
                mid=int(info.get("owner", {}).get("mid") or 0),
                view=int(stat.get("view") or 0),
                like=int(stat.get("like") or 0),
                coin=int(stat.get("coin") or 0),
                favorite=int(stat.get("favorite") or 0),
                reply=int(stat.get("reply") or 0),
                share=int(stat.get("share") or 0),
                duration=duration,
                avg_view_duration=avg_view_duration,
                like_rate=int(stat.get("like") or 0) / max(int(stat.get("view") or 1), 1),
                completion_rate=completion_rate,
                source="目标视频",
                url=f"https://www.bilibili.com/video/{bv_id}",
                extra={"estimated": True},
            )
            self.store.save_video_metrics(metrics)
            return metrics
        except Exception:
            metrics = VideoMetrics(
                bvid=bv_id,
                title="演示视频",
                author="演示UP",
                view=12000,
                like=620,
                coin=180,
                favorite=240,
                reply=86,
                share=30,
                duration=180,
                avg_view_duration=88,
                like_rate=620 / 12000,
                completion_rate=88 / 180,
                source="fallback",
                extra={"estimated": True},
            )
            self.store.save_video_metrics(metrics)
            return metrics

    # 把当前视频和对标样本做规则对比，输出可直接执行的诊断与内容建议。
    def _rule_based_diagnosis(self, current: VideoMetrics, benchmark_videos: List[VideoMetrics]) -> Dict[str, List[str] | str]:
        avg_views = mean([v.view for v in benchmark_videos]) if benchmark_videos else current.view
        avg_like_rate = mean([v.like_rate for v in benchmark_videos]) if benchmark_videos else current.like_rate
        diagnosis = []
        content_suggestions = []
        if current.view < avg_views * 0.6:
            diagnosis.append("当前播放明显低于同类爆款，标题和封面吸引力不足。")
            content_suggestions.append("前 3 秒直接抛出结果或反差观点，减少铺垫。")
        if current.completion_rate < 0.45:
            diagnosis.append("估算完播率偏低，开头钩子不够强或中段节奏偏慢。")
            content_suggestions.append("将开头控制在 8 秒内，并提前预告结尾收益点。")
        if current.like_rate < avg_like_rate * 0.8:
            diagnosis.append("点赞率偏低，说明共鸣点或可收藏价值不足。")
            content_suggestions.append("增加一条可直接复制执行的清单，提高收藏和点赞意愿。")
        if not diagnosis:
            diagnosis.append("整体数据正常，建议做标题和封面的小幅 AB 测试。")
            content_suggestions.append("保持当前内容结构，只测试标题措辞和封面文案。")
        return {
            "diagnosis": "；".join(diagnosis),
            "content_suggestions": content_suggestions,
        }

    # 执行完整优化流程，先走规则兜底，再用 LLM 对建议做结构化增强。
    def run(self, bv_id: str, benchmark_videos: List[VideoMetrics] | None = None) -> OptimizationSuggestion:
        current = self.fetch_video_metrics(bv_id)
        benchmark_videos = benchmark_videos or []
        history = self.store.get_history(bv_id, limit=5)
        rules = self._rule_based_diagnosis(current, benchmark_videos)
        fallback = {
            "diagnosis": rules["diagnosis"],
            "optimized_titles": [
                f"{current.title}：90 秒讲清最关键的 3 个点",
                f"做对这一步，{current.title} 的效果会完全不一样",
            ],
            "cover_suggestion": "封面突出结果对比，使用高反差底色 + 4~6 字大字标题，放大核心收益点。",
            "content_suggestions": rules["content_suggestions"],
        }
        benchmark_summary = (
            f"历史记录 {len(history)} 条；对比样本 {len(benchmark_videos)} 条；"
            f"当前播放 {current.view:,}，点赞率 {current.like_rate:.2%}，估算完播率 {current.completion_rate:.2%}。"
        )
        system_prompt = "你是 B 站增长顾问，请基于数据输出可执行优化建议。"
        user_prompt = (
            f"当前视频标题：{current.title}\n"
            f"当前数据：播放 {current.view}，点赞 {current.like}，投币 {current.coin}，收藏 {current.favorite}，评论 {current.reply}，"
            f"转发 {current.share}，时长 {current.duration} 秒，估算完播率 {current.completion_rate:.2%}。\n"
            f"基准总结：{benchmark_summary}\n"
            "请返回 JSON，字段包含 diagnosis, optimized_titles(2个), cover_suggestion, content_suggestions(数组)。"
        )
        data = self.llm.invoke_json(system_prompt, user_prompt, fallback)
        return OptimizationSuggestion(
            bv_id=bv_id,
            diagnosis=data.get("diagnosis", fallback["diagnosis"]),
            optimized_titles=data.get("optimized_titles", fallback["optimized_titles"])[:2],
            cover_suggestion=data.get("cover_suggestion", fallback["cover_suggestion"]),
            content_suggestions=data.get("content_suggestions", fallback["content_suggestions"]),
            benchmark_summary=benchmark_summary,
            raw_text=str(data),
        )
