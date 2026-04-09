"""LangSmith tracing helpers with safe fallbacks."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
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


# 检查 LangSmith 依赖库是否可用（导入成功且非 None）。
def langsmith_available() -> bool:
    return bool(_langsmith_traceable and _langsmith_trace and Client)


# 检查 LangSmith 功能是否已启用（配置中开启了 tracing 且提供了 API Key）。
def langsmith_enabled() -> bool:
    return bool(CONFIG.langsmith_tracing and (CONFIG.langsmith_api_key or "").strip())


# 配置 LangSmith 环境变量，根据配置设置项目名称、API Key、端点等。
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


# traceable 装饰器的安全封装，当 LangSmith 不可用时直接返回原函数。
def traceable(*args: Any, **kwargs: Any):
    if not langsmith_available():
        if args and callable(args[0]) and not kwargs:
            return args[0]

        def passthrough(func):
            return func

        return passthrough
    return _langsmith_traceable(*args, **kwargs)


# 创建一个 LangSmith trace 块，用于包裹需要追踪的代码段。
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


# 结束一个 LangSmith trace 记录，将输出结果附加到该 run 上。
def end_trace(run: Any, outputs: Any) -> None:
    if run is None:
        return
    try:
        run.end(outputs=outputs)
    except Exception:
        return


# 获取 LangSmith 客户端实例，用于直接操作 trace 数据。
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


# 刷新所有_pending状态的 trace，确保它们被发送到 LangSmith 服务器。
def flush_traces() -> None:
    if wait_for_all_tracers is None:
        return
    try:
        wait_for_all_tracers()
    except Exception:
        return


# 本地 trace 保存目录
TRACE_LOCAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "test")


def _ensure_trace_dir() -> str:
    """确保 trace 保存目录存在"""
    os.makedirs(TRACE_LOCAL_DIR, exist_ok=True)
    return TRACE_LOCAL_DIR


def _format_duration(seconds: float) -> str:
    """格式化 duration 为可读字符串"""
    if seconds is None:
        return "0s"
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """兼容字典和 Pydantic 模型的属性获取"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _calc_latency(run: Any) -> float:
    """计算 run 的延迟（秒）"""
    start = _get_attr(run, "start_time")
    end = _get_attr(run, "end_time")
    if start and end:
        return (end - start).total_seconds()
    return 0


def _build_trace_tree(runs: list, parent_map: dict, level: int = 0) -> list[str]:
    """构建 trace 树形结构的文本表示"""
    lines = []
    for run in runs:
        indent = "  " * level
        name = _get_attr(run, "name") or "unnamed"
        run_type = _get_attr(run, "run_type") or ""
        latency = _calc_latency(run)
        total_tokens = _get_attr(run, "total_tokens")
        extra_info = ""
        if total_tokens:
            extra_info = f" / {total_tokens//1000}k" if total_tokens >= 1000 else f" / {total_tokens}"
        lines.append(f"{indent}{name} ({run_type}) - {_format_duration(latency)}{extra_info}")

        run_id = _get_attr(run, "id")
        children = parent_map.get(run_id) if run_id else None
        if children:
            lines.extend(_build_trace_tree(children, parent_map, level + 1))
    return lines


def _format_run_as_markdown(run: Any, depth: int = 0) -> str:
    """将单个 run 格式化为 Markdown 文本"""
    lines = []
    indent = "  " * depth
    name = _get_attr(run, "name") or "unnamed"
    run_type = _get_attr(run, "run_type") or ""

    lines.append(f"{indent}#### {name}")
    lines.append("")
    lines.append(f"{indent}```")
    lines.append(f"{indent}json")
    lines.append(f"{indent}{{")

    # inputs
    inputs = _get_attr(run, "inputs") or {}
    if inputs:
        lines.append(f"{indent}  \"inputs\": {json.dumps(inputs, ensure_ascii=False, indent=2)}".replace("\n", f"\n{indent}  "))

    # outputs
    outputs = _get_attr(run, "outputs") or {}
    if outputs:
        if inputs:
            lines.append(f"{indent}  \"outputs\": {json.dumps(outputs, ensure_ascii=False, indent=2)}".replace("\n", f"\n{indent}  "))
        else:
            lines.append(f"{indent}  \"outputs\": {json.dumps(outputs, ensure_ascii=False, indent=2)}".replace("\n", f"\n{indent}  "))

    # token usage
    total_tokens = _get_attr(run, "total_tokens")
    prompt_tokens = _get_attr(run, "prompt_tokens")
    completion_tokens = _get_attr(run, "completion_tokens")
    if total_tokens or prompt_tokens or completion_tokens:
        token_info = {"total_tokens": total_tokens, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        lines.append(f"{indent}  \"token_usage\": {json.dumps(token_info, ensure_ascii=False, indent=2)}".replace("\n", f"\n{indent}  "))

    # extra info
    extra = _get_attr(run, "extra") or {}
    if extra:
        lines.append(f"{indent}  \"extra\": {json.dumps(extra, ensure_ascii=False, indent=2)}".replace("\n", f"\n{indent}  "))

    lines.append(f"{indent}}}")
    lines.append(f"{indent}```")
    lines.append("")

    return "\n".join(lines)


def export_recent_traces_to_file(
    module_type: str = "unknown",
    limit: int = 1,
    trace_name_contains: str | None = None,
) -> list[str]:
    """
    导出最近的 trace 到本地文件。

    Args:
        module_type: 模块类型，用于生成文件名（如 "视频分析", "内容创作", "智能对话"）
        limit: 最多导出的 root run 数量（默认1）
        trace_name_contains: 只导出 name 包含此字符串的 trace

    Returns:
        保存的文件路径列表
    """
    client = get_langsmith_client()
    if not client:
        return []

    try:
        project_name = (CONFIG.langsmith_project or "").strip() or "bilibili-hot-rag"
        now = datetime.now()

        # 列出最近的 root runs，使用正确的 API 字段名
        runs = list(client.list_runs(
            project_name=project_name,
            is_root=True,
            limit=limit,
            select=["name", "run_type", "start_time", "end_time", "error", "id", "parent_run_id", "trace_id", "total_tokens", "prompt_tokens", "completion_tokens"],
        ))

        if not runs:
            return []

        # 按 start_time 排序（最新的在前）
        runs.sort(key=lambda r: _get_attr(r, "start_time") or now, reverse=True)

        # 过滤出包含指定字符串的 trace
        if trace_name_contains:
            runs = [r for r in runs if trace_name_contains in (_get_attr(r, "name") or "")]

        if not runs:
            return []

        saved_files = []
        trace_dir = _ensure_trace_dir()

        for run in runs:
            trace_id = _get_attr(run, "trace_id") or _get_attr(run, "id")
            if not trace_id:
                continue

            # 获取完整的 trace 树
            all_runs = list(client.list_runs(
                project_name=project_name,
                trace_id=trace_id,
                select=["name", "run_type", "start_time", "end_time", "error", "id", "parent_run_id", "trace_id", "total_tokens", "prompt_tokens", "completion_tokens", "inputs", "outputs", "extra"],
            ))

            # 构建 parent_map 用于树形显示
            parent_map: dict = {}
            root_runs = []
            for r in all_runs:
                parent_id = _get_attr(r, "parent_run_id")
                if parent_id:
                    if parent_id not in parent_map:
                        parent_map[parent_id] = []
                    parent_map[parent_id].append(r)
                else:
                    root_runs.append(r)

            # 按时间排序 children
            for pid in parent_map:
                parent_map[pid].sort(key=lambda r: _get_attr(r, "start_time") or now)

            # 生成 Markdown 内容
            lines = []
            lines.append("全链路")
            lines.append("===")
            lines.append("")

            # 树形概览
            lines.append("```")
            for root in root_runs:
                name = _get_attr(root, "name") or "unnamed"
                latency = _calc_latency(root)
                total_tokens = _get_attr(root, "total_tokens")
                extra = f" / {total_tokens//1000}k" if total_tokens and total_tokens >= 1000 else (f" / {total_tokens}" if total_tokens else "")
                lines.append(f"\n{name}")
                start_time = _get_attr(root, "start_time")
                if start_time:
                    lines.append(f"{start_time.strftime('%I:%M %p') if hasattr(start_time, 'strftime') else start_time}")
                lines.append(f"{_format_duration(latency)}")
                if total_tokens:
                    lines.append(f"{total_tokens}{extra}")
                children = parent_map.get(_get_attr(root, "id"))
                if children:
                    lines.extend(_build_trace_tree(children, parent_map, 1))
            lines.append("```")
            lines.append("")

            # 细分部分
            lines.append("细分")
            lines.append("===")
            lines.append("")

            for r in all_runs:
                lines.append(_format_run_as_markdown(r, 0))

            # 生成文件名
            timestamp = now.strftime("%Y%m%d%H%M%S")
            module_suffix = {
                "module_create": "内容创作",
                "module_analyze": "视频分析",
                "workspace_chat": "智能对话",
            }.get(module_type, module_type)

            filename = f"langsmith记录{module_suffix}{timestamp}.md"
            filepath = os.path.join(trace_dir, filename)

            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            saved_files.append(filepath)

        return saved_files

    except Exception:
        return []
