from __future__ import annotations

import sys
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
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def require_available(self) -> None:
        return None

    def invoke_json_required(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise AssertionError("No fake LLM responses remaining")
        return self.responses.pop(0)


class LLMWorkspaceAgentTests(unittest.TestCase):
    def test_run_structured_does_not_prefetch_tools(self) -> None:
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "B站 热点", "limit": 3}, "final": None},
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
            task_goal="回答用户问题",
            user_payload={"message": "最近 B 站有什么热点"},
            response_contract="返回一个 JSON 对象，字段必须包含：reply",
            allowed_tools=["web_search"],
            required_final_keys=["reply"],
            load_history=False,
            save_memory=False,
            enable_reflection=False,
        )

        self.assertEqual(result["agent_trace"], ["web_search"])
        self.assertEqual(result["tool_observations"][0]["action"], "web_search")

    def test_run_structured_blocks_tool_when_budget_is_exceeded(self) -> None:
        llm = FakeLLM(
            [
                {"action": "web_search", "action_input": {"query": "第一次", "limit": 3}, "final": None},
                {"action": "web_search", "action_input": {"query": "第一次", "limit": 3}, "final": None},
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
                task_goal="回答用户问题",
                user_payload={"message": "最近 B 站有什么热点"},
                response_contract="返回一个 JSON 对象，字段必须包含：reply",
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


class SearchToolTests(unittest.TestCase):
    def test_search_uses_tavily_when_available(self) -> None:
        tool = SearchTool(api_key="", tavily_api_key="tvly-test")
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {"title": "结果一", "url": "https://example.com/1", "content": "摘要一"},
                {"title": "结果二", "url": "https://example.com/2", "content": "摘要二"},
            ]
        }

        with patch("tools.search_tool.requests.post", return_value=response) as mocked_post:
            result = tool.search("B站 热点", limit=2)

        self.assertEqual(result["provider"], "tavily")
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["link"], "https://example.com/1")
        mocked_post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
