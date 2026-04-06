from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

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
    """测试RAG（检索增强生成）相关组件的集成功能"""

    def _make_tempdir(self) -> str:
        """创建临时目录用于测试"""
        temp_root = ROOT / "tests" / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        target = temp_root / f"case_{uuid4().hex}"
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

    def test_knowledge_base_add_and_retrieve(self) -> None:
        """测试知识库的添加和检索功能"""
        tempdir = self._make_tempdir()
        try:
            kb = KnowledgeBase(persist_directory=tempdir, collection_name="test_kb")
            self.assertTrue(kb.available())

            kb.add_document(
                Document(
                    id="doc-1",
                    text="赶海视频更容易带动评论，因为观众会代入自己的海边生活体验。",
                    metadata={"source": "case", "partition": "life"},
                )
            )

            # 测试基于语义相似度的检索
            result = kb.retrieve("赶海 海边 生活 评论", limit=3)

            self.assertTrue(result["matches"])
            self.assertIn("赶海视频", result["matches"][0]["text"])
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_long_term_memory_save_and_retrieve(self) -> None:
        """测试长期记忆的保存和检索功能"""
        tempdir = self._make_tempdir()
        try:
            memory = LongTermMemory(persist_directory=tempdir, collection_name="test_memory")
            if getattr(memory, "backend", "disabled") == "disabled":
                self.fail("LongTermMemory should use a vector backend in the project venv")

            # 保存用户数据
            memory.save_user_data(
                "user-a",
                {"topic": "情侣日常记录", "copy": "今天终于见面了"},
                memory_type="copywriting",
            )

            # 检索用户历史
            result = memory.retrieve_user_history("user-a", "情侣见面 日常", limit=2)

            self.assertTrue(result["history"])
            self.assertIn("情侣日常记录", result["history"][0]["text"])
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_knowledge_base_overwrites_same_document_id(self) -> None:
        """测试使用相同ID添加文档时的覆盖逻辑"""
        tempdir = self._make_tempdir()
        try:
            kb = KnowledgeBase(persist_directory=tempdir, collection_name="test_kb_overwrite")
            kb.add_document(
                Document(
                    id="hot:BV1demo",
                    text="旧版本：播放 100 点赞 10",
                    metadata={"source": "bilibili_hot_sync", "board_type": "全站热门榜", "bvid": "BV1demo"},
                )
            )
            first_count = kb.count()

            # 使用相同ID添加新文档，应覆盖旧文档
            result = kb.add_document(
                Document(
                    id="hot:BV1demo",
                    text="新版本：播放 200 点赞 20",
                    metadata={"source": "bilibili_hot_sync", "board_type": "全站热门榜", "bvid": "BV1demo"},
                )
            )
            second_count = kb.count()
            retrieved = kb.retrieve("播放 200 点赞 20", limit=2)

            self.assertEqual(result["status"], "updated")  # 状态应为更新
            self.assertEqual(first_count, second_count)  # 数量不变
            self.assertTrue(retrieved["matches"])
            self.assertIn("新版本", retrieved["matches"][0]["text"])  # 检索到新内容
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_search_tool_returns_structured_warning_without_key(self) -> None:
        """测试搜索工具在无API密钥时返回结构化警告"""
        tool = SearchTool(api_key="", tavily_api_key="")

        result = tool.search("B站 热点 活动", limit=3)

        self.assertEqual(result["query"], "B站 热点 活动")
        self.assertEqual(result["results"], [])
        self.assertIn("warning", result)

    def test_code_interpreter_executes_python(self) -> None:
        """测试代码解释器能够执行Python代码"""
        tool = CodeInterpreterTool()

        result = tool.run({"code": "value = 1 + 2\nprint(value)\nresult = value"})

        self.assertEqual(result["error"], "")
        self.assertIn("3", result["stdout"] or result["result"])

    def test_retrieval_tool_is_callable(self) -> None:
        """测试检索工具可被正常调用"""
        tool = RetrievalTool()
        result = tool.handler({"query": "测试检索", "limit": 2})

        self.assertIn("matches", result)

    def test_router_chain_builds_plan(self) -> None:
        """测试路由链能够构建执行计划"""
        result = route_request({"partition": "life", "seed_topic": "赶海日常", "bv_id": "BV1demo"})

        self.assertEqual(result["partition"], "life")
        self.assertIn("topic", result["plan_steps"])  # 包含主题规划步骤
        self.assertIn("optimize", result["plan_steps"])  # 包含优化步骤

    def test_ingest_uploaded_file_uses_add_document(self) -> None:
        """测试文件上传后使用add_document存储"""
        with patch("knowledge_sync.add_document") as mocked_add_document, patch("knowledge_sync.document_exists") as mocked_exists:
            mocked_exists.return_value = False
            mocked_add_document.return_value = {"status": "ok", "document_id": "file:test:1", "chunk_count": 2}

            result = ingest_uploaded_file("test.md", "# Sample\n\nB站案例库".encode("utf-8"))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["filename"], "test.md")
            mocked_add_document.assert_called_once()
            document = mocked_add_document.call_args.args[0]
            self.assertIsInstance(document, Document)
            self.assertIn("B站案例库", document.text)
            self.assertTrue(str(document.id).startswith("file:"))

    def test_update_chroma_knowledge_base_delegates_to_crawler(self) -> None:
        """测试更新Chroma知识库时委托给爬虫"""
        with patch("knowledge_sync.crawl_and_store_bilibili_hot_videos") as mocked_crawler:
            mocked_crawler.return_value = {"status": "ok", "total_saved": 3, "boards": []}

            result = update_chroma_knowledge_base(per_board_limit=6)

            self.assertEqual(result["status"], "ok")
            mocked_crawler.assert_called_once_with(per_board_limit=6)

    def test_embedding_interface_stays_compatible(self) -> None:
        """测试嵌入接口的兼容性"""
        embeddings = build_default_embeddings()
        docs = embeddings.embed_documents(["两性情感", "赶海日常"])
        query = embeddings.embed_query("生活内容")

        self.assertEqual(len(docs), 2)  # 两个文档的嵌入
        self.assertTrue(all(isinstance(vector, list) for vector in docs))  # 嵌入为向量列表
        self.assertIsInstance(query, list)
        self.assertTrue(len(query) > 0)  # 查询嵌入非空


if __name__ == "__main__":
    unittest.main()
