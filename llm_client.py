"""LangChain-backed LLM client with robust JSON extraction."""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

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

    def require_available(self) -> None:
        if not self.available():
            raise RuntimeError("LLM unavailable: configure LLM_API_KEY and install LangChain/OpenAI dependencies first.")

    def _coerce_result_text(self, result: Any) -> str:
        content = getattr(result, "content", result)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", "") or getattr(item, "content", "")
                    if text:
                        parts.append(str(text))
                    else:
                        parts.append(str(item))
            return "\n".join(part for part in parts if part).strip()
        return str(content)

    def _extract_json_text(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("LLM returned empty content")

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)

        decoder = json.JSONDecoder()
        candidate_positions = [0]
        candidate_positions.extend(match.start() for match in re.finditer(r"[\{\[]", raw))

        checked: set[int] = set()
        for start in candidate_positions:
            if start in checked:
                continue
            checked.add(start)
            snippet = raw[start:].lstrip()
            if not snippet:
                continue
            try:
                _, end = decoder.raw_decode(snippet)
                return snippet[:end]
            except Exception:
                continue

        raise ValueError("LLM response does not contain valid JSON")

    def _invoke_messages(self, system_prompt: str, user_prompt: str):
        self.require_available()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        return self.model.invoke(messages)

    def invoke_json(self, system_prompt: str, user_prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.available():
            return fallback
        try:
            result = self._invoke_messages(
                system_prompt,
                user_prompt + "\n\nReturn JSON only. Do not add explanation outside the JSON payload.",
            )
            text = self._coerce_result_text(result)
            data = json.loads(self._extract_json_text(text))
            return data if isinstance(data, dict) else fallback
        except Exception:
            return fallback

    def invoke_text(self, system_prompt: str, user_prompt: str, fallback: str) -> str:
        if not self.available():
            return fallback
        try:
            result = self._invoke_messages(system_prompt, user_prompt)
            return self._coerce_result_text(result)
        except Exception:
            return fallback

    def invoke_json_required(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.require_available()
        result = self._invoke_messages(
            system_prompt,
            user_prompt + "\n\nReturn JSON only. Do not add explanation outside the JSON payload.",
        )
        text = self._coerce_result_text(result)
        data = json.loads(self._extract_json_text(text))
        if not isinstance(data, dict):
            raise ValueError("LLM must return a JSON object")
        return data

    def invoke_text_required(self, system_prompt: str, user_prompt: str) -> str:
        self.require_available()
        result = self._invoke_messages(system_prompt, user_prompt)
        return self._coerce_result_text(result)
