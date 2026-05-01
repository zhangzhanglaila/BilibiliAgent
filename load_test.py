#!/usr/bin/env python
"""Load test script for BiliAgent — hits all 3 API endpoints with realistic workloads.

Usage:
    python load_test.py [--chat N] [--analyze N] [--create N] [--concurrent M] [--base-url URL]

Default: 50 chat + 30 analyze + 30 create, 4 concurrent, http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import time
import urllib.request
from typing import Any


def post_json(url: str, data: dict, timeout: float = 60.0) -> dict:
    """POST JSON and return parsed response. Raises on HTTP errors."""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"success": False, "error": f"HTTP {e.code}: {body[:300]}"}
    except Exception as e:
        return {"success": False, "error": str(e)[:300]}


# ─── Realistic test data ──────────────────────────────────────────────

# Chat: mix of simple questions (→ direct) and tool-requiring ones (→ agent)
SIMPLE_CHAT_MESSAGES = [
    "你好，介绍一下你自己",
    "什么是好的B站标题",
    "怎么提高视频完播率",
    "B站推荐算法有什么特点",
    "如何选择视频封面",
    "新手UP主应该注意什么",
    "视频时长多长合适",
    "怎么让观众三连",
    "B站粉丝怎么涨",
    "做知识区还是生活区好",
    "怎么写出吸引人的简介",
    "视频发布时间有讲究吗",
    "如何做好视频开头前5秒",
    "B站弹幕文化是什么",
    "如何跟粉丝互动",
    "视频配音有什么技巧",
    "怎么选择BGM",
    "字幕重要吗",
    "如何避免视频被下架",
    "怎么做系列视频",
]

AGENT_CHAT_MESSAGES = [
    "帮我分析一下这个视频 https://www.bilibili.com/video/BV1GJ4m1U7yM",
    "最近知识区有什么热点趋势",
    "帮我找几个AI方向的爆款选题",
    "搜索一下最近科技区什么内容火",
    "帮我对标一下这个UP主的内容风格",
    "给我推荐一些最近的排行榜视频",
    "帮我查一下知识库有没有关于剪辑技巧的内容",
    "帮我找几个同赛道的爆款视频",
    "我想看看最近的热点话题",
    "检索一下本地资料里的文案模板",
]

# Video analysis: real BVIDs from different partitions
ANALYZE_URLS = [
    "https://www.bilibili.com/video/BV1GJ4m1U7yM",  # AI/科技
    "https://www.bilibili.com/video/BV1uT4y1P7CX",  # 知识
    "https://www.bilibili.com/video/BV1hK4y1S7Gy",  # 生活
    "https://www.bilibili.com/video/BV1F44y1L7Ct",  # 游戏
    "https://www.bilibili.com/video/BV1XZ4y1c7Wj",  # 动画
    "https://www.bilibili.com/video/BV1N54y1G7xM",  # 音乐
    "https://www.bilibili.com/video/BV1eB4y1U7my",  # 影视
    "https://www.bilibili.com/video/BV1Wv4y1T7Lp",  # 时尚
    "https://www.bilibili.com/video/BV1a44y1L7kD",  # 美食
    "https://www.bilibili.com/video/BV1Ji4y1d7YQ",  # 数码
]

# Content creation: varied directions
CREATE_INPUTS = [
    {"field": "AI", "direction": "副业赚钱", "idea": "用AI工具做视频剪辑", "partition": "知识"},
    {"field": "科技", "direction": "数码评测", "idea": "iPhone 17 Pro 深度体验", "partition": "数码"},
    {"field": "生活", "direction": "日常vlog", "idea": "我的一天高效工作流程", "partition": "生活"},
    {"field": "游戏", "direction": "游戏实况", "idea": "黑神话悟空隐藏Boss攻略", "partition": "游戏"},
    {"field": "美食", "direction": "做饭教程", "idea": "15分钟搞定上班族便当", "partition": "美食"},
    {"field": "知识", "direction": "学习方法", "idea": "如何用费曼技巧快速学东西", "partition": "知识"},
    {"field": "影视", "direction": "电影解说", "idea": "2025年最值得看的5部科幻片", "partition": "影视"},
    {"field": "音乐", "direction": "乐器教学", "idea": "零基础学吉他第1课", "partition": "音乐"},
    {"field": "时尚", "direction": "穿搭分享", "idea": "春季通勤穿搭一周不重样", "partition": "时尚"},
    {"field": "动画", "direction": "动画杂谈", "idea": "进击的巨人结局深度解析", "partition": "动画"},
]


def make_chat_payload(message: str) -> dict:
    return {
        "message": message,
        "context": {
            "field": random.choice(["AI", "科技", "生活", "游戏"]),
            "direction": random.choice(["教程", "评测", "vlog", "盘点"]),
            "partition": "知识",
            "style": random.choice(["干货", "幽默", "叙事", "治愈"]),
        },
        "session_id": f"loadtest-{random.randint(1, 10)}",
    }


def make_analyze_payload(url: str) -> dict:
    return {"url": url}


def make_create_payload(inp: dict) -> dict:
    return {
        "field": inp["field"],
        "direction": inp["direction"],
        "idea": inp["idea"],
        "partition": inp["partition"],
        "style": random.choice(["干货", "幽默", "叙事"]),
    }


def fetch_metrics(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base_url}/api/metrics") as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_cache_stats(base_url: str) -> dict:
    try:
        with urllib.request.urlopen(f"{base_url}/api/cache-stats") as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}


def reset_metrics(base_url: str) -> bool:
    try:
        post_json(f"{base_url}/api/metrics/reset", {"confirm": "yes"})
        return True
    except Exception:
        return False


def run_one(args, endpoint: str, payload: dict, idx: int, total: int) -> dict:
    """Run a single request and return result with timing."""
    url = f"{args.base_url}{endpoint}"
    label = f"[{idx+1}/{total}] {endpoint}"
    print(f"  {label} ...", end=" ", flush=True)
    t0 = time.time()
    result = post_json(url, payload, timeout=args.timeout)
    elapsed = time.time() - t0
    status = "OK" if result.get("success") else f"FAIL: {result.get('error','unknown')[:60]}"
    print(f"{elapsed:.1f}s {status}")
    return {"elapsed": elapsed, "success": result.get("success", False), "endpoint": endpoint}


def run_batch(executor, args, endpoint: str, payloads: list[dict]) -> list[dict]:
    """Submit a batch of requests concurrently, return results in order."""
    futures = []
    total = len(payloads)
    for i, payload in enumerate(payloads):
        fut = executor.submit(run_one, args, endpoint, payload, i, total)
        futures.append(fut)
    return [f.result() for f in futures]


def print_separator(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_metrics_block(metrics: dict, cache: dict):
    """Pretty-print the final metrics."""
    if not metrics.get("success"):
        print(f"  Metrics fetch failed: {metrics.get('error','?')}")
        return

    data = metrics.get("data", {})
    rows = data.get("rows", [])
    summary = data.get("summary", "")

    if not rows:
        print("  No metrics collected yet.")
        return

    print(f"\n{'Module':<14} {'Path':<8} {'N':>5} {'P50':>8} {'P95':>8} {'Succ%':>7}")
    print("-" * 55)
    for r in rows:
        p50_str = "cache" if r["p50_ms"] == 0 else f"{r['p50_ms']/1000:.1f}s"
        p95_str = f"{r['p95_ms']/1000:.1f}s"
        print(
            f"{r['module']:<14} {r['path']:<8} {r['count']:>5} "
            f"{p50_str:>8} {p95_str:>8} {r['success_rate']*100:>6.1f}%"
        )

    # Cache stats
    if cache.get("success"):
        cdata = cache.get("data", {})
        print(f"\n--- Cache ---")
        for name, stats in cdata.items():
            print(f"  {name}: active={stats['active']} expired={stats['expired']} max={stats['max_size']}")

    # Overall path distribution
    path_totals = {}
    for r in rows:
        path_totals[r["path"]] = path_totals.get(r["path"], 0) + r["total_calls"]
    total_all = sum(path_totals.values()) or 1
    print(f"\n--- Path Distribution ---")
    for path, count in sorted(path_totals.items()):
        pct = count / total_all * 100
        bar = "█" * int(pct / 2)
        print(f"  {path:<8} {count:>4} ({pct:>5.1f}%) {bar}")

    # Diagnose
    print(f"\n--- Diagnosis ---")
    issues = []

    # Router: direct hit rate for chat
    chat_direct = next((r for r in rows if r["module"] == "chat" and r["path"] == "direct"), None)
    chat_agent = next((r for r in rows if r["module"] == "chat" and r["path"] == "agent"), None)
    if chat_direct and chat_agent:
        total_chat = chat_direct["total_calls"] + chat_agent["total_calls"]
        direct_pct = chat_direct["total_calls"] / max(total_chat, 1) * 100
        if direct_pct < 70:
            issues.append(f"[Router] Direct hit rate {direct_pct:.0f}% < 70% target — classifier too conservative")
        else:
            print(f"  [OK] Chat direct hit rate: {direct_pct:.0f}%")

    # P95 check
    for r in rows:
        targets = {"direct": 6000, "fast": 18000, "agent": 35000, "rules": 5000}
        target = targets.get(r["path"], 99999)
        if r["p95_ms"] > target and r["count"] > 0:
            issues.append(f"[P95] {r['module']}:{r['path']} P95={r['p95_ms']/1000:.1f}s > target {target/1000:.0f}s")

    # Cache hit rate
    cache_hit = sum(1 for r in rows if r["p50_ms"] == 0 and r["count"] > 0)
    cache_hit_pct = cache_hit / max(total_all, 1) * 100
    if cache_hit_pct < 20:
        issues.append(f"[Cache] Hit rate {cache_hit_pct:.0f}% < 20% target — key design or TTL issue")
    else:
        print(f"  [OK] Cache hit rate: {cache_hit_pct:.0f}%")

    # Success rate
    for r in rows:
        if r["success_rate"] < 0.95 and r["total_calls"] >= 5:
            issues.append(f"[Success] {r['module']}:{r['path']} success_rate={r['success_rate']*100:.0f}% < 95%")

    if issues:
        for issue in issues:
            print(f"  !! {issue}")
    else:
        print("  All targets met.")


def main():
    parser = argparse.ArgumentParser(description="BiliAgent Load Test")
    parser.add_argument("--chat", type=int, default=50, help="Number of chat requests (default: 50)")
    parser.add_argument("--analyze", type=int, default=30, help="Number of analyze requests (default: 30)")
    parser.add_argument("--create", type=int, default=30, help="Number of create requests (default: 30)")
    parser.add_argument("--concurrent", type=int, default=4, help="Concurrent workers (default: 4)")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="Server base URL")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-request timeout in seconds")
    parser.add_argument("--reset", action="store_true", help="Reset metrics before starting")
    parser.add_argument("--no-reset", action="store_true", help="Keep existing metrics")
    args = parser.parse_args()

    # Validate server is up
    print(f"Checking server at {args.base_url} ...")
    try:
        urllib.request.urlopen(f"{args.base_url}/api/runtime-info", timeout=5)
    except Exception as e:
        print(f"ERROR: Cannot reach server at {args.base_url} — is it running?")
        print(f"  {e}")
        sys.exit(1)
    print("Server is up.\n")

    # Reset metrics by default
    if not args.no_reset:
        reset_metrics(args.base_url)
        print("Metrics reset.")

    # Build payload lists
    chat_payloads = []
    for i in range(args.chat):
        if i < args.chat * 0.7:  # 70% simple → direct path
            msg = random.choice(SIMPLE_CHAT_MESSAGES)
        else:  # 30% agent-triggering → agent path
            msg = random.choice(AGENT_CHAT_MESSAGES)
        chat_payloads.append(make_chat_payload(msg))

    analyze_payloads = []
    for i in range(args.analyze):
        url = random.choice(ANALYZE_URLS)
        analyze_payloads.append(make_analyze_payload(url))

    create_payloads = []
    for i in range(args.create):
        inp = random.choice(CREATE_INPUTS)
        create_payloads.append(make_create_payload(inp))

    total = len(chat_payloads) + len(analyze_payloads) + len(create_payloads)
    print(f"Total requests: {total} ({len(chat_payloads)} chat + {len(analyze_payloads)} analyze + {len(create_payloads)} create)")
    print(f"Concurrency: {args.concurrent}\n")

    t_start = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrent) as executor:
        # Run all three modules concurrently
        futures = {}
        futures["chat"] = executor.submit(run_batch, executor, args, "/api/chat", chat_payloads)
        futures["analyze"] = executor.submit(run_batch, executor, args, "/api/module-analyze", analyze_payloads)
        futures["create"] = executor.submit(run_batch, executor, args, "/api/module-create", create_payloads)

        results = {}
        for name, fut in futures.items():
            results[name] = fut.result()

    total_elapsed = time.time() - t_start

    # Summarize
    all_results = results["chat"] + results["analyze"] + results["create"]
    success_count = sum(1 for r in all_results if r["success"])
    avg_latency = sum(r["elapsed"] for r in all_results) / max(len(all_results), 1)

    print_separator("Test Complete")
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"  Requests: {len(all_results)}")
    print(f"  Success: {success_count}/{len(all_results)} ({success_count/len(all_results)*100:.1f}%)")
    print(f"  Avg latency: {avg_latency:.1f}s")

    # Wait briefly for async metrics to flush
    time.sleep(0.5)

    # Fetch and print metrics
    metrics = fetch_metrics(args.base_url)
    cache = fetch_cache_stats(args.base_url)
    print_metrics_block(metrics, cache)

    print(f"\nDashboard: {args.base_url}/dashboard")
    print("Done.")


if __name__ == "__main__":
    main()
