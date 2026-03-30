"""CLI entrypoint and reusable wrappers for the Bilibili agent graph."""
from __future__ import annotations

import argparse
from typing import Any, Dict, List

from graph import BilibiliAgentGraph
from models import to_plain_data


# 把命令行里逗号分隔的 UP 主 ID 文本解析成整数列表。
def parse_up_ids(value: str | None) -> List[int] | None:
    if not value:
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip().isdigit()]


# 运行选题 Agent，并把 dataclass 结果转换成普通字典。
def run_topic(
    partition_name: str = "knowledge",
    up_ids: List[int] | None = None,
    seed_topic: str | None = None,
) -> Dict[str, Any]:
    graph = BilibiliAgentGraph()
    result = graph.run_single_agent(
        "topic",
        {
            "partition_name": partition_name,
            "up_ids": up_ids,
            "seed_topic": seed_topic,
        },
    )
    return to_plain_data(result["topic_result"])


# 运行文案 Agent，并返回适合 CLI / Web 复用的普通结构。
def run_copy(topic: str, style: str = "干货") -> Dict[str, Any]:
    graph = BilibiliAgentGraph()
    result = graph.run_single_agent("copy", {"topic": topic, "style": style})
    return to_plain_data(result["copywriting_result"])


# 运行运营 Agent，输出评论区互动建议动作。
def run_operate(bv_id: str, dry_run: bool = True) -> Dict[str, Any]:
    graph = BilibiliAgentGraph()
    result = graph.run_single_agent("operate", {"bv_id": bv_id, "dry_run": dry_run})
    return to_plain_data(result["operation_result"])


# 运行优化 Agent，生成标题、封面和内容层面的优化建议。
def run_optimize(bv_id: str) -> Dict[str, Any]:
    graph = BilibiliAgentGraph()
    result = graph.run_single_agent("optimize", {"bv_id": bv_id})
    return to_plain_data(result["optimization_result"])


# 运行完整流水线，顺序串起选题、文案、运营和优化四个阶段。
def run_pipeline(
    bv_id: str,
    partition_name: str = "knowledge",
    up_ids: List[int] | None = None,
    style: str = "干货",
    seed_topic: str | None = None,
) -> Dict[str, Any]:
    graph = BilibiliAgentGraph()
    result = graph.run_full_pipeline(
        {
            "bv_id": bv_id,
            "partition_name": partition_name,
            "up_ids": up_ids,
            "style": style,
            "seed_topic": seed_topic,
        }
    )
    return to_plain_data(result)


# 以 CLI 友好的格式打印选题结果。
def print_topic_result(result: dict) -> None:
    ideas = result.get("ideas", [])
    print("\n=== 选题结果 ===")
    for index, idea in enumerate(ideas, start=1):
        print(f"{index}. 选题：{idea.get('topic')}")
        print(f"   类型：{idea.get('video_type')}")
        print(f"   理由：{idea.get('reason')}")
        print(f"   关键词：{', '.join(idea.get('keywords', []))}")


# 以 CLI 友好的格式打印文案结果。
def print_copy_result(result: dict) -> None:
    print("\n=== 文案结果 ===")
    print("标题备选：")
    for title in result.get("titles", []):
        print(f"- {title}")
    print("\n脚本：")
    for part in result.get("script", []):
        print(f"- [{part.get('duration')}] {part.get('section')}: {part.get('content')}")
    print(f"\n简介：{result.get('description')}")
    print(f"\n标签：{', '.join(result.get('tags', []))}")
    print(f"\n置顶评论：{result.get('pinned_comment')}")


# 以 CLI 友好的格式打印运营建议结果。
def print_operation_result(result: dict) -> None:
    print("\n=== 运营结果 ===")
    print(result.get("summary", ""))
    for group_name, actions in [
        ("回复", result.get("replies", [])),
        ("删除", result.get("deletions", [])),
        ("点赞", result.get("likes", [])),
        ("关注", result.get("follows", [])),
    ]:
        print(f"\n{group_name}动作：")
        for action in actions:
            print(f"- [{action.get('action')}] {action.get('message')} (dry_run={action.get('dry_run')})")


# 以 CLI 友好的格式打印优化建议结果。
def print_optimization_result(result: dict) -> None:
    print("\n=== 数据优化结果 ===")
    print(f"诊断：{result.get('diagnosis')}")
    print("优化标题：")
    for title in result.get("optimized_titles", []):
        print(f"- {title}")
    print(f"封面建议：{result.get('cover_suggestion')}")
    print("内容调整：")
    for item in result.get("content_suggestions", []):
        print(f"- {item}")
    print(f"基准总结：{result.get('benchmark_summary')}")


# 构建命令行参数解析器，定义所有可用子命令和参数。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B站全自动运营多 Agent 系统")
    sub = parser.add_subparsers(dest="command", required=True)

    topic = sub.add_parser("topic", help="运行选题 Agent")
    topic.add_argument("--partition", default="knowledge", help="分区名称，默认 knowledge")
    topic.add_argument("--up-ids", default="", help="同类 UP 主 ID，逗号分隔")
    topic.add_argument("--topic", default="", help="当前输入链接对应的主题种子")

    copy = sub.add_parser("copy", help="运行文案 Agent")
    copy.add_argument("--topic", required=True, help="手动输入选题")
    copy.add_argument("--style", default="干货", help="文案风格：干货/教学/搞笑/混剪")

    operate = sub.add_parser("operate", help="运行运营 Agent")
    operate.add_argument("--bv", required=True, help="目标视频 BV 号")
    operate.add_argument("--dry-run", action="store_true", help="仅输出建议动作，不真实执行")

    optimize = sub.add_parser("optimize", help="运行数据优化 Agent")
    optimize.add_argument("--bv", required=True, help="目标视频 BV 号")

    pipeline = sub.add_parser("pipeline", help="运行全流程")
    pipeline.add_argument("--bv", required=True, help="目标视频 BV 号")
    pipeline.add_argument("--partition", default="knowledge", help="分区名称")
    pipeline.add_argument("--up-ids", default="", help="同类 UP 主 ID，逗号分隔")
    pipeline.add_argument("--style", default="干货", help="文案风格")
    pipeline.add_argument("--topic", default="", help="当前输入链接对应的主题种子")
    return parser


# 作为 CLI 入口，根据子命令分发到对应的 Agent 包装函数。
def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "topic":
        result = run_topic(
            partition_name=args.partition,
            up_ids=parse_up_ids(args.up_ids),
            seed_topic=args.topic or None,
        )
        print_topic_result(result)
        return

    if args.command == "copy":
        result = run_copy(topic=args.topic, style=args.style)
        print_copy_result(result)
        return

    if args.command == "operate":
        result = run_operate(bv_id=args.bv, dry_run=args.dry_run)
        print_operation_result(result)
        return

    if args.command == "optimize":
        result = run_optimize(bv_id=args.bv)
        print_optimization_result(result)
        return

    if args.command == "pipeline":
        result = run_pipeline(
            bv_id=args.bv,
            partition_name=args.partition,
            up_ids=parse_up_ids(args.up_ids),
            style=args.style,
            seed_topic=args.topic or None,
        )
        print_topic_result(result["topic_result"])
        print_copy_result(result["copywriting_result"])
        print_operation_result(result["operation_result"])
        print_optimization_result(result["optimization_result"])


if __name__ == "__main__":
    main()
