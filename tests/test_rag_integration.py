from __future__ import annotations

import sys
import tempfile
import unittest
import shutil
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.llm_workspace_agent import RetrievalTool
from chains.router_chain import route_request
from knowledge_base import Document, KnowledgeBase, build_default_embeddings
from knowledge_sync import ingest_uploaded_file, update_chroma_knowledge_base
from memory.long_term_memory import LongTermMemory
from tools.code_interpreter import CodeInterpreterTool
from tools.search_tool import SearchTool


class RagIntegrationTests(unittest.TestCase):
    def _make_tempdir(self) -> str:
        return tempfile.mkdtemp()

    def test_knowledge_base_add_and_retrieve(self) -> None:
        tempdir = self._make_tempdir()
        try:
            kb = KnowledgeBase(persist_directory=tempdir, collection_name="test_kb")
            if kb.backend == "disabled":
                with self.assertRaises(RuntimeError):
                    kb.add_document(
                        Document(
                            id="doc-1",
                            text="夫妻坦白局的视频更容易引发评论，因为观众会带入自己的亲密关系经历。",
                            metadata={"source": "case", "partition": "life"},
                        )
                    )
                return

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
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_long_term_memory_save_and_retrieve(self) -> None:
        tempdir = self._make_tempdir()
        try:
            memory = LongTermMemory(persist_directory=tempdir, collection_name="test_memory")
            if getattr(memory, "backend", "disabled") == "disabled":
                with self.assertRaises(RuntimeError):
                    memory.save_user_data(
                        "user-a",
                        {"topic": "情侣日常记录", "copy": "今天终于见面了"},
                        memory_type="copywriting",
                    )
                return
            memory.save_user_data(
                "user-a",
                {"topic": "情侣日常记录", "copy": "今天终于见面了"},
                memory_type="copywriting",
            )

            result = memory.retrieve_user_history("user-a", "情侣见面 日常", limit=2)

            self.assertTrue(result["history"])
            self.assertIn("情侣日常记录", result["history"][0]["text"])
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_knowledge_base_overwrites_same_document_id(self) -> None:
        tempdir = self._make_tempdir()
        try:
            kb = KnowledgeBase(persist_directory=tempdir, collection_name="test_kb_overwrite")
            if kb.backend == "disabled":
                with self.assertRaises(RuntimeError):
                    kb.count()
                return

            kb.add_document(
                Document(
                    id="hot:BV1demo",
                    text="旧版本：播放量 100 点赞量 10",
                    metadata={"source": "bilibili_hot_sync", "board_type": "全站热门榜", "bvid": "BV1demo"},
                )
            )
            first_count = kb.count()

            result = kb.add_document(
                Document(
                    id="hot:BV1demo",
                    text="新版本：播放量 200 点赞量 20",
                    metadata={"source": "bilibili_hot_sync", "board_type": "全站热门榜", "bvid": "BV1demo"},
                )
            )
            second_count = kb.count()
            retrieved = kb.retrieve("播放量 200 点赞量 20", limit=2)

            self.assertEqual(result["status"], "updated")
            self.assertEqual(first_count, second_count)
            self.assertTrue(retrieved["matches"])
            self.assertIn("新版本", retrieved["matches"][0]["text"])
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

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
        try:
            result = tool.handler({"query": "测试检索", "limit": 2})
        except RuntimeError as exc:
            self.assertIn("Chroma", str(exc))
            return
        self.assertIn("matches", result)

    def test_router_chain_builds_plan(self) -> None:
        result = route_request({"partition": "life", "seed_topic": "夫妻坦白局", "bv_id": "BV1demo"})

        self.assertEqual(result["partition"], "life")
        self.assertIn("topic", result["plan_steps"])
        self.assertIn("optimize", result["plan_steps"])

    def test_ingest_uploaded_file_uses_add_document(self) -> None:
        with patch("knowledge_sync.add_document") as mocked_add_document, patch("knowledge_sync.document_exists") as mocked_exists:
            mocked_exists.return_value = False
            mocked_add_document.return_value = {"status": "ok", "document_id": "file:test:1", "chunk_count": 2}

            result = ingest_uploaded_file("test.md", "# Sample\n\nB站两性情感选题案例".encode("utf-8"))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["filename"], "test.md")
            mocked_add_document.assert_called_once()
            document = mocked_add_document.call_args.args[0]
            self.assertIsInstance(document, Document)
            self.assertIn("B站两性情感选题案例", document.text)
            self.assertTrue(str(document.id).startswith("file:"))

    def test_update_chroma_knowledge_base_delegates_to_crawler(self) -> None:
        with patch("knowledge_sync.crawl_and_store_bilibili_hot_videos") as mocked_crawler:
            mocked_crawler.return_value = {"status": "ok", "total_saved": 3, "boards": []}

            result = update_chroma_knowledge_base(per_board_limit=6)

            self.assertEqual(result["status"], "ok")
            mocked_crawler.assert_called_once_with(per_board_limit=6)

    def test_embedding_interface_stays_compatible(self) -> None:
        embeddings = build_default_embeddings()
        docs = embeddings.embed_documents(["两性情感", "夫妻坦白局"])
        query = embeddings.embed_query("情感话题")

        self.assertEqual(len(docs), 2)
        self.assertTrue(all(isinstance(vector, list) for vector in docs))
        self.assertIsInstance(query, list)
        self.assertTrue(len(query) > 0)


if __name__ == "__main__":
    unittest.main()
