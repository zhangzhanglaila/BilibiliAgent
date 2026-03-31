"""数据优化 Agent：拉取视频数据、入库并输出优化建议。"""
from __future__ import annotations

import re
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

    # 统一清洗标题文本，避免模板替换后残留多余空格和标点。
    def _clean_text(self, text: str) -> str:
        value = re.sub(r"\s+", " ", text or "")
        return value.strip(" ，,。.;；:：-_")

    # 从标题里提取“第 x 期”这类期数前缀，保留原视频常见的连载感。
    def _extract_episode_prefix(self, title: str) -> tuple[str, str]:
        clean = self._clean_text(title)
        match = re.match(r"^(第\s*\d+\s*期)[：:\s-]*", clean)
        if not match:
            return "", clean
        return self._clean_text(match.group(1)), self._clean_text(clean[match.end() :])

    # 统一处理标题前缀拼接，避免多空格和孤立冒号。
    def _join_episode_prefix(self, episode: str, text: str) -> str:
        body = self._clean_text(text)
        prefix = self._clean_text(episode)
        return f"{prefix} {body}".strip() if prefix else body

    # 拆开标题主语和后半段细节，方便后续按生活流 / 实测流重新组织表达。
    def _split_title_parts(self, title: str) -> tuple[str, str, str]:
        episode, remainder = self._extract_episode_prefix(title)
        if "：" in remainder:
            subject, detail = remainder.split("：", 1)
        elif ":" in remainder:
            subject, detail = remainder.split(":", 1)
        else:
            subject, detail = remainder, ""
        return episode, self._clean_text(subject), self._clean_text(detail)

    # 让标题主语更像正常 B 站陈述句，而不是对用户说话。
    def _normalize_subject(self, subject: str) -> str:
        clean = self._clean_text(subject)
        clean = re.sub(r"^(你的|我的|我们|你们)", "", clean)
        clean = re.sub(r"(怎么|如何|为什么|教程|攻略|技巧|方法)$", "", clean)
        return self._clean_text(clean) or "这条内容"

    # 拆出生活类标题里按流程记录的小片段。
    def _extract_detail_segments(self, detail: str) -> List[str]:
        clean = self._clean_text(detail)
        if not clean:
            return []
        parts = re.split(r"[+＋|｜/]", clean)
        segments: List[str] = []
        for part in parts:
            value = self._clean_text(part)
            if len(value) < 2 or value in segments:
                continue
            segments.append(value)
        return segments[:8]

    # 判断当前标题是否属于生活化 vlog / 日常记录风格。
    def _is_life_record_title(self, title: str) -> bool:
        clean = self._clean_text(title).lower()
        life_tokens = [
            "异地恋",
            "报备",
            "日常",
            "vlog",
            "记录",
            "女友",
            "男友",
            "情侣",
            "酒店",
            "拍照",
            "外卖",
            "清吧",
            "早餐",
            "早饭",
            "午饭",
            "晚饭",
            "通勤",
            "下班",
            "回家",
            "旅行",
        ]
        segment_count = len(self._extract_detail_segments(clean))
        return any(token in clean for token in life_tokens) or segment_count >= 3

    # 识别更偏实测 / 对比 / 经验总结的内容题型。
    def _is_result_record_title(self, title: str) -> bool:
        clean = self._clean_text(title).lower()
        return any(
            token in clean
            for token in ["实测", "测试", "对比", "开箱", "测评", "拆解", "复盘", "效率", "工具", "方法", "教程"]
        )

    # 从细节片段里挑一个更有情绪或画面感的节点，避免机械平铺。
    def _pick_detail_highlight(self, segments: List[str]) -> str:
        if not segments:
            return ""
        emotion_tokens = ["冷", "累", "困", "忙", "赶", "撑", "饿", "晚", "雨", "风", "拍照", "回家", "清吧"]
        for segment in segments:
            if any(token in segment for token in emotion_tokens):
                return segment
        return segments[min(1, len(segments) - 1)]

    # 把多个生活片段拼成顺一点的并列表达，避免重复和生硬连接词。
    def _join_detail_segments(self, *parts: str) -> str:
        values: List[str] = []
        for part in parts:
            clean = self._clean_text(part)
            if not clean or clean in values:
                continue
            values.append(clean)
        return "、".join(values)

    # 为生活 / vlog 类型生成更像 B 站生活区的叙事型标题。
    def _build_life_record_titles(self, title: str) -> List[str]:
        episode, subject, detail = self._split_title_parts(title)
        subject = self._normalize_subject(subject)
        segments = self._extract_detail_segments(detail)
        first = segments[0] if segments else "早饭"
        highlight = self._pick_detail_highlight(segments) or first
        last = segments[-1] if segments else "回家"

        if "报备" in subject or "异地恋" in subject:
            titles = [
                self._join_episode_prefix(
                    episode,
                    f"{subject}：{self._join_detail_segments(first, highlight, last)}，今天的小事都想慢慢告诉你",
                ),
                self._join_episode_prefix(episode, f"{subject}：从{first}到{last}，把这一天一件件报备给你"),
            ]
        else:
            titles = [
                self._join_episode_prefix(
                    episode,
                    f"{subject}：{self._join_detail_segments(first, highlight, last)}，今天又是满满当当的一天",
                ),
                self._join_episode_prefix(episode, f"{subject}：从{first}到{last}，顺手把这一天慢慢记下来"),
            ]
        return [self._clean_text(item) for item in titles if self._clean_text(item)]

    # 为实测 / 经验型内容生成陈述式标题，避免落回提问或教学模板。
    def _build_result_record_titles(self, title: str) -> List[str]:
        episode, subject, detail = self._split_title_parts(title)
        subject = self._normalize_subject(subject)
        tail = self._clean_text(detail)
        if tail:
            titles = [
                self._join_episode_prefix(episode, f"{subject}：这次把 {tail} 整个过程都跑了一遍"),
                self._join_episode_prefix(episode, f"围着{subject}做完这一轮，过程和结果都顺手记下来了"),
            ]
        else:
            titles = [
                self._join_episode_prefix(episode, f"把{subject}完整做了一遍，这次先把真实结果记下来"),
                self._join_episode_prefix(episode, f"围着{subject}忙完这一轮，过程里的变化都留住了"),
            ]
        return [self._clean_text(item) for item in titles if self._clean_text(item)]

    # 为普通内容生成更自然的陈述式优化标题。
    def _build_general_record_titles(self, title: str) -> List[str]:
        episode, subject, detail = self._split_title_parts(title)
        subject = self._normalize_subject(subject)
        tail = self._clean_text(detail)
        if tail:
            titles = [
                self._join_episode_prefix(episode, f"{subject}：把 {tail} 这段过程完整记下来了"),
                self._join_episode_prefix(episode, f"围着{subject}忙了一整天，{tail} 这部分终于顺下来了"),
            ]
        else:
            titles = [
                self._join_episode_prefix(episode, f"把{subject}从头到尾顺了一遍，这次终于自然多了"),
                self._join_episode_prefix(episode, f"围着{subject}忙完这一轮，过程里的细节都补齐了"),
            ]
        return [self._clean_text(item) for item in titles if self._clean_text(item)]

    # 规则模式下生成符合原题材气质的替换标题。
    def _build_fallback_titles(self, title: str) -> List[str]:
        if self._is_life_record_title(title):
            return self._build_life_record_titles(title)[:2]
        if self._is_result_record_title(title):
            return self._build_result_record_titles(title)[:2]
        return self._build_general_record_titles(title)[:2]

    # 过滤掉“90 秒讲清 / 做对这一步”这类模板化标题。
    def _is_bad_optimized_title(self, title: str) -> bool:
        clean = self._clean_text(title)
        if not clean:
            return True
        if "?" in clean or "？" in clean or clean.endswith(("吗", "呢")):
            return True
        return any(
            token in clean
            for token in [
                "90 秒讲清",
                "90秒讲清",
                "最关键的 3 个点",
                "最关键的3个点",
                "做对这一步",
                "效果会完全不一样",
                "为什么",
                "如何",
                "怎么",
                "哪种",
                "教程",
                "攻略",
                "教你",
            ]
        )

    # 统一清洗优化标题；模型就算回了模板化结果，也自动退回到规则标题。
    def _normalize_optimized_titles(self, raw_titles: object, fallback_titles: List[str]) -> List[str]:
        values = raw_titles if isinstance(raw_titles, list) else []
        result: List[str] = []
        for item in values:
            clean = self._clean_text(str(item))
            if self._is_bad_optimized_title(clean) or clean in result:
                continue
            result.append(clean)
        for item in fallback_titles:
            clean = self._clean_text(item)
            if not clean or clean in result:
                continue
            result.append(clean)
            if len(result) >= 2:
                break
        return result[:2] or fallback_titles[:2]

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
        fallback_titles = self._build_fallback_titles(current.title)
        fallback = {
            "diagnosis": rules["diagnosis"],
            "optimized_titles": fallback_titles,
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
            "optimized_titles 必须贴合原视频题材和口吻；如果原题是生活区 vlog / 日常记录，就输出叙事型、陈述型标题，不要提问、不要教学口吻，不要出现“90 秒讲清 / 做对这一步 / 效果会完全不一样”这类模板。"
        )
        data = self.llm.invoke_json(system_prompt, user_prompt, fallback)
        return OptimizationSuggestion(
            bv_id=bv_id,
            diagnosis=data.get("diagnosis", fallback["diagnosis"]),
            optimized_titles=self._normalize_optimized_titles(
                data.get("optimized_titles", fallback["optimized_titles"]),
                fallback["optimized_titles"],
            ),
            cover_suggestion=data.get("cover_suggestion", fallback["cover_suggestion"]),
            content_suggestions=data.get("content_suggestions", fallback["content_suggestions"]),
            benchmark_summary=benchmark_summary,
            raw_text=str(data),
        )
