from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.llm_workspace_agent import RetrievalTool
from chains.router_chain import route_request
from knowledge_base import Document, KnowledgeBase
from memory.long_term_memory import LongTermMemory
from tools.code_interpreter import CodeInterpreterTool
from tools.search_tool import SearchTool


class RagIntegrationTests(unittest.TestCase):
    def test_knowledge_base_add_and_retrieve(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            kb = KnowledgeBase(persist_directory=tempdir, collection_name="test_kb")
            kb.add_document(
                Document(
                    id="doc-1",
                    text="夫妻坦白局的视频更容易引发评论，因为观众会带入自己的亲密关系经历。",
                    metadata={"source": "case", "partition": "life"},
                )
            )

            result = kb.retrieve("夫妻坦白局 亲密关系 评论", limit=3)

            self.assertTrue(result["matches"])
            self.assertIn("夫妻坦白局", result["matches"][0]["text"])

    def test_long_term_memory_save_and_retrieve(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            memory = LongTermMemory(persist_directory=tempdir, collection_name="test_memory")
            memory.save_user_data(
                "user-a",
                {"topic": "情侣日常记录", "copy": "今天终于见面了"},
                memory_type="copywriting",
            )

            result = memory.retrieve_user_history("user-a", "情侣见面 日常", limit=2)

            self.assertTrue(result["history"])
            self.assertIn("情侣日常记录", result["history"][0]["text"])

    def test_search_tool_returns_structured_warning_without_key(self) -> None:
        tool = SearchTool(api_key="")

        result = tool.search("B站 热点 活动", limit=3)

        self.assertEqual(result["query"], "B站 热点 活动")
        self.assertEqual(result["results"], [])
        self.assertIn("warning", result)

    def test_code_interpreter_executes_python(self) -> None:
        tool = CodeInterpreterTool()

        result = tool.run({"code": "value = 1 + 2\nprint(value)\nresult = value"})

        self.assertEqual(result["error"], "")
        self.assertIn("3", result["stdout"] or result["result"])

    def test_retrieval_tool_is_callable(self) -> None:
        tool = RetrievalTool()

        result = tool.handler({"query": "测试检索", "limit": 2})

        self.assertIn("matches", result)

    def test_router_chain_builds_plan(self) -> None:
        result = route_request({"partition": "life", "seed_topic": "夫妻坦白局", "bv_id": "BV1demo"})

        self.assertEqual(result["partition"], "life")
        self.assertIn("topic", result["plan_steps"])
        self.assertIn("optimize", result["plan_steps"])


if __name__ == "__main__":
    unittest.main()
