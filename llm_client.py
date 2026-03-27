"""LangChain 大模型封装，支持无 key 降级模式。"""
from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.prompts import ChatPromptTemplate

from config import CONFIG

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None


class LLMClient:
    def __init__(self) -> None:
        self.enabled = bool(CONFIG.llm_api_key and ChatOpenAI)
        self.model = None
        if self.enabled:
            self.model = ChatOpenAI(
                model=CONFIG.llm_model,
                api_key=CONFIG.llm_api_key,
                base_url=CONFIG.llm_base_url,
                temperature=0.7,
                timeout=30,
            )

    def available(self) -> bool:
        return self.enabled and self.model is not None

    def invoke_json(self, system_prompt: str, user_prompt: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available():
            return fallback
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_prompt),
                    ("user", user_prompt + "\n\n请只返回 JSON，不要输出额外解释。"),
                ]
            )
            chain = prompt | self.model
            result = chain.invoke({})
            text = getattr(result, "content", str(result))
            return json.loads(text)
        except Exception:
            return fallback

    def invoke_text(self, system_prompt: str, user_prompt: str, fallback: str) -> str:
        if not self.available():
            return fallback
        try:
            prompt = ChatPromptTemplate.from_messages(
                [("system", system_prompt), ("user", user_prompt)]
            )
            chain = prompt | self.model
            result = chain.invoke({})
            return getattr(result, "content", str(result))
        except Exception:
            return fallback
