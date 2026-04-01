"""Simple task router for the Bilibili workflow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RouteDecision:
    task_type: str
    partition: str
    strategy: str
    plan_steps: List[str] = field(default_factory=list)


class RouterChain:
    def route(self, payload: Dict[str, Any]) -> RouteDecision:
        partition = str(payload.get("partition_name") or payload.get("partition") or "knowledge").strip() or "knowledge"
        task_type = self._task_type(payload)
        strategy = self._strategy(partition, task_type)
        plan_steps = ["plan", "topic", "title", "script", "tags"]
        if payload.get("bv_id"):
            plan_steps.extend(["operate", "optimize"])
        return RouteDecision(task_type=task_type, partition=partition, strategy=strategy, plan_steps=plan_steps)

    def _task_type(self, payload: Dict[str, Any]) -> str:
        if payload.get("bv_id") or payload.get("optimization_result"):
            return "video_analysis"
        if payload.get("copywriting_result") or payload.get("style"):
            return "copywriting"
        if payload.get("seed_topic") or payload.get("topic"):
            return "topic_generation"
        return "workspace"

    def _strategy(self, partition: str, task_type: str) -> str:
        partition_mapping = {
            "knowledge": "问题拆解 + 信息增量",
            "tech": "实测对比 + 决策支持",
            "life": "真实体验 + 情绪共鸣",
            "emotion": "关系细节 + 私密表达",
            "game": "实战复盘 + 版本差异",
            "ent": "反差开场 + 互动讨论",
        }
        base = partition_mapping.get(partition, "真实场景 + 用户兴趣")
        return f"{task_type} / {base}"


def route_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    decision = RouterChain().route(payload)
    return {
        "task_type": decision.task_type,
        "partition": decision.partition,
        "strategy": decision.strategy,
        "plan_steps": decision.plan_steps,
    }
