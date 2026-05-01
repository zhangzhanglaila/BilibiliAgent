"""LangChain-backed LLM client with robust JSON extraction."""
from __future__ import annotations

import json
import re
import subprocess
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4 as _uuid4

import requests
from langchain_core.messages import HumanMessage, SystemMessage

from config import CONFIG
from observability import end_trace, trace_block

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
    if "[fail_fast:" in text:
        m = re.search(r'\[fail_fast:(\d+)\]', text)
        if m:
            code = m.group(1)
            if code == "500":
                return "server_error"
            if code == "503":
                return "service_unavailable"
            if code == "529":
                return "service_overloaded"
    if "billing_service_error" in text or "billing service temporarily unavailable" in text:
        return "billing_service_unavailable"
    if "winerror 10013" in text or "unable to connect to the remote server" in text:
        return "connection_blocked"
    if "503" in text and ("temporarily unavailable" in text or "service unavailable" in text):
        return "service_unavailable"
    if "overloaded_error" in text or "529" in text:
        return "service_overloaded"
    if "500" in text or "server_error" in text or "internal server error" in text:
        return "server_error"
    if "502" in text or "bad gateway" in text or "520" in text:
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
        "connection_blocked",
        "gateway_timeout",
        "timeout",
        "rate_limit",
    }


# 判断当前错误是否应该直接跳过同 provider 的再次尝试。
def should_skip_same_provider_fallback(exc: Exception) -> bool:
    return classify_llm_error(exc) in {
        "billing_service_unavailable",
        "service_unavailable",
        "server_error",
        "bad_gateway",
        "gateway_timeout",
        "auth",
        "quota",
        "service_overloaded",
    }


# 把模型调用异常格式化成适合直接返回给前端的错误文案。
def format_llm_error(exc: Exception) -> str:
    if isinstance(exc, LLMInvocationError):
        return str(exc)

    category = classify_llm_error(exc)
    if category == "billing_service_unavailable":
        return "上游 LLM 服务暂时不可用（503 / billing_service_error）。这是服务提供方的计费或网关故障，请稍后重试。"
    if category == "connection_blocked":
        return "当前运行进程的网络连接被系统或环境拦截，LLM 请求没能真正发出去。请检查本机代理、防火墙，或 Python 进程的出网权限。"
    if category == "service_unavailable":
        return "上游 LLM 服务暂时不可用（503）。请稍后重试。"
    if category == "server_error":
        return "上游 LLM 服务内部错误（500）。请稍后重试或切换模型。"
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
        reasoning_effort: str | None = None,
        disable_response_storage: bool | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
        retry_backoff_seconds: float | None = None,
    ) -> None:
        self.provider = (provider or CONFIG.llm_provider or "openai").strip() or "openai"
        self.api_key = (api_key if api_key is not None else CONFIG.llm_api_key).strip()
        self.base_url = (base_url if base_url is not None else CONFIG.llm_base_url).strip()
        self.model_name = (model if model is not None else CONFIG.llm_model).strip()
        self.reasoning_effort = (
            str(reasoning_effort if reasoning_effort is not None else CONFIG.llm_reasoning_effort).strip().lower()
        )
        self.disable_response_storage = bool(
            CONFIG.llm_disable_response_storage if disable_response_storage is None else disable_response_storage
        )
        self.timeout_seconds = int(timeout_seconds if timeout_seconds is not None else CONFIG.llm_timeout_seconds)
        self.max_retry_count = int(max_retries if max_retries is not None else CONFIG.llm_max_retries)
        self.retry_backoff_seconds = float(
            retry_backoff_seconds if retry_backoff_seconds is not None else CONFIG.llm_retry_backoff_seconds
        )
        # 统一走 urllib HTTP 直连，不经过 ChatOpenAI（避免 httpx/requests 被 Windows 系统代理拦截）
        self.enabled = bool(self.api_key and self.base_url)
        self.model = None

    # 判断当前客户端是否具备可用的模型实例。
    def available(self) -> bool:
        return self.enabled

    # 在真正调用模型前强制检查可用性，缺配置时直接报清晰错误。
    def require_available(self) -> None:
        if not self.available():
            raise RuntimeError("LLM 不可用：请检查当前运行模式里的 API Key 配置，以及 LangChain/OpenAI 依赖是否已安装。")

    # 判断当前是否可以走 OpenAI 兼容的直接 HTTP 调用兜底。
    def _http_fallback_available(self) -> bool:
        return bool(self.api_key and (self.base_url or self.provider == "openai"))

    # 直接调用 OpenAI 兼容接口，绕过部分 LangChain 兼容性问题。
    def _invoke_via_http(self, system_prompt: str, user_prompt: str):
        if not self._http_fallback_available():
            raise RuntimeError("LLM HTTP fallback unavailable")

        endpoint = f"{(self.base_url or 'https://api.openai.com/v1').rstrip('/')}/chat/completions"
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.disable_response_storage:
            payload["store"] = False

        request_id = _uuid4().hex[:8]
        print(f"[LLM][{request_id}] start model={self.model_name} endpoint={endpoint}")
        t0 = time.time()
        with trace_block(
            "llm_client.http_fallback",
            run_type="llm",
            inputs={
                "provider": self.provider,
                "model": self.model_name,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            metadata={
                "base_url": self.base_url,
                "reasoning_effort": self.reasoning_effort,
                "disable_response_storage": self.disable_response_storage,
                "request_id": request_id,
            },
            tags=["llm", "http_fallback"],
        ) as run:
            try:
                import urllib.request as _ur
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = _ur.Request(
                    endpoint,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    method="POST",
                )
                with _ur.urlopen(req, timeout=self.timeout_seconds) as resp:
                    status = resp.status
                    raw = resp.read().decode("utf-8")
            except Exception as exc:
                message = str(exc)
                print(f"[LLM][{request_id}] FAIL elapsed={time.time() - t0:.2f}s error={message[:120]}")
                if "WinError 10013" in message:
                    return self._invoke_via_powershell_http(endpoint, payload)
                raise RuntimeError(message) from exc

            if status >= 400:
                body = raw.strip() or f"HTTP {status}"
                if status in (500, 503, 529):
                    raise RuntimeError(f"[fail_fast:{status}] {body}")
                raise RuntimeError(body)

            try:
                data = json.loads(raw)
            except Exception as exc:
                raise RuntimeError("LLM HTTP fallback returned invalid JSON") from exc

            content = (
                (((data.get("choices") or [{}])[0]).get("message") or {}).get("content")
                or (((data.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                or ""
            )
            if isinstance(content, list):
                content = "\n".join(
                    str(item.get("text") or item.get("content") or "")
                    for item in content
                    if isinstance(item, dict)
                ).strip()
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("LLM HTTP fallback returned empty content")
            print(f"[LLM][{request_id}] done elapsed={time.time() - t0:.2f}s content_len={len(content)}")
            end_trace(run, {"content_preview": content[:500]})
            return SimpleNamespace(content=content)

    # 在部分 Windows 环境里，Python 套接字会被拦，但 PowerShell 的 Web 请求还能正常走。
    def _invoke_via_powershell_http(self, endpoint: str, payload: dict[str, Any]):
        body_json = json.dumps(payload, ensure_ascii=False)
        escaped_body = body_json.replace("'", "''")
        escaped_endpoint = endpoint.replace("'", "''")
        escaped_key = self.api_key.replace("'", "''")
        command = (
            "$ProgressPreference='SilentlyContinue'; "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"$body = @'\n{escaped_body}\n'@; "
            f"$resp = Invoke-RestMethod -Method Post -Uri '{escaped_endpoint}' "
            f"-Headers @{{ Authorization = 'Bearer {escaped_key}'; 'Content-Type' = 'application/json' }} "
            f"-Body $body -TimeoutSec {max(1, int(self.timeout_seconds))}; "
            "$content = $resp.choices[0].message.content; "
            "if ($content -is [System.Array]) { "
            "  $content = ($content | ForEach-Object { if ($_ -is [string]) { $_ } elseif ($_.text) { $_.text } elseif ($_.content) { $_.content } else { $_.ToString() } }) -join \"`n\" "
            "}; "
            "Write-Output $content"
        )
        with trace_block(
            "llm_client.powershell_http_fallback",
            run_type="llm",
            inputs={
                "provider": self.provider,
                "model": self.model_name,
                "endpoint": endpoint,
            },
            metadata={
                "base_url": self.base_url,
                "reasoning_effort": self.reasoning_effort,
            },
            tags=["llm", "powershell_fallback"],
        ) as run:
            try:
                completed = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True,
                    text=True,
                    timeout=max(1, int(self.timeout_seconds)) + 5,
                    check=False,
                )
            except Exception as exc:
                raise RuntimeError(str(exc)) from exc

            if completed.returncode != 0:
                error_text = (completed.stderr or completed.stdout or "").strip()
                raise RuntimeError(error_text or "PowerShell HTTP fallback failed")

            if (completed.stderr or "").strip() and not (completed.stdout or "").strip():
                raise RuntimeError((completed.stderr or "").strip())

            content = (completed.stdout or "").strip()
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("PowerShell HTTP fallback returned empty content")
            end_trace(run, {"content_preview": content[:500]})
            return SimpleNamespace(content=content)

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

    # 发起 LLM 请求，统一走 urllib HTTP 直连，不再经 ChatOpenAI。
    def _invoke_messages(self, system_prompt: str, user_prompt: str):
        self.require_available()
        attempts = max(1, self.max_retry_count + 1)
        last_error: LLMInvocationError | None = None

        with trace_block(
            "llm_client.invoke_messages",
            run_type="chain",
            inputs={
                "provider": self.provider,
                "model": self.model_name,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            metadata={
                "base_url": self.base_url,
                "reasoning_effort": self.reasoning_effort,
                "disable_response_storage": self.disable_response_storage,
                "max_retry_count": self.max_retry_count,
            },
            tags=["llm", "agent"],
        ) as run:
            for attempt in range(1, attempts + 1):
                try:
                    result = self._invoke_via_http(system_prompt, user_prompt)
                    end_trace(run, {"attempts": attempt, "content_preview": self._coerce_result_text(result)[:500]})
                    return result
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
                    time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))

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

    # 带 fallback 的安全调用：先尝试正常参数，失败后用更保守参数重试一次。
    def invoke_json_with_fallback(
        self, system_prompt: str, user_prompt: str, fallback: dict[str, Any]
    ) -> dict[str, Any]:
        """Try JSON call with standard params, fall back to conservative params on failure."""
        if not self.available():
            return fallback
        try:
            return self.invoke_json_required(
                system_prompt,
                user_prompt + "\n\nReturn JSON only. Do not add explanation outside the JSON payload.",
            )
        except Exception:
            pass
        # Conservative fallback: lower temperature, shorter prompt
        try:
            return self.invoke_json(system_prompt, user_prompt[:3000], fallback)
        except Exception:
            return fallback
