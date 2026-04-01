"""LangGraph workflow orchestration for the Bilibili agents."""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from agents.copywriting_agent import CopywritingAgent
from agents.operation_agent import OperationAgent
from agents.optimization_agent import OptimizationAgent
from agents.topic_agent import TopicAgent
from chains.router_chain import RouterChain


class PipelineState(TypedDict, total=False):
    partition_name: str
    up_ids: List[int]
    style: str
    seed_topic: str
    topic: str
    bv_id: str
    dry_run: bool
    topic_result: Dict[str, Any]
    copywriting_result: Any
    operation_result: Any
    optimization_result: Any
    plan: Dict[str, Any]
    title_result: List[str]
    script_result: List[Dict[str, str]]
    tag_result: Dict[str, Any]


class BilibiliAgentGraph:
    # 初始化整条工作流需要的 Agent，并编译 LangGraph 图。
    def __init__(self) -> None:
        self.topic_agent = TopicAgent()
        self.copy_agent = CopywritingAgent()
        self.operation_agent = OperationAgent()
        self.optimization_agent = OptimizationAgent()
        self.router = RouterChain()
        self.graph = self._build_graph()

    # 先根据输入生成执行计划，明确任务类型、策略和执行步骤。
    def _plan_node(self, state: PipelineState) -> PipelineState:
        route = self.router.route(dict(state))
        state["plan"] = {
            "task_type": route.task_type,
            "partition": route.partition,
            "strategy": route.strategy,
            "steps": route.plan_steps,
        }
        if not state.get("partition_name"):
            state["partition_name"] = route.partition
        return state

    # 执行选题节点，把主题结果写回状态，并补齐后续节点要用的 topic/style。
    def _topic_node(self, state: PipelineState) -> PipelineState:
        seed_topic = state.get("seed_topic") or state.get("topic")
        result = self.topic_agent.run(
            partition_name=state.get("partition_name"),
            up_ids=state.get("up_ids"),
            seed_topic=seed_topic,
        )
        ideas = result.get("ideas", [])
        if ideas:
            state["topic"] = ideas[0].topic
            state["style"] = state.get("style") or ideas[0].video_type
        elif seed_topic and not state.get("topic"):
            state["topic"] = seed_topic
        state["topic_result"] = result
        return state

    # 生成标题阶段，先出完整文案草稿，再单独暴露标题结果给后续节点复用。
    def _title_node(self, state: PipelineState) -> PipelineState:
        ideas = state.get("topic_result", {}).get("ideas", [])
        topic_idea = ideas[0] if ideas else None
        copy_result = self.copy_agent.run(
            topic=state.get("topic"),
            topic_idea=topic_idea,
            style=state.get("style", "干货"),
        )
        state["copywriting_result"] = copy_result
        state["title_result"] = list(getattr(copy_result, "titles", []) or [])
        return state

    # 生成脚本阶段，提炼脚本部分并写回状态。
    def _script_node(self, state: PipelineState) -> PipelineState:
        copy_result = state.get("copywriting_result")
        script = list(getattr(copy_result, "script", []) or [])
        state["script_result"] = script
        return state

    # 生成标签阶段，提取标签、简介和置顶评论，供后续直接发布或展示。
    def _tag_node(self, state: PipelineState) -> PipelineState:
        copy_result = state.get("copywriting_result")
        state["tag_result"] = {
            "tags": list(getattr(copy_result, "tags", []) or []),
            "description": getattr(copy_result, "description", ""),
            "pinned_comment": getattr(copy_result, "pinned_comment", ""),
        }
        return state

    # 执行运营节点，给目标视频生成互动处理建议。
    def _operation_node(self, state: PipelineState) -> PipelineState:
        bv_id = state.get("bv_id", "BV1Demo411111")
        state["operation_result"] = self.operation_agent.process_video_interactions(
            bv_id=bv_id,
            dry_run=state.get("dry_run", True),
        )
        return state

    # 执行优化节点，结合对标样本产出标题和内容优化建议。
    def _optimization_node(self, state: PipelineState) -> PipelineState:
        benchmark_videos = state.get("topic_result", {}).get("videos", [])
        bv_id = state.get("bv_id", "BV1Demo411111")
        state["optimization_result"] = self.optimization_agent.run(
            bv_id=bv_id,
            benchmark_videos=benchmark_videos,
        )
        return state

    # 定义并编译整条 LangGraph 流水线。
    def _build_graph(self):
        workflow = StateGraph(PipelineState)
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("topic", self._topic_node)
        workflow.add_node("title", self._title_node)
        workflow.add_node("script", self._script_node)
        workflow.add_node("tags", self._tag_node)
        workflow.add_node("operate", self._operation_node)
        workflow.add_node("optimize", self._optimization_node)
        workflow.set_entry_point("plan")
        workflow.add_edge("plan", "topic")
        workflow.add_edge("topic", "title")
        workflow.add_edge("title", "script")
        workflow.add_edge("script", "tags")
        workflow.add_edge("tags", "operate")
        workflow.add_edge("operate", "optimize")
        workflow.add_edge("optimize", END)
        return workflow.compile()

    # 运行完整流水线并返回最终状态。
    def run_full_pipeline(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.graph.invoke(state)

    # 按名称单独运行某一个 Agent，方便 Web 和 CLI 按需复用。
    def run_single_agent(self, agent_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
        if agent_name == "plan":
            planning_state: PipelineState = dict(state)
            return {"plan": self._plan_node(planning_state).get("plan", {})}
        if agent_name == "topic":
            return {
                "topic_result": self.topic_agent.run(
                    partition_name=state.get("partition_name"),
                    up_ids=state.get("up_ids"),
                    seed_topic=state.get("seed_topic") or state.get("topic"),
                )
            }
        if agent_name == "copy":
            result = self.copy_agent.run(
                topic=state.get("topic"),
                style=state.get("style", "干货"),
            )
            return {
                "copywriting_result": result,
                "title_result": getattr(result, "titles", []),
                "script_result": getattr(result, "script", []),
                "tag_result": {
                    "tags": getattr(result, "tags", []),
                    "description": getattr(result, "description", ""),
                    "pinned_comment": getattr(result, "pinned_comment", ""),
                },
            }
        if agent_name == "operate":
            return {
                "operation_result": self.operation_agent.process_video_interactions(
                    state.get("bv_id", "BV1Demo411111"),
                    dry_run=state.get("dry_run", True),
                )
            }
        if agent_name == "optimize":
            return {"optimization_result": self.optimization_agent.run(state.get("bv_id", "BV1Demo411111"))}
        raise ValueError(f"unknown agent: {agent_name}")
