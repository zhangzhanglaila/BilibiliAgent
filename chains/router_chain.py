"""Simple task router for the Bilibili workflow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RouteDecision:
    """路由决策结果数据类

    用于存储从请求负载中提取的路由决策信息，包括任务类型、分区、策略和执行步骤。

    Attributes:
        task_type: 任务类型，如 video_analysis、copywriting、topic_generation、workspace
        partition: 内容分区，如 knowledge、tech、life、emotion、game、ent
        strategy: 执行策略，格式为 "任务类型 / 基础策略"
        plan_steps: 执行步骤列表，如 ["plan", "topic", "title", "script", "tags"]
    """
    task_type: str
    partition: str
    strategy: str
    plan_steps: List[str] = field(default_factory=list)


class RouterChain:
    """Bilibili 工作流任务路由器

    根据请求负载的内容特征，自动识别任务类型、确定内容分区，并生成相应的执行策略。
    主要用于将用户的不同类型请求（如视频分析、文案生成、主题生成等）路由到合适的处理流程。
    """

    def route(self, payload: Dict[str, Any]) -> RouteDecision:
        """根据请求负载进行路由决策

        分析 payload 中的关键字段（如 bv_id、copywriting_result、seed_topic 等），
        自动识别任务类型和内容分区，生成执行策略和步骤计划。

        Args:
            payload: 请求负载字典，包含任务的原始数据

        Returns:
            RouteDecision: 包含任务类型、分区、策略和执行步骤的路由决策对象
        """
        partition = str(payload.get("partition_name") or payload.get("partition") or "knowledge").strip() or "knowledge"
        task_type = self._task_type(payload)
        strategy = self._strategy(partition, task_type)
        plan_steps = ["plan", "topic", "title", "script", "tags"]
        if payload.get("bv_id"):
            plan_steps.extend(["operate", "optimize"])
        return RouteDecision(task_type=task_type, partition=partition, strategy=strategy, plan_steps=plan_steps)

    def _task_type(self, payload: Dict[str, Any]) -> str:
        """识别任务类型

        根据 payload 中的关键字段判断任务属于哪种类型。

        Args:
            payload: 请求负载字典

        Returns:
            str: 任务类型字符串
                  - video_analysis: 视频分析任务（存在 bv_id 或 optimization_result）
                  - copywriting: 文案生成任务（存在 copywriting_result 或 style）
                  - topic_generation: 主题生成任务（存在 seed_topic 或 topic）
                  - workspace: 工作区任务（默认类型）
        """
        if payload.get("bv_id") or payload.get("optimization_result"):
            return "video_analysis"
        if payload.get("copywriting_result") or payload.get("style"):
            return "copywriting"
        if payload.get("seed_topic") or payload.get("topic"):
            return "topic_generation"
        return "workspace"

    def _strategy(self, partition: str, task_type: str) -> str:
        """生成执行策略

        根据内容分区和任务类型，生成针对性的执行策略描述。

        Args:
            partition: 内容分区，如 knowledge、tech、life 等
            task_type: 任务类型

        Returns:
            str: 策略描述，格式为 "任务类型 / 基础策略"
        """
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
    """请求路由入口函数

    便捷的路由接口，接收请求负载并返回标准化的路由决策字典。

    Args:
        payload: 请求负载字典，包含任务相关的数据

    Returns:
        Dict[str, Any]: 包含 task_type、partition、strategy、plan_steps 的路由决策字典
    """
    decision = RouterChain().route(payload)
    return {
        "task_type": decision.task_type,
        "partition": decision.partition,
        "strategy": decision.strategy,
        "plan_steps": decision.plan_steps,
    }
