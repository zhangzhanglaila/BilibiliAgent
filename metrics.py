"""Performance metrics collection for LLM call paths.

Tracks latency, path distribution, success rate across the three-layer router:
  - direct : single LLM call, no tools
  - fast   : preloaded context + single LLM call
  - agent  : full ReAct loop with tool calling
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional

logger = logging.getLogger("biliagent.metrics")

# In-memory ring buffer — last N samples per (module, path) key
_MAX_SAMPLES = 500
_metrics_lock = threading.Lock()
_latency_samples: Dict[str, List[float]] = defaultdict(list)
_success_counts: Dict[str, int] = defaultdict(int)
_failure_counts: Dict[str, int] = defaultdict(int)
_error_types: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))


def record(
    *,
    module: str,
    path: str,
    latency_ms: float,
    success: bool = True,
    error_type: str = "",
    tokens: int = 0,
) -> None:
    """Record one call sample. Thread-safe."""
    key = f"{module}:{path}"
    with _metrics_lock:
        _latency_samples[key].append(latency_ms)
        if len(_latency_samples[key]) > _MAX_SAMPLES:
            _latency_samples[key] = _latency_samples[key][-_MAX_SAMPLES:]
        if success:
            _success_counts[key] += 1
        else:
            _failure_counts[key] += 1
            if error_type:
                _error_types[key][error_type] += 1

    # Structured log for grep / dashboard
    logger.info(
        "[PERF] module=%s path=%s latency_ms=%.0f success=%s tokens=%d %s",
        module, path, latency_ms, str(success).lower(), tokens,
        f"error={error_type}" if error_type else "",
    )


def p50(samples: List[float]) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    return s[len(s) // 2]


def p95(samples: List[float]) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    idx = int(len(s) * 0.95)
    return s[min(idx, len(s) - 1)]


def stats_for(module: str, path: str) -> dict:
    """Return P50, P95, hit rate, error rate for one (module, path) pair."""
    key = f"{module}:{path}"
    with _metrics_lock:
        samples = list(_latency_samples[key])
        success = _success_counts.get(key, 0)
        failure = _failure_counts.get(key, 0)
        errors = dict(_error_types.get(key, {}))
    total = success + failure
    return {
        "module": module,
        "path": path,
        "count": len(samples),
        "total_calls": total,
        "success_rate": round(success / total, 4) if total > 0 else 0.0,
        "p50_ms": round(p50(samples), 0),
        "p95_ms": round(p95(samples), 0),
        "error_types": errors,
    }


def summary_table() -> list[dict]:
    """Return the metrics table as a list of dicts, sorted by module then path."""
    keys: set[str] = set()
    with _metrics_lock:
        keys = set(_latency_samples.keys()) | set(_success_counts.keys()) | set(_failure_counts.keys())

    rows = []
    for key in sorted(keys):
        if ":" not in key:
            continue
        module, path = key.split(":", 1)
        rows.append(stats_for(module, path))
    return rows


def summary_text() -> str:
    """Pretty-print the metrics table."""
    rows = summary_table()
    if not rows:
        return "No metrics collected yet."

    lines = [
        f"{'Module':<12} {'Path':<8} {'N':>6} {'P50(ms)':>9} {'P95(ms)':>9} {'Succ%':>7} {'Hit%':>7}"
    ]
    lines.append("-" * 60)
    for r in rows:
        total = r["total_calls"] or 1
        lines.append(
            f"{r['module']:<12} {r['path']:<8} {r['count']:>6} "
            f"{r['p50_ms']:>9.0f} {r['p95_ms']:>9.0f} "
            f"{r['success_rate']*100:>6.1f}% {'?':>7}"
        )

    # Overall hit rate per path
    lines.append("")
    lines.append("--- Path Distribution ---")
    path_totals: Dict[str, int] = defaultdict(int)
    for r in rows:
        path_totals[r["path"]] += r["total_calls"]
    total_all = sum(path_totals.values()) or 1
    for path, count in sorted(path_totals.items()):
        lines.append(f"  {path}: {count} ({count/total_all*100:.1f}%)")

    return "\n".join(lines)


def reset() -> None:
    """Clear all metrics. Useful for testing."""
    with _metrics_lock:
        _latency_samples.clear()
        _success_counts.clear()
        _failure_counts.clear()
        _error_types.clear()


# Convenience context manager for instrumenting a block
class TimedBlock:
    def __init__(self, module: str, path: str):
        self.module = module
        self.path = path
        self.start: float = 0.0
        self.success = True
        self.error_type = ""
        self.tokens = 0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.time() - self.start) * 1000
        if exc_type is not None:
            self.success = False
            self.error_type = exc_type.__name__
        record(
            module=self.module,
            path=self.path,
            latency_ms=latency_ms,
            success=self.success,
            error_type=self.error_type,
            tokens=self.tokens,
        )
        return False  # don't suppress exceptions

    def set_error(self, error_type: str) -> None:
        self.success = False
        self.error_type = error_type
