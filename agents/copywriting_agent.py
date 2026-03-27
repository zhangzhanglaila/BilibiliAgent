"""文案 Agent：根据选题生成发布文案。"""
from __future__ import annotations

from typing import Any, Dict, List

from llm_client import LLMClient
from models import CopywritingResult, TopicIdea


STYLE_GUIDE = {
    "干货": "表达清晰，节奏快，强调结论先行和可执行步骤。",
    "教学": "像老师带着做一遍，步骤明确，适合新手。",
    "搞笑": "保留信息量，同时加入轻松调侃和反差包袱。",
    "混剪": "强调高能片段、节奏感和视觉冲击。",
}


class CopywritingAgent:
    def __init__(self) -> None:
        self.llm = LLMClient()

    def _fallback(self, topic: str, style: str) -> CopywritingResult:
        titles = [
            f"{topic}，普通人也能立刻上手的 3 个方法",
            f"别再瞎做了，{topic} 的正确打开方式",
            f"为什么别人做 {topic} 更容易爆？答案在这里",
        ]
        script = [
            {"section": "开头钩子", "duration": "0-8 秒", "content": f"你是不是也在做 {topic}，但效果一直一般？今天直接给你最省时间的做法。"},
            {"section": "核心内容 1", "duration": "8-35 秒", "content": "先讲最容易出结果的关键动作，再解释为什么它有效。"},
            {"section": "核心内容 2", "duration": "35-70 秒", "content": "补充一个常见误区和一个可直接照抄的示例。"},
            {"section": "结尾引导", "duration": "70-85 秒", "content": "如果你想看我继续拆解同类选题，记得点赞、收藏、关注。"},
        ]
        description = (
            f"本期视频围绕【{topic}】展开，适合想提升 B 站内容效果的创作者。"
            "内容包含核心方法、常见误区和可直接执行的建议，欢迎三连支持。"
        )
        tags = [topic, "B站运营", "自媒体", "内容创作", "视频脚本", "涨粉", "爆款标题", "账号运营", "创作技巧", style]
        pinned_comment = "这期你最想抄走哪个方法？评论区告诉我，我继续做下一期。"
        return CopywritingResult(
            topic=topic,
            style=style,
            titles=titles,
            script=script,
            description=description,
            tags=tags,
            pinned_comment=pinned_comment,
            raw_text="fallback",
        )

    def run(self, topic: str | None = None, topic_idea: TopicIdea | None = None, style: str = "干货") -> CopywritingResult:
        final_topic = topic or (topic_idea.topic if topic_idea else "B站高效运营")
        fallback = self._fallback(final_topic, style)
        fallback_json = {
            "titles": fallback.titles,
            "script": fallback.script,
            "description": fallback.description,
            "tags": fallback.tags,
            "pinned_comment": fallback.pinned_comment,
        }
        system_prompt = "你是 B 站百万粉 UP 主的文案总监，输出能直接发布的视频文案。"
        user_prompt = (
            f"主题：{final_topic}\n"
            f"风格：{style}\n"
            f"风格要求：{STYLE_GUIDE.get(style, STYLE_GUIDE['干货'])}\n"
            "请生成 JSON，字段为 titles(3个标题), script(数组，每项包含section/duration/content), "
            "description, tags(10-15个), pinned_comment。"
        )
        data = self.llm.invoke_json(system_prompt, user_prompt, fallback_json)
        return CopywritingResult(
            topic=final_topic,
            style=style,
            titles=data.get("titles", fallback.titles)[:3],
            script=data.get("script", fallback.script),
            description=data.get("description", fallback.description),
            tags=data.get("tags", fallback.tags)[:15],
            pinned_comment=data.get("pinned_comment", fallback.pinned_comment),
            raw_text=str(data),
        )
