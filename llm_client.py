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
    # 封装模型调用异常，并附带分类和是否可重试等元信息。
    def __init__(self, message: str, *, raw_message: str = "", category: str = "unknown", transient: bool = False) -> None:
        super().__init__(message)
        self.raw_message = raw_message or message
        self.category = category
        self.transient = transient


# 提取异常里的原始文本，供统一错误分类使用。
def _error_text(exc: Exception) -> str:
    return str(exc or "").strip()


# 把底层异常归类成项目内部统一使用的错误类型。
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


# 判断当前错误是否适合做自动重试。
def is_retryable_llm_error(exc: Exception) -> bool:
    return classify_llm_error(exc) in {
        "billing_service_unavailable",
        "service_unavailable",
        "bad_gateway",
        "gateway_timeout",
        "timeout",
        "rate_limit",
    }


# 判断当前错误是否应该直接跳过同 provider 的再次尝试。
def should_skip_same_provider_fallback(exc: Exception) -> bool:
    return classify_llm_error(exc) in {
        "billing_service_unavailable",
        "service_unavailable",
        "bad_gateway",
        "gateway_timeout",
        "auth",
        "quota",
    }


# 把模型调用异常格式化成适合直接返回给前端的错误文案。
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
        return "LLM API Key 无效或鉴权失败，请检查当前填写的 API Key / Base URL / Model 配置。"
    if category == "quota":
        return "LLM 服务当前不可用，可能是额度或权限限制，请检查服务提供方账户状态。"
    return _error_text(exc) or "未知 LLM 错误"


# 根据错误类别映射出更合适的 HTTP 状态码。
def llm_error_http_status(exc: Exception) -> int:
    return 503 if is_retryable_llm_error(exc) else 500


class LLMClient:
    # 初始化统一的 LLM 客户端封装，负责连接配置和容错策略。
    def __init__(
        self,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        self.provider = (provider or CONFIG.llm_provider or "openai").strip() or "openai"
        self.api_key = (api_key if api_key is not None else CONFIG.llm_api_key).strip()
        self.base_url = (base_url if base_url is not None else CONFIG.llm_base_url).strip()
        self.model_name = (model if model is not None else CONFIG.llm_model).strip()
        self.timeout_seconds = int(timeout_seconds if timeout_seconds is not None else CONFIG.llm_timeout_seconds)
        self.max_retry_count = int(max_retries if max_retries is not None else CONFIG.llm_max_retries)
        self.retry_backoff_seconds = float(
            retry_backoff_seconds if retry_backoff_seconds is not None else CONFIG.llm_retry_backoff_seconds
        )
        self.enabled = bool(self.api_key and ChatOpenAI)
        self.model = None
        if self.enabled:
            self.model = ChatOpenAI(
                model=self.model_name,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=0.7,
                timeout=self.timeout_seconds,
                max_retries=0,
            )

    # 判断当前客户端是否具备可用的模型实例。
    def available(self) -> bool:
        return self.enabled and self.model is not None

    # 在真正调用模型前强制检查可用性，缺配置时直接报清晰错误。
    def require_available(self) -> None:
        if not self.available():
            raise RuntimeError("LLM 不可用：请检查当前运行模式里的 API Key 配置，以及 LangChain/OpenAI 依赖是否已安装。")

    # 把不同 SDK 形态的返回内容统一抽成纯文本。
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

    # 从模型返回文本里尽量提取出一段可解析的 JSON。
    def _extract_json_text(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("LLM returned empty content")

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\s*```$", "", raw)

        decoder = json.JSONDecoder()
        # 模型经常会在 JSON 前后多说几句，或者包一层代码块，这里会从所有可能的
        # 起点里挑出第一段真正能解析成功的 JSON。
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

    # 发送一次消息请求，并在这里统一处理重试和错误包装。
    def _invoke_messages(self, system_prompt: str, user_prompt: str):
        self.require_available()
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        attempts = max(1, self.max_retry_count + 1)
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
                # 重试放在这里统一处理，这样所有上层调用拿到的错误分类和退避策略都一致。
                time.sleep(self.retry_backoff_seconds * attempt)

        raise last_error or LLMInvocationError("LLM 调用失败")

    # 调用模型获取 JSON，失败时返回调用方给定的兜底结果。
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

    # 调用模型获取纯文本，失败时退回兜底文本。
    def invoke_text(self, system_prompt: str, user_prompt: str, fallback: str) -> str:
        if not self.available():
            return fallback
        try:
            result = self._invoke_messages(system_prompt, user_prompt)
            return self._coerce_result_text(result)
        except Exception:
            return fallback

    # 调用模型并强制要求返回合法 JSON，对严格 Agent 场景使用。
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

    # 调用模型并强制要求返回文本结果，失败时直接抛异常。
    def invoke_text_required(self, system_prompt: str, user_prompt: str) -> str:
        self.require_available()
        result = self._invoke_messages(system_prompt, user_prompt)
        return self._coerce_result_text(result)
