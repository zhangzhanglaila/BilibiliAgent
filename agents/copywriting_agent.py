"""文案 Agent：根据选题生成发布文案。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from llm_client import LLMClient
from models import CopywritingResult, TopicIdea


STYLE_GUIDE = {
    "干货": "表达清晰，节奏快，强调结论先行和可执行步骤。",
    "教学": "像老师带着做一遍，步骤明确，适合新手。",
    "搞笑": "保留信息量，同时加入轻松调侃和反差包袱。",
    "混剪": "强调高能片段、节奏感和视觉冲击。",
}

STYLE_ENDING = {
    "干货": "如果你想看我继续拆同类题材，评论区留一个方向，我按实战继续出。",
    "教学": "如果你想让我把这个题材拆成拍摄清单，评论区留“继续”，我下一条直接给模板。",
    "搞笑": "如果你也踩过这种坑，评论区打个“我也这样”，我继续把后面两条内容排给你。",
    "混剪": "如果你想看我把镜头节奏和卡点顺序继续拆开，评论区留题材，我下一条直接排镜头。",
}


class CopywritingAgent:
    # 初始化文案 Agent，并准备可选的 LLM 增强能力。
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or LLMClient()

    # 规则模板和 LLM 输出都走同一套清洗逻辑，避免前端拿到两种风格完全不同的脏数据。
    def _clean_text(self, text: str) -> str:
        value = re.sub(r"\s+", " ", text or "")
        return value.strip(" ，,。.;；:：-_")

    # 把自然语言主题归类到几种固定的文案框架里。
    def _topic_mode(self, topic: str) -> str:
        cleaned = self._clean_text(topic)
        if any(token in cleaned for token in ["第1条、第2条、第3条", "做系列内容时", "做成系列内容时"]):
            return "series_plan"
        if any(token in cleaned for token in ["开场动作", "进推荐", "别一上来就", "前三秒"]):
            return "opening_hook"
        if any(token in cleaned for token in ["第一条视频跳什么", "先跳什么", "跳什么更容易起量"]):
            return "dance_first_video"
        if any(token in cleaned for token in ["第一条视频先做什么", "第一条视频先拍什么", "第一条该怎么做"]):
            return "first_video"
        return "general"

    # 从完整主题里抽取更适合作为主语的主体部分。
    def _extract_subject(self, topic: str) -> str:
        cleaned = self._clean_text(topic)
        if not cleaned:
            return "这类内容"

        direct_split = cleaned.split("：", 1)[0].split(":", 1)[0]
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
                return self._clean_text(cleaned[:index])

        if direct_split and direct_split != cleaned:
            return self._clean_text(direct_split.removeprefix("别直接硬拍"))
        return cleaned

    # 把主体整理成更像“账号定位”的表达。
    def _account_subject(self, subject: str) -> str:
        cleaned = self._clean_text(subject)
        if not cleaned or cleaned == "这类内容":
            return "这类账号"
        return cleaned if cleaned.endswith("账号") else f"{cleaned}账号"

    # 把主体整理成更像“内容主题”的表达。
    def _content_subject(self, subject: str) -> str:
        cleaned = self._clean_text(subject)
        if not cleaned:
            return "这类内容"
        return cleaned[:-2] if cleaned.endswith("账号") else cleaned

    # 从主题文本里提取标签和标题可复用的关键词。
    def _extract_keywords(self, text: str) -> List[str]:
        words = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", self._clean_text(text))
        keywords: List[str] = []
        for word in words:
            if word in keywords:
                continue
            keywords.append(word)
        return keywords[:6]

    # 按主题模式和风格生成多组可直接使用的标题。
    def _build_titles(self, topic: str, style: str) -> List[str]:
        mode = self._topic_mode(topic)
        subject = self._extract_subject(topic)
        account_subject = self._account_subject(subject)
        content_subject = self._content_subject(subject)

        if mode == "dance_first_video":
            return [
                f"想做{account_subject}，第一条先跳什么才更容易进推荐",
                f"别一上来就上难度，{account_subject}第一条先跳这几类动作",
                f"同样是{content_subject}，为什么这种开场更容易被看完",
            ]
        if mode == "opening_hook":
            return [
                f"{account_subject}别一上来就硬跳，这种开场动作更容易进推荐",
                f"3 秒先留人再发力：{content_subject}开场该怎么设计",
                f"做{content_subject}最容易掉播放的，不是动作难度，是开场顺序",
            ]
        if mode == "series_plan":
            return [
                f"{account_subject}做系列别乱发，第1条到第3条这样排更容易起量",
                f"想把{content_subject}做成系列，先把前三条内容顺序定好",
                f"{content_subject}连续发三条时，每一条分别承担什么作用",
            ]
        if mode == "first_video":
            return [
                f"新号做{content_subject}，第一条先拍什么更容易拿到推荐",
                f"别直接上最难的，{content_subject}第一条先做这个切口",
                f"想把{content_subject}做起来，第一条视频先解决这件事",
            ]
        if style == "教学":
            return [
                f"{content_subject}新手第一条该怎么拍，顺序我给你排好了",
                f"想做{content_subject}，先按这个结构拍，试错成本最低",
                f"{content_subject}别乱开题，先从最容易验证的一条开始",
            ]
        if style == "搞笑":
            return [
                f"{content_subject}别上来就自嗨，这种拍法更容易被看完",
                f"同样是做{content_subject}，为什么有人一发就有量",
                f"{content_subject}第一条别硬冲，这种切口更容易出效果",
            ]
        if style == "混剪":
            return [
                f"{content_subject}第一条怎么剪，前三秒高能位要这样放",
                f"想做{content_subject}混剪，先把镜头顺序排对",
                f"{content_subject}为什么总留不住人，问题通常出在第一屏",
            ]
        return [
            f"{content_subject}先做哪种切口，更容易被推荐",
            f"同样是{content_subject}，这类表达为什么更容易起量",
            f"{content_subject}想做成系列，先从这一条开始",
        ]

    # 按主题模式生成分段脚本结构。
    def _build_script(self, topic: str, style: str) -> List[Dict[str, str]]:
        mode = self._topic_mode(topic)
        subject = self._extract_subject(topic)
        account_subject = self._account_subject(subject)
        content_subject = self._content_subject(subject)
        ending = STYLE_ENDING.get(style, STYLE_ENDING["干货"])

        if mode == "dance_first_video":
            return [
                {
                    "section": "开头钩子",
                    "duration": "0-8 秒",
                    "content": f"如果你正准备做{account_subject}，第一条别急着上完整编舞。先选 3 秒内能看懂、动作识别度高、镜头能立住的内容，更容易拿到第一波推荐。",
                },
                {
                    "section": "动作选择",
                    "duration": "8-28 秒",
                    "content": "优先拍节奏明确、上手不难、能带表情管理的动作。太难的编排会拖慢更新，也不利于你快速测出观众偏好。",
                },
                {
                    "section": "镜头节奏",
                    "duration": "28-58 秒",
                    "content": "开头先给最抓眼的定格或转身，中段补一个近景表情点，结尾留一句互动提问，比如“下一条想看我跳哪种风格”。",
                },
                {
                    "section": "结尾引导",
                    "duration": "58-75 秒",
                    "content": ending,
                },
            ]
        if mode == "opening_hook":
            return [
                {
                    "section": "开头钩子",
                    "duration": "0-8 秒",
                    "content": f"做{content_subject}最容易犯的错，就是上来直接全身远景开干。前三秒先留人，再展示完整动作，推荐系统才更容易把你送出去。",
                },
                {
                    "section": "前三秒设计",
                    "duration": "8-26 秒",
                    "content": "先给半身近景、明显节奏点或一句字幕反差，把观众先拽住，再接主动作。前三秒的任务不是展示全部，而是制造停留。",
                },
                {
                    "section": "中段推进",
                    "duration": "26-56 秒",
                    "content": "中段把最稳的动作和表情管理放在一起，结尾补一个评论区问题或下一条预告，让这一条既能看完，也能带出后续内容。",
                },
                {
                    "section": "结尾引导",
                    "duration": "56-75 秒",
                    "content": ending,
                },
            ]
        if mode == "series_plan":
            return [
                {
                    "section": "开头钩子",
                    "duration": "0-8 秒",
                    "content": f"想把{content_subject}做成系列，别第一条就把所有东西全塞进去。前三条要分工，不然账号很难建立稳定记忆点。",
                },
                {
                    "section": "第 1 条作用",
                    "duration": "8-26 秒",
                    "content": "第一条负责让观众记住你的人设和最强记忆点，内容要简单、明确、好理解，不要先堆复杂信息。",
                },
                {
                    "section": "第 2 条和第 3 条",
                    "duration": "26-58 秒",
                    "content": "第二条放大第一条里反馈最好的动作或表达，第三条再补变化和互动。这样你能更快判断哪种内容值得继续放大。",
                },
                {
                    "section": "结尾引导",
                    "duration": "58-78 秒",
                    "content": ending,
                },
            ]
        if mode == "first_video":
            return [
                {
                    "section": "开头钩子",
                    "duration": "0-8 秒",
                    "content": f"新号做{content_subject}，第一条别想着面面俱到。先解决一个最具体、最容易让人停下来的问题，比堆信息更重要。",
                },
                {
                    "section": "切口选择",
                    "duration": "8-30 秒",
                    "content": "优先选一个用户一看就懂的切口，比如结果对比、常见误区、第一步怎么做。切口越具体，推荐和点击越容易稳定。",
                },
                {
                    "section": "内容结构",
                    "duration": "30-60 秒",
                    "content": "开头给结果，中段拆原因，结尾留下一条后续延展方向。第一条的目标不是讲完，而是让观众愿意继续看你下一条。",
                },
                {
                    "section": "结尾引导",
                    "duration": "60-78 秒",
                    "content": ending,
                },
            ]
        return [
            {
                "section": "开头钩子",
                "duration": "0-8 秒",
                "content": f"这条就先把结论放前面：做{content_subject}，先别贪多，先拿一个最容易验证的切口去测反馈。",
            },
            {
                "section": "核心观点 1",
                "duration": "8-28 秒",
                "content": "先讲观众为什么会停下来看，再讲你具体要给什么结果。只要这两件事对上，内容方向就不会跑偏。",
            },
            {
                "section": "核心观点 2",
                "duration": "28-56 秒",
                "content": "把最容易出效果的表达放在前半段，把补充说明放在后半段。顺序排对，完播和互动通常都会比平铺直叙更好。",
            },
            {
                "section": "结尾引导",
                "duration": "56-75 秒",
                "content": ending,
            },
        ]

    # 生成适合发布页使用的简介文本。
    def _build_description(self, topic: str, style: str) -> str:
        mode = self._topic_mode(topic)
        subject = self._content_subject(self._extract_subject(topic))
        summaries = {
            "dance_first_video": "重点拆第一条起号该拍什么、动作怎么选、镜头顺序怎么排。",
            "opening_hook": "重点拆前三秒怎么留人、开场动作怎么设计、节奏怎么推进。",
            "series_plan": "重点拆前三条内容怎么分工，避免系列内容一上来就散。",
            "first_video": "重点拆第一条视频的切口、结构和后续承接方式。",
            "general": "重点拆选题切口、表达结构和互动设计。",
        }
        return (
            f"本条围绕“{topic}”展开，适合正在做 {subject} 的创作者参考。"
            f"{summaries.get(mode, summaries['general'])} 文案风格为「{style}」，可直接按段落改成自己的版本。"
        )

    # 生成一组适合发布时使用的标签。
    def _build_tags(self, topic: str, style: str) -> List[str]:
        mode = self._topic_mode(topic)
        subject = self._content_subject(self._extract_subject(topic))
        mode_tags = {
            "dance_first_video": ["舞蹈账号", "第一条视频", "起号", "开场动作", "镜头节奏"],
            "opening_hook": ["前三秒", "开场设计", "留人", "推荐机制", "镜头节奏"],
            "series_plan": ["系列内容", "账号规划", "内容节奏", "起号", "更新策略"],
            "first_video": ["第一条视频", "起号", "内容切口", "新号运营", "结构设计"],
            "general": ["内容策划", "选题", "视频脚本", "创作灵感", "账号运营"],
        }
        tags: List[str] = []
        for item in [subject, *self._extract_keywords(topic), *mode_tags.get(mode, mode_tags["general"]), "B站创作", style]:
            clean = self._clean_text(item)
            if len(clean) < 2 or clean in tags:
                continue
            tags.append(clean)
        return tags[:12]

    # 生成用于提升互动率的置顶评论。
    def _build_pinned_comment(self, topic: str) -> str:
        mode = self._topic_mode(topic)
        subject = self._content_subject(self._extract_subject(topic))
        if mode == "dance_first_video":
            return f"你觉得做 {subject}，第一条应该先试卡点、轻剧情还是简单动作？评论区留一个，我继续往下拆。"
        if mode == "opening_hook":
            return f"你现在最卡的是开场镜头、动作选择，还是结尾互动？评论区留一个点，我下一条继续补。"
        if mode == "series_plan":
            return "你现在最卡的是第 1 条、第 2 条还是第 3 条？评论区留数字，我按这个顺序继续拆。"
        return f"如果你也在做 {subject}，评论区告诉我你最想先优化哪一步，我继续按这个方向出下一条。"

    # 在不依赖模型的情况下构造一份完整的文案兜底结果。
    def _fallback(self, topic: str, style: str) -> CopywritingResult:
        return CopywritingResult(
            topic=topic,
            style=style,
            titles=self._build_titles(topic, style),
            script=self._build_script(topic, style),
            description=self._build_description(topic, style),
            tags=self._build_tags(topic, style),
            pinned_comment=self._build_pinned_comment(topic),
            raw_text="fallback",
        )

    # 从模型结果里挑出可用标题，失败时回退到本地兜底标题。
    def _pick_titles(self, data: Dict[str, Any], fallback: CopywritingResult) -> List[str]:
        raw = data.get("titles")
        if not isinstance(raw, list):
            return fallback.titles
        values = [self._clean_text(str(item)) for item in raw if self._clean_text(str(item))]
        return values[:3] or fallback.titles

    # 从模型结果里挑出可用脚本段落，失败时回退到本地兜底脚本。
    def _pick_script(self, data: Dict[str, Any], fallback: CopywritingResult) -> List[Dict[str, str]]:
        raw = data.get("script")
        if not isinstance(raw, list):
            return fallback.script

        result: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            section = self._clean_text(str(item.get("section", "")))
            duration = self._clean_text(str(item.get("duration", "")))
            content = self._clean_text(str(item.get("content", "")))
            if not content:
                continue
            result.append(
                {
                    "section": section or "片段",
                    "duration": duration or "",
                    "content": content,
                }
            )
        return result or fallback.script

    # 从模型结果里挑出可用标签，失败时回退到本地兜底标签。
    def _pick_tags(self, data: Dict[str, Any], fallback: CopywritingResult) -> List[str]:
        raw = data.get("tags")
        if not isinstance(raw, list):
            return fallback.tags

        result: List[str] = []
        for item in raw:
            clean = self._clean_text(str(item))
            if len(clean) < 2 or clean in result:
                continue
            result.append(clean)
        return result[:15] or fallback.tags

    # 执行完整文案生成流程，先准备规则兜底，再按需用 LLM 增强。
    def run(self, topic: str | None = None, topic_idea: TopicIdea | None = None, style: str = "干货") -> CopywritingResult:
        final_topic = self._clean_text(topic or (topic_idea.topic if topic_idea else "B站高效运营"))
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
            "请基于创作者场景生成自然、可直接口播和发布的 JSON，避免复读输入词，避免机械使用“高效做法”等空泛表述。"
            "字段为 titles(3个标题), script(数组，每项包含section/duration/content), "
            "description, tags(10-15个), pinned_comment。"
        )
        data = self.llm.invoke_json(system_prompt, user_prompt, fallback_json)
        if not isinstance(data, dict):
            data = fallback_json

        description = self._clean_text(str(data.get("description", ""))) or fallback.description
        pinned_comment = self._clean_text(str(data.get("pinned_comment", ""))) or fallback.pinned_comment

        return CopywritingResult(
            topic=final_topic,
            style=style,
            titles=self._pick_titles(data, fallback),
            script=self._pick_script(data, fallback),
            description=description,
            tags=self._pick_tags(data, fallback),
            pinned_comment=pinned_comment,
            raw_text=str(data),
        )
