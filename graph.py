"""LangGraph workflow orchestration for the Bilibili agents."""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from agents.copywriting_agent import CopywritingAgent
from agents.operation_agent import OperationAgent
from agents.optimization_agent import OptimizationAgent
from agents.topic_agent import TopicAgent


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


class BilibiliAgentGraph:
    # 初始化整条工作流需要的 Agent，并编译 LangGraph 图。
    def __init__(self) -> None:
        self.topic_agent = TopicAgent()
        self.copy_agent = CopywritingAgent()
        self.operation_agent = OperationAgent()
        self.optimization_agent = OptimizationAgent()
        self.graph = self._build_graph()

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

    # 执行文案节点，基于上一步的主题结果生成可发布文案。
    def _copy_node(self, state: PipelineState) -> PipelineState:
        ideas = state.get("topic_result", {}).get("ideas", [])
        topic_idea = ideas[0] if ideas else None
        state["copywriting_result"] = self.copy_agent.run(
            topic=state.get("topic"),
            topic_idea=topic_idea,
            style=state.get("style", "干货"),
        )
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
        workflow.add_node("topic", self._topic_node)
        workflow.add_node("copy", self._copy_node)
        workflow.add_node("operate", self._operation_node)
        workflow.add_node("optimize", self._optimization_node)
        # 后面的文案、运营、优化都依赖这里先产出的主题、风格和对标样本。
        workflow.set_entry_point("topic")
        workflow.add_edge("topic", "copy")
        workflow.add_edge("copy", "operate")
        workflow.add_edge("operate", "optimize")
        workflow.add_edge("optimize", END)
        return workflow.compile()

    # 运行完整流水线并返回最终状态。
    def run_full_pipeline(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.graph.invoke(state)

    # 按名称单独运行某一个 Agent，方便 Web 和 CLI 按需复用。
    def run_single_agent(self, agent_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
        if agent_name == "topic":
            return {
                "topic_result": self.topic_agent.run(
                    partition_name=state.get("partition_name"),
                    up_ids=state.get("up_ids"),
                    seed_topic=state.get("seed_topic") or state.get("topic"),
                )
            }
        if agent_name == "copy":
            return {
                "copywriting_result": self.copy_agent.run(
                    topic=state.get("topic"),
                    style=state.get("style", "干货"),
                )
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
