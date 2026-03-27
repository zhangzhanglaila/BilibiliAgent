"""LangGraph 工作流编排。"""
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
    topic: str
    bv_id: str
    topic_result: Dict[str, Any]
    copywriting_result: Any
    operation_result: Any
    optimization_result: Any


class BilibiliAgentGraph:
    def __init__(self) -> None:
        self.topic_agent = TopicAgent()
        self.copy_agent = CopywritingAgent()
        self.operation_agent = OperationAgent()
        self.optimization_agent = OptimizationAgent()
        self.graph = self._build_graph()

    def _topic_node(self, state: PipelineState) -> PipelineState:
        result = self.topic_agent.run(
            partition_name=state.get("partition_name"),
            up_ids=state.get("up_ids"),
        )
        ideas = result.get("ideas", [])
        if ideas and not state.get("topic"):
            state["topic"] = ideas[0].topic
            state["style"] = state.get("style") or ideas[0].video_type
        state["topic_result"] = result
        return state

    def _copy_node(self, state: PipelineState) -> PipelineState:
        ideas = state.get("topic_result", {}).get("ideas", [])
        topic_idea = ideas[0] if ideas else None
        state["copywriting_result"] = self.copy_agent.run(
            topic=state.get("topic"),
            topic_idea=topic_idea,
            style=state.get("style", "干货"),
        )
        return state

    def _operation_node(self, state: PipelineState) -> PipelineState:
        bv_id = state.get("bv_id", "BV1Demo411111")
        state["operation_result"] = self.operation_agent.process_video_interactions(
            bv_id=bv_id,
            dry_run=True,
        )
        return state

    def _optimization_node(self, state: PipelineState) -> PipelineState:
        benchmark_videos = state.get("topic_result", {}).get("videos", [])
        bv_id = state.get("bv_id", "BV1Demo411111")
        state["optimization_result"] = self.optimization_agent.run(
            bv_id=bv_id,
            benchmark_videos=benchmark_videos,
        )
        return state

    def _build_graph(self):
        workflow = StateGraph(PipelineState)
        workflow.add_node("topic", self._topic_node)
        workflow.add_node("copy", self._copy_node)
        workflow.add_node("operate", self._operation_node)
        workflow.add_node("optimize", self._optimization_node)
        workflow.set_entry_point("topic")
        workflow.add_edge("topic", "copy")
        workflow.add_edge("copy", "operate")
        workflow.add_edge("operate", "optimize")
        workflow.add_edge("optimize", END)
        return workflow.compile()

    def run_full_pipeline(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return self.graph.invoke(state)

    def run_single_agent(self, agent_name: str, state: Dict[str, Any]) -> Dict[str, Any]:
        if agent_name == "topic":
            return {"topic_result": self.topic_agent.run(state.get("partition_name"), state.get("up_ids"))}
        if agent_name == "copy":
            return {"copywriting_result": self.copy_agent.run(topic=state.get("topic"), style=state.get("style", "干货"))}
        if agent_name == "operate":
            return {"operation_result": self.operation_agent.process_video_interactions(state.get("bv_id", "BV1Demo411111"), dry_run=state.get("dry_run", True))}
        if agent_name == "optimize":
            return {"optimization_result": self.optimization_agent.run(state.get("bv_id", "BV1Demo411111"))}
        raise ValueError(f"未知 agent: {agent_name}")
