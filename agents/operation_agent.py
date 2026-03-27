"""运营 Agent：处理评论互动，默认 dry-run 安全模式。"""
from __future__ import annotations

import time
from typing import Dict, Iterable, List

from bilibili_api import comment, sync, video

from config import CONFIG
from llm_client import LLMClient
from models import InteractionAction, OperationResult


DEFAULT_REPLY_TEMPLATES = {
    "thanks": "感谢支持，后面我会继续更新更实用的内容。",
    "qa": "这个问题问得很好，我下一期会补充更详细的实操案例。",
    "engage": "你这个观点很有代表性，评论区也可以继续展开聊聊。",
}


class OperationAgent:
    def __init__(self, request_interval: float | None = None) -> None:
        self.request_interval = request_interval or CONFIG.request_interval
        self.llm = LLMClient()
        self.spam_keywords = ["加V", "兼职", "返利", "私聊", "vx", "微信", "傻", "废物", "引战"]

    def _sleep(self) -> None:
        time.sleep(self.request_interval)

    def _safe_sync(self, coro, default):
        try:
            return sync(coro)
        except Exception:
            return default

    def is_spam(self, text: str, custom_keywords: Iterable[str] | None = None) -> bool:
        words = self.spam_keywords + list(custom_keywords or [])
        lower = text.lower()
        return any(word.lower() in lower for word in words)

    def generate_reply(self, text: str, style: str = "友好", template_map: Dict[str, str] | None = None) -> str:
        templates = {**DEFAULT_REPLY_TEMPLATES, **(template_map or {})}
        if any(key in text for key in ["谢谢", "支持", "喜欢"]):
            fallback = templates["thanks"]
        elif "?" in text or "怎么" in text or "为什么" in text:
            fallback = templates["qa"]
        else:
            fallback = templates["engage"]
        system_prompt = "你是 B 站 UP 主助手，回复要自然、真诚、简洁。"
        user_prompt = f"评论内容：{text}\n回复风格：{style}\n请生成一条 30 字以内的中文回复。"
        return self.llm.invoke_text(system_prompt, user_prompt, fallback)

    def fetch_comments(self, bv_id: str) -> List[Dict]:
        try:
            target = video.Video(bvid=bv_id)
            info = self._safe_sync(target.get_info(), {})
            aid = info.get("aid")
            if not aid:
                return []
            replies = self._safe_sync(comment.get_comments(oid=aid, type_=comment.CommentResourceType.VIDEO), {})
            self._sleep()
            return replies.get("replies", []) if isinstance(replies, dict) else []
        except Exception:
            return []

    def process_video_interactions(
        self,
        bv_id: str,
        dry_run: bool = True,
        custom_keywords: Iterable[str] | None = None,
        template_map: Dict[str, str] | None = None,
    ) -> OperationResult:
        replies: List[InteractionAction] = []
        deletions: List[InteractionAction] = []
        likes: List[InteractionAction] = []
        follows: List[InteractionAction] = []

        comments = self.fetch_comments(bv_id)
        if not comments:
            comments = [
                {"rpid": "demo-1", "content": {"message": "这个方法挺有用，谢谢"}, "member": {"uname": "粉丝A", "mid": 101}},
                {"rpid": "demo-2", "content": {"message": "怎么做到开头留人？"}, "member": {"uname": "粉丝B", "mid": 102}},
                {"rpid": "demo-3", "content": {"message": "加V领资料 vx123"}, "member": {"uname": "广告号", "mid": 103}},
            ]

        for item in comments:
            message = item.get("content", {}).get("message", "")
            uname = item.get("member", {}).get("uname", "匿名用户")
            mid = item.get("member", {}).get("mid", 0)
            rpid = str(item.get("rpid", "unknown"))
            if self.is_spam(message, custom_keywords):
                deletions.append(
                    InteractionAction(
                        action="delete_comment",
                        target=rpid,
                        message=f"识别为垃圾评论，建议删除：{message}",
                        dry_run=dry_run,
                    )
                )
                continue
            reply_text = self.generate_reply(message, template_map=template_map)
            replies.append(
                InteractionAction(
                    action="reply_comment",
                    target=rpid,
                    message=f"回复 @{uname}: {reply_text}",
                    dry_run=dry_run,
                )
            )
            likes.append(
                InteractionAction(
                    action="like_comment",
                    target=rpid,
                    message=f"为评论点赞：{message[:20]}",
                    dry_run=dry_run,
                )
            )
            if len(message) >= 12 or any(key in message for key in ["有用", "收藏", "三连"]):
                follows.append(
                    InteractionAction(
                        action="follow_user",
                        target=str(mid),
                        message=f"建议关注优质用户 @{uname}",
                        dry_run=dry_run,
                    )
                )

        summary = (
            f"共处理 {len(comments)} 条互动，建议回复 {len(replies)} 条，"
            f"删除 {len(deletions)} 条，点赞 {len(likes)} 条，关注 {len(follows)} 人。"
        )
        return OperationResult(
            bv_id=bv_id,
            replies=replies,
            deletions=deletions,
            likes=likes,
            follows=follows,
            summary=summary,
        )

    def monitor_loop(self, bv_id: str, interval_seconds: int = 60, rounds: int = 3, dry_run: bool = True) -> List[OperationResult]:
        results = []
        for _ in range(rounds):
            results.append(self.process_video_interactions(bv_id=bv_id, dry_run=dry_run))
            time.sleep(interval_seconds)
        return results
