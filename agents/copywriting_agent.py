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

LIFE_SCRIPT_BANNED_TOKENS = (
    "切口",
    "测反馈",
    "反馈",
    "完播",
    "方向跑偏",
    "实战拆解",
    "实战继续出",
    "推荐机制",
    "留人",
    "结构",
    "表达",
    "运营",
    "起量",
    "账号",
    "流量",
    "停下来看",
    "结果导向",
)

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

    # 把偏策略/提问口吻的主题整理成更适合标题表达的生活化主体。
    def _title_subject(self, topic: str) -> str:
        base = self._content_subject(self._extract_subject(topic))
        if not base or base == "这类内容":
            base = self._clean_text(topic)

        patterns = [
            r"第一条(?:视频)?",
            r"第[1一二三123]+条",
            r"做成系列内容时",
            r"做系列内容时",
            r"别一上来就",
            r"先(?:做|拍|跳)什么",
            r"更容易(?:起量|进推荐|被推荐)",
            r"怎么(?:拍|做|设计)?",
            r"如何",
            r"为什么",
            r"起号",
            r"切口",
            r"开场动作",
            r"前三秒",
            r"镜头顺序",
            r"内容顺序",
            r"结构设计",
            r"教程",
            r"攻略",
        ]
        for pattern in patterns:
            base = re.sub(pattern, " ", base, flags=re.IGNORECASE)
        base = re.sub(r"(视频|内容|账号)$", "", base)
        base = self._clean_text(base)
        if base in {"这类", "这类内容"}:
            return ""
        return base

    # 判断主题是否更接近日常记录 / 生活区 vlog 的表达场景。
    def _is_life_record_topic(self, topic: str) -> bool:
        text = f"{self._clean_text(topic)} {self._title_subject(topic)}".lower()
        return any(
            token in text
            for token in [
                "异地恋",
                "报备",
                "情侣",
                "恋爱",
                "日常",
                "生活",
                "vlog",
                "记录",
                "通勤",
                "下班",
                "回家",
                "碎碎念",
            ]
        )

    # 判断主题是否更明确属于恋爱 / 约会 / 异地恋日常口播场景。
    def _is_romance_daily_topic(self, topic: str) -> bool:
        text = self._clean_text(topic).lower()
        return any(
            token in text
            for token in [
                "异地恋",
                "情侣",
                "恋爱",
                "约会",
                "见面",
                "520",
                "女友",
                "男友",
                "报备",
            ]
        )

    # 从标题里抽取生活场景关键词，方便把脚本写得更贴画面。
    def _extract_life_scenes(self, topic: str) -> List[str]:
        clean = self._clean_text(topic)
        detail = clean.split("：", 1)[1] if "：" in clean else clean.split(":", 1)[1] if ":" in clean else clean
        raw_parts = re.split(r"[+＋|｜/、，,]", detail)
        scenes: List[str] = []
        for part in raw_parts:
            value = self._clean_text(part)
            if len(value) < 2:
                continue
            scenes.append(value)

        merged: List[str] = []
        mapping = [
            ("酒店", ["酒店", "躺酒店", "回酒店"]),
            ("早午餐", ["早饭", "早餐", "早午餐", "午饭", "中饭", "自助早饭", "自助中饭"]),
            ("逛街拍照", ["逛街", "拍照", "外景拍照", "散步", "压马路"]),
            ("小清吧", ["清吧", "小清吧", "小酒馆", "酒吧"]),
            ("外卖", ["外卖", "夜宵"]),
        ]
        for normalized, keywords in mapping:
            if any(keyword in scene for scene in scenes for keyword in keywords):
                if normalized not in merged:
                    merged.append(normalized)
        for default in ["酒店", "早午餐", "逛街拍照", "小清吧"]:
            if default not in merged:
                merged.append(default)
            if len(merged) >= 5:
                break
        return merged[:5]

    # 根据恋爱日常题材生成更像短视频口播的结尾互动。
    def _build_life_record_interaction(self, topic: str, scenes: List[str]) -> str:
        if "异地恋" in topic:
            return "异地恋见面的时候，你们最舍不得结束的是哪一段？评论区告诉我，我想看看是不是大家都一样。"
        if "情侣" in topic or "约会" in topic:
            return f"如果是你，你会把这天里最想反复重来的那一段留给{scenes[-1] if scenes else '晚上'}吗？评论区聊聊。"
        return "如果是你，你会把今天最想反复过一遍的那一段留在哪个时刻？评论区告诉我。"

    # 为恋爱 / 异地恋 / 约会 vlog 生成可直接口播的 4 段脚本。
    def _build_romance_daily_script(self, topic: str) -> List[Dict[str, str]]:
        scenes = self._extract_life_scenes(topic)
        first_scene = scenes[0] if scenes else "酒店"
        second_scene = scenes[1] if len(scenes) > 1 else "早午餐"
        third_scene = scenes[2] if len(scenes) > 2 else "逛街拍照"
        last_scene = scenes[3] if len(scenes) > 3 else scenes[-1] if scenes else "小清吧"
        meeting_text = "异地恋见面" if "异地恋" in topic else "情侣见面"
        interaction = self._build_life_record_interaction(topic, scenes)

        return [
            {
                "section": "开头钩子",
                "duration": "0-8 秒",
                "content": f"{meeting_text}最戳人的，真的不是多隆重，就是一醒来发现对方就在旁边，连赖在{first_scene}里发呆都觉得很甜。",
            },
            {
                "section": "核心画面 1",
                "duration": "8-28 秒",
                "content": f"我们慢慢出门去吃{second_scene}，一边挑吃的，一边说昨晚没说完的小事，那种终于能面对面聊天的感觉，一下子就把距离感冲淡了。",
            },
            {
                "section": "核心画面 2",
                "duration": "28-56 秒",
                "content": f"后面又去{third_scene}，风有点大，人也冻得直缩脖子，但他还是会一边帮我看镜头，一边催我把手揣回口袋。到了晚上坐进{last_scene}，整个人才真的慢下来，突然就很想把这一天按暂停。",
            },
            {
                "section": "结尾互动",
                "duration": "56-75 秒",
                "content": interaction,
            },
        ]

    # 校验恋爱日常脚本是否真的像短视频口播，而不是运营话术。
    def _is_valid_romance_daily_script(self, script: List[Dict[str, str]]) -> bool:
        if len(script) < 4:
            return False
        expected_sections = ["开头钩子", "核心画面 1", "核心画面 2", "结尾互动"]
        for index, section_name in enumerate(expected_sections):
            current = self._clean_text(str((script[index] or {}).get("section", "")))
            if current != section_name:
                return False
        full_text = " ".join(self._clean_text(item.get("content", "")) for item in script)
        if any(token in full_text for token in LIFE_SCRIPT_BANNED_TOKENS):
            return False
        scene_tokens = ["酒店", "早饭", "早餐", "早午餐", "午饭", "中饭", "逛街", "拍照", "清吧", "见面"]
        emotion_tokens = ["甜", "想", "舍不得", "终于", "慢下来", "发呆", "聊天", "抱", "开心", "想念"]
        if not any(token in full_text for token in scene_tokens):
            return False
        if not any(token in full_text for token in emotion_tokens):
            return False
        return True

    # 判断标题是否仍然落回了提问式 / 教学式模板。
    def _is_bad_title(self, title: str) -> bool:
        clean = self._clean_text(title)
        if not clean:
            return True
        if "?" in clean or "？" in clean or clean.endswith(("吗", "呢")):
            return True
        if any(clean.startswith(prefix) for prefix in ["别直接", "别一上来就"]):
            return True
        if any(
            token in clean
            for token in [
                "为什么",
                "如何",
                "怎么",
                "哪种",
                "哪类",
                "该怎么",
                "先做什么",
                "先拍什么",
                "先跳什么",
                "更容易起量",
                "更容易进推荐",
                "更容易被推荐",
                "先做哪种切口",
                "教程",
                "攻略",
                "教你",
            ]
        ):
            return True
        return False

    # 统一清洗标题列表；如果模型结果仍然模板化，则自动用规则标题补齐。
    def _normalize_titles(self, raw_titles: object, fallback_titles: List[str]) -> List[str]:
        values = raw_titles if isinstance(raw_titles, list) else []
        result: List[str] = []
        for item in values:
            clean = self._clean_text(str(item))
            if self._is_bad_title(clean) or clean in result:
                continue
            result.append(clean)

        for item in fallback_titles:
            clean = self._clean_text(item)
            if not clean or clean in result:
                continue
            result.append(clean)
            if len(result) >= 3:
                break
        return result[:3] or fallback_titles

    # 生成更像生活区 vlog 的日常记录标题。
    def _build_life_record_titles(self, topic: str, subject: str) -> List[str]:
        text = f"{self._clean_text(topic)} {subject}"
        if "异地恋" in text and "报备" in text:
            return [
                "异地恋报备日常，从早安到晚安都想慢慢告诉你",
                "今天也在认真报备，吃饭下班回家路上都没落下",
                "把异地恋过成普通日常，琐碎小事也想第一时间分享",
            ]
        if "异地恋" in text:
            return [
                "异地恋的一天，从早安电话到晚安视频都记下来了",
                "隔着屏幕过日常，今天的碎碎念也想慢慢分享",
                "异地恋日常存档，吃饭下班回家路上都在认真联系",
            ]
        if "报备" in text:
            return [
                "今天也在认真报备，把一天里的小事都慢慢说完",
                "报备式日常记录，吃饭下班回家路上都想告诉你",
                "把琐碎日常发给重要的人，这一天也被认真记住了",
            ]

        base = subject if subject and subject != "这类内容" else "日常"
        if base.endswith("日常"):
            return [
                f"{base}存档，把今天从头到尾慢慢记下来",
                f"围着{base}过的一天，琐碎小事也想认真分享",
                f"今天的{base}小记录，轻轻松松把状态都留住",
            ]
        return [
            f"{base}日常记录，把今天从头到尾慢慢拍下来",
            f"围着{base}过的一天，琐碎流程也想认真分享",
            f"今天的{base}小存档，顺手把真实状态都留住",
        ]

    # 按主题模式和风格生成多组可直接使用的标题。
    def _build_titles(self, topic: str, style: str) -> List[str]:
        mode = self._topic_mode(topic)
        subject = self._extract_subject(topic)
        content_subject = self._content_subject(subject)
        title_subject = self._title_subject(topic)
        default_subjects = {
            "dance_first_video": "第一条内容",
            "opening_hook": "开场",
            "series_plan": "这个系列",
            "first_video": "第一条内容",
            "general": "这条内容",
        }
        base_subject = title_subject or default_subjects.get(mode, "这条内容")

        if self._is_life_record_topic(topic):
            life_subject = title_subject or (content_subject if content_subject and content_subject != "这类内容" else "日常")
            return self._build_life_record_titles(topic, life_subject)

        if mode == "dance_first_video":
            return [
                f"第一次认真拍{base_subject}，先从最顺手的那段开始",
                f"把{base_subject}的第一条慢慢顺下来，动作和镜头都轻一点",
                f"今天先把{base_subject}的第一条拍出来，简单一点反而更舒服",
            ]
        if mode == "opening_hook":
            return [
                f"把{base_subject}的开场重新顺了一遍，前三秒终于不空了",
                f"今天只改{base_subject}的开头，整条节奏一下子顺了很多",
                f"这次先把{base_subject}前几秒拍稳，后面看起来也自然多了",
            ]
        if mode == "series_plan":
            return [
                f"把{base_subject}当成系列慢慢发，前三条先这样记下来",
                f"今天顺了{base_subject}的前三条内容，节奏终于没那么乱了",
                f"先把{base_subject}的系列起步排清楚，后面拍起来轻松很多",
            ]
        if mode == "first_video":
            if base_subject == "第一条内容":
                return [
                    "第一次认真发第一条内容，这条就当作起步记录",
                    "新号开更的第一天，先把第一条稳稳顺下来",
                    "先把这次起步完整拍下来，轻一点的版本反而更自然",
                ]
            return [
                f"第一次认真做{base_subject}，这条就当作起步记录",
                f"新号开始拍{base_subject}，先把第一条稳稳顺下来",
                f"把{base_subject}的第一条从头到尾捋顺，这次先发轻一点的版本",
            ]
        return [
            f"这次把{base_subject}从头到尾顺了一遍，整条节奏舒服多了",
            f"围着{base_subject}忙了一天，流程和细节都慢慢补齐了",
            f"今天先把{base_subject}这件事认真做完，过程也顺手记下来了",
        ]

    # 按主题模式生成分段脚本结构。
    def _build_script(self, topic: str, style: str) -> List[Dict[str, str]]:
        mode = self._topic_mode(topic)
        subject = self._extract_subject(topic)
        account_subject = self._account_subject(subject)
        content_subject = self._content_subject(subject)
        ending = STYLE_ENDING.get(style, STYLE_ENDING["干货"])

        if self._is_romance_daily_topic(topic):
            return self._build_romance_daily_script(topic)

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
        if self._is_romance_daily_topic(topic):
            return (
                f"这条就想把“{topic}”里的见面日常慢慢记下来。"
                "酒店、早午餐、逛街拍照和小清吧都不是多特别的行程，但放在异地恋见面的那天里，每一段都会变得很舍不得。"
            )
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
        if self._is_romance_daily_topic(topic):
            tags: List[str] = []
            for item in ["异地恋", "情侣日常", "约会vlog", "见面日常", *self._extract_life_scenes(topic), *self._extract_keywords(topic)]:
                clean = self._clean_text(item)
                if len(clean) < 2 or clean in tags:
                    continue
                tags.append(clean)
            return tags[:12]
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
        if self._is_romance_daily_topic(topic):
            if "异地恋" in topic:
                return "异地恋见面的哪一个瞬间最让你破防？是刚见面、一起吃饭，还是晚上准备分开的时候？"
            return "如果是你，你会把这一天里最舍不得结束的那一段留在哪个时刻？评论区聊聊。"
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
        return self._normalize_titles(data.get("titles"), fallback.titles)

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
        if not result:
            return fallback.script
        if len(result) < 4:
            return fallback.script
        if self._is_romance_daily_topic(fallback.topic):
            if not self._is_valid_romance_daily_script(result):
                return fallback.script
        return result

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
            "其中 titles 必须是 3 个生活化、叙事感、陈述型的 B 站标题，像真实 vlog / 日常记录会使用的标题；"
            "不要提问句，不要教学口吻，不要出现“为什么 / 怎么 / 如何 / 哪种 / 先做什么 / 更容易起量 / 更容易进推荐”这类模板化表达。"
            "如果主题属于异地恋 / 情侣约会 / 520日常 vlog，script 必须写成短视频口播：有场景、有情绪、有人话感，严格按 0-8 秒开头钩子、8-28 秒核心画面1、28-56 秒核心画面2、56-75 秒结尾互动来写。"
            "禁止出现“切口、测反馈、完播、方向跑偏、实战拆解、推荐机制”等运营词。"
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
