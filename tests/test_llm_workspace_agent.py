from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.llm_workspace_agent import AgentTool, LLMWorkspaceAgent
from config import CONFIG
from tools.search_tool import SearchTool


class FakeLLM:
    """模拟LLM客户端，用于测试Agent的工具调用逻辑"""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def require_available(self) -> None:
        """模拟LLM可用性检查"""
        return None

    def invoke_json_required(self, system_prompt: str, user_prompt: str) -> dict:
        """模拟调用LLM并返回预定义的响应队列"""
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("No fake LLM responses remaining")
        return self.responses.pop(0)


class LLMWorkspaceAgentTests(unittest.TestCase):
    """测试LLMWorkspaceAgent的工具调用和执行流程"""

    def test_run_structured_does_not_prefetch_tools(self) -> None:
        """测试Agent不会预取工具，而是按需调用"""
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "bilibili hot", "limit": 3}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "ok"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="web_search",
                    description="search",
                    handler=lambda payload: {"query": payload.get("query", ""), "results": [{"title": "hit"}]},
                )
            ],
        )

        result = agent.run_structured(
            task_name="workspace_chat",
            task_goal="answer the user",
            user_payload={"message": "what is hot now"},
            response_contract="return JSON with reply",
            allowed_tools=["web_search"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
        )

        self.assertEqual(result["agent_trace"], ["web_search"])
        self.assertEqual(result["tool_observations"][0]["action"], "web_search")

    def test_run_structured_requires_required_tools_before_final(self) -> None:
        """测试Agent在生成最终响应前必须先调用所有必需工具"""
        llm = FakeLLM(
            [
                {"action": "final", "action_input": {}, "final": {"reply": "too early"}},
                {"action": "retrieval", "action_input": {"query": "赶海", "limit": 2}, "final": None},
                {"action": "video_briefing", "action_input": {"url": "https://www.bilibili.com/video/BV1demo"}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "done"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="retrieval",
                    description="retrieval",
                    handler=lambda payload: {"query": payload.get("query", ""), "matches": [{"text": "sample"}]},
                ),
                AgentTool(
                    name="video_briefing",
                    description="video_briefing",
                    handler=lambda payload: {"video": {"title": "sample video"}},
                ),
            ],
        )

        result = agent.run_structured(
            task_name="module_analyze",
            task_goal="analyze one video",
            user_payload={"url": "https://www.bilibili.com/video/BV1demo"},
            response_contract="return JSON with reply",
            allowed_tools=["retrieval", "video_briefing"],
            required_tools=["retrieval", "video_briefing"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
        )

        self.assertEqual(result["agent_trace"], ["retrieval", "video_briefing"])
        errors = [
            item.get("observation", {}).get("error", "")
            for item in result["tool_observations"]
            if item.get("action") == "validation_error"
        ]
        self.assertTrue(any("仍需先调用工具" in error for error in errors))

    def test_run_structured_enforces_required_tool_order(self) -> None:
        """测试Agent强制执行必需工具的调用顺序"""
        llm = FakeLLM(
            [
                {"action": "retrieval", "action_input": {"query": "赶海", "limit": 2}, "final": None},
                {"action": "video_briefing", "action_input": {"url": "https://www.bilibili.com/video/BV1demo"}, "final": None},
                {"action": "retrieval", "action_input": {"query": "赶海", "limit": 2}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "done"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="video_briefing",
                    description="video",
                    handler=lambda payload: {"video": {"title": "sample"}},
                ),
                AgentTool(
                    name="retrieval",
                    description="retrieval",
                    handler=lambda payload: {"matches": [{"text": "sample"}]},
                ),
            ],
        )

        result = agent.run_structured(
            task_name="module_analyze",
            task_goal="analyze one video",
            user_payload={"url": "https://www.bilibili.com/video/BV1demo"},
            response_contract="return JSON with reply",
            allowed_tools=["video_briefing", "retrieval"],
            required_tools=["video_briefing", "retrieval"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
            strict_required_tool_order=True,
        )

        self.assertEqual(result["agent_trace"], ["video_briefing", "retrieval"])
        errors = [
            item.get("observation", {}).get("error", "")
            for item in result["tool_observations"]
            if item.get("action") == "validation_error"
        ]
        self.assertTrue(any("当前必须先调用工具 video_briefing" in error for error in errors))

    def test_run_structured_uses_action_validator(self) -> None:
        """测试Agent使用action_validator验证工具调用的合法性"""
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "latest", "limit": 3}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "done"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="web_search",
                    description="search",
                    handler=lambda payload: {"results": [{"title": "hit"}]},
                )
            ],
        )

        result = agent.run_structured(
            task_name="workspace_chat",
            task_goal="answer the user",
            user_payload={"message": "search something"},
            response_contract="return JSON with reply",
            allowed_tools=["web_search"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
            action_validator=lambda action, *_: "blocked by validator" if action == "web_search" else "",
        )

        self.assertEqual(result["agent_trace"], [])
        errors = [
            item.get("observation", {}).get("error", "")
            for item in result["tool_observations"]
            if item.get("action") == "validation_error"
        ]
        self.assertTrue(any("blocked by validator" in error for error in errors))

    def test_run_structured_blocks_tool_when_budget_is_exceeded(self) -> None:
        """测试当工具调用预算耗尽时，Agent不再调用该工具"""
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "same", "limit": 3}, "final": None},
                {"action": "web_search", "action_input": {"query": "same", "limit": 3}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "done"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="web_search",
                    description="search",
                    handler=lambda payload: {"query": payload.get("query", ""), "results": [{"title": "hit"}]},
                )
            ],
        )

        with patch.object(
            CONFIG,
            "llm_agent_budget",
            return_value={
                "max_steps": 4,
                "max_tool_calls": 2,
                "repeat_action_limit": 1,
                "tool_limits": {"web_search": 1},
            },
        ):
            result = agent.run_structured(
                task_name="workspace_chat",
                task_goal="answer the user",
                user_payload={"message": "search something"},
                response_contract="return JSON with reply",
                allowed_tools=["web_search"],
                required_final_keys=["reply"],
                load_history=False,
                save_memory=False,
                enable_reflection=False,
            )

        self.assertEqual(result["agent_trace"], ["web_search"])
        errors = [
            item.get("observation", {}).get("error", "")
            for item in result["tool_observations"]
            if item.get("action") == "validation_error"
        ]
        self.assertTrue(any("已达上限" in error for error in errors))


    def test_run_structured_marks_tool_timeout_and_continues(self) -> None:
        """测试工具超时时的处理：标记超时状态但继续执行后续流程"""
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "slow", "limit": 3}, "final": None},
                {"action": "final", "action_input": {}, "final": {"reply": "done"}},
            ]
        )
        agent = LLMWorkspaceAgent(
            llm_client=llm,
            memory_store=None,
            tools=[
                AgentTool(
                    name="web_search",
                    description="search",
                    handler=lambda payload: (time.sleep(0.12), {"query": payload.get("query", ""), "results": []})[1],
                    timeout_seconds=0.01,
                )
            ],
        )

        result = agent.run_structured(
            task_name="workspace_chat",
            task_goal="answer the user",
            user_payload={"message": "search something"},
            response_contract="return JSON with reply",
            allowed_tools=["web_search"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
        )

        self.assertEqual(result["agent_trace"], ["web_search"])
        self.assertTrue(result["tool_observations"][0]["observation"]["timed_out"])
        self.assertEqual(result["tool_observations"][0]["observation"]["tool"], "web_search")


class SearchToolTests(unittest.TestCase):
    """测试SearchTool的搜索功能"""

    def test_search_uses_tavily_when_available(self) -> None:
        """测试当Tavily API可用时，优先使用Tavily进行搜索"""
        tool = SearchTool(api_key="", tavily_api_key="tvly-test")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"title": "result one", "url": "https://example.com/1", "content": "summary one"},
                {"title": "result two", "url": "https://example.com/2", "content": "summary two"},
            ]
        }

        with patch("tools.search_tool.requests.post", return_value=response) as mocked_post:
            result = tool.search("bilibili hot", limit=2)

        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["link"], "https://example.com/1")
        mocked_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
