"""LangSmith tracing helpers with safe fallbacks."""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from config import CONFIG

try:
    from langsmith import Client, traceable as _langsmith_traceable
    from langsmith.run_helpers import trace as _langsmith_trace
except Exception:  # pragma: no cover
    Client = None
    _langsmith_traceable = None
    _langsmith_trace = None

try:
    from langchain_core.tracers.langchain import wait_for_all_tracers
except Exception:  # pragma: no cover
    wait_for_all_tracers = None


def langsmith_available() -> bool:
    return bool(_langsmith_traceable and _langsmith_trace and Client)


def langsmith_enabled() -> bool:
    return bool(CONFIG.langsmith_tracing and (CONFIG.langsmith_api_key or "").strip())


def configure_langsmith(entrypoint: str = "app") -> dict[str, Any]:
    project_name = (CONFIG.langsmith_project or "").strip() or f"bilibili-hot-rag-{entrypoint}"
    api_key = (CONFIG.langsmith_api_key or "").strip()
    endpoint = (CONFIG.langsmith_endpoint or "").strip()
    enabled = bool(langsmith_available() and CONFIG.langsmith_tracing and api_key)

    os.environ["LANGCHAIN_TRACING_V2"] = "true" if enabled else "false"
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    os.environ["LANGSMITH_PROJECT"] = project_name
    os.environ["LANGCHAIN_PROJECT"] = project_name
    os.environ["LANGCHAIN_CALLBACKS_BACKGROUND"] = "true" if CONFIG.langchain_callbacks_background else "false"

    if api_key:
        os.environ["LANGSMITH_API_KEY"] = api_key
        os.environ["LANGCHAIN_API_KEY"] = api_key
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint
        os.environ["LANGCHAIN_ENDPOINT"] = endpoint

    return {
        "enabled": enabled,
        "project_name": project_name,
        "endpoint": endpoint,
        "available": langsmith_available(),
    }


def traceable(*args: Any, **kwargs: Any):
    if not langsmith_available():
        if args and callable(args[0]) and not kwargs:
            return args[0]

        def passthrough(func):
            return func

        return passthrough
    return _langsmith_traceable(*args, **kwargs)


@contextmanager
def trace_block(
    name: str,
    *,
    run_type: str = "chain",
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    project_name: str | None = None,
) -> Iterator[Any]:
    if not langsmith_available() or not langsmith_enabled():
        yield None
        return
    with _langsmith_trace(
        name=name,
        run_type=run_type,
        inputs=inputs or {},
        metadata=metadata or {},
        tags=tags or [],
        project_name=project_name or ((CONFIG.langsmith_project or "").strip() or None),
    ) as run:
        yield run


def end_trace(run: Any, outputs: Any) -> None:
    if run is None:
        return
    try:
        run.end(outputs=outputs)
    except Exception:
        return


def get_langsmith_client() -> Any | None:
    if not langsmith_available() or not langsmith_enabled():
        return None
    try:
        client_kwargs = {"api_key": (CONFIG.langsmith_api_key or "").strip()}
        endpoint = (CONFIG.langsmith_endpoint or "").strip()
        if endpoint:
            client_kwargs["api_url"] = endpoint
        return Client(**client_kwargs)
    except Exception:
        return None


def flush_traces() -> None:
    if wait_for_all_tracers is None:
        return
    try:
        wait_for_all_tracers()
    except Exception:
        return
