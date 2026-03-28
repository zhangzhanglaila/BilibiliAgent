"""LangChain-backed LLM client with robust JSON extraction."""
from __future__ import annotations

import json
import re
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from config import CONFIG

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None


class LLMInvocationError(RuntimeError):
    def __init__(self, message: str, *, raw_message: str = "", category: str = "unknown", transient: bool = False) -> None:
        super().__init__(message)
        self.raw_message = raw_message or message
        self.category = category
        self.transient = transient


def _error_text(exc: Exception) -> str:
    return str(exc or "").strip()


def classify_llm_error(exc: Exception) -> str:
    if isinstance(exc, LLMInvocationError):
        return exc.category

    text = _error_text(exc).lower()
    if "billing_service_error" in text or "billing service temporarily unavailable" in text:
        return "billing_service_unavailable"
    if "503" in text and ("temporarily unavailable" in text or "service unavailable" in text):
        return "service_unavailable"
    if "502" in text or "bad gateway" in text:
        return "bad_gateway"
    if "504" in text or "gateway timeout" in text:
        return "gateway_timeout"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if "401" in text or "invalid api key" in text or "authentication" in text:
        return "auth"
    if "403" in text or ("insufficient" in text and "quota" in text) or "quota" in text:
        return "quota"
    return "unknown"


def is_retryable_llm_error(exc: Exception) -> bool:
    return classify_llm_error(exc) in {
        "billing_service_unavailable",
        "service_unavailable",
        "bad_gateway",
        "gateway_timeout",
        "timeout",
        "rate_limit",
    }


def should_skip_same_provider_fallback(exc: Exception) -> bool:
    return classify_llm_error(exc) in {
        "billing_service_unavailable",
        "service_unavailable",
        "bad_gateway",
        "gateway_timeout",
        "auth",
        "quota",
    }


def format_llm_error(exc: Exception) -> str:
    if isinstance(exc, LLMInvocationError):
        return str(exc)

    category = classify_llm_error(exc)
    if category == "billing_service_unavailable":
        return "上游 LLM 服务暂时不可用（503 / billing_service_error）。这是服务提供方的计费或网关故障，请稍后重试。"
    if category == "service_unavailable":
        return "上游 LLM 服务暂时不可用（503）。请稍后重试。"
    if category in {"bad_gateway", "gateway_timeout"}:
        return "上游 LLM 网关暂时异常（502/504）。请稍后重试。"
    if category == "timeout":
        return f"LLM 请求超时（>{CONFIG.llm_timeout_seconds}s）。请稍后重试。"
    if category == "rate_limit":
        return "上游 LLM 服务当前限流，请稍后重试。"
    if category == "auth":
        return "LLM API Key 无效或鉴权失败，请检查 .env 中的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。"
    if category == "quota":
        return "LLM 服务当前不可用，可能是额度或权限限制，请检查服务提供方账户状态。"
    return _error_text(exc) or "未知 LLM 错误"


def llm_error_http_status(exc: Exception) -> int:
    return 503 if is_retryable_llm_error(exc) else 500


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
                timeout=CONFIG.llm_timeout_seconds,
                max_retries=0,
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
        attempts = max(1, int(CONFIG.llm_max_retries) + 1)
        last_error: LLMInvocationError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return self.model.invoke(messages)
            except Exception as exc:
                category = classify_llm_error(exc)
                wrapped = LLMInvocationError(
                    format_llm_error(exc),
                    raw_message=_error_text(exc),
                    category=category,
                    transient=is_retryable_llm_error(exc),
                )
                last_error = wrapped
                if attempt >= attempts or not wrapped.transient:
                    raise wrapped
                time.sleep(CONFIG.llm_retry_backoff_seconds * attempt)

        raise last_error or LLMInvocationError("LLM 调用失败")

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
