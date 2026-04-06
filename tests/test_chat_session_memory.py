from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.services.llm import run_llm_chat
from web.services.session_memory import ChatSessionMemoryStore, ensure_session_id


class FakeSessionStore:
    """模拟会话存储，用于测试场景"""

    def __init__(self) -> None:
        self.loaded: tuple[str, list[dict]] | None = None
        self.saved: tuple[str, list[dict]] | None = None

    def load_session_history(self, session_id: str, frontend_history) -> dict:
        """模拟加载会话历史，返回缓存的上下文"""
        history = list(frontend_history or [])
        self.loaded = (session_id, history)
        return {
            "history": [{"role": "assistant", "content": "cached context"}],
            "source": "redis",
            "load_ms": 1.5,
        }

    def save_session_history_async(self, session_id: str, history) -> None:
        """模拟异步保存会话历史"""
        self.saved = (session_id, list(history or []))


class FakeAgent:
    """模拟Agent，用于测试run_llm_chat的集成流程"""

    def __init__(self) -> None:
        self.kwargs = {}

    def run_structured(self, **kwargs):
        """模拟Agent执行，返回预定义的响应结构"""
        self.kwargs = kwargs
        return {
            "reply": "ok",
            "suggested_next_actions": [],
            "mode": "llm_agent",
            "tool_observations": [],
        }


class ChatSessionMemoryStoreTests(unittest.TestCase):
    """测试ChatSessionMemoryStore的会话管理功能"""

    def test_ensure_session_id_generates_for_missing_value(self) -> None:
        """测试当session_id为空时，自动生成一个新的session_id"""
        session_id, generated = ensure_session_id("")
        self.assertTrue(generated)
        self.assertTrue(session_id)

    def test_load_session_history_falls_back_then_roundtrips_in_memory(self) -> None:
        """测试会话历史加载的回退机制：先使用前端历史，再用内存缓存"""
        store = ChatSessionMemoryStore()
        frontend_history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]

        # 首次加载，使用前端传入的历史记录
        first = store.load_session_history("session-a", frontend_history)
        self.assertEqual(first["source"], "frontend_session")
        self.assertEqual(len(first["history"]), 2)

        # 保存到内存后再次加载，应从内存缓存读取
        store.save_session_history("session-a", frontend_history)
        second = store.load_session_history("session-a", [])
        self.assertEqual(second["source"], "memory")
        self.assertEqual(second["history"][0]["content"], "one")
        self.assertGreaterEqual(store.metrics_snapshot()["avg_context_load_ms"], 0.0)

    def test_load_session_history_truncates_large_history(self) -> None:
        """测试当历史记录超过限制时进行截断（限制为10条）"""
        store = ChatSessionMemoryStore()
        frontend_history = [{"role": "user", "content": f"msg-{index}"} for index in range(100)]

        result = store.load_session_history("session-large", frontend_history)

        self.assertEqual(len(result["history"]), 10)  # 限制为10条
        self.assertEqual(result["history"][-1]["content"], "msg-99")  # 最后一条是最新的

    def test_memory_cache_evicts_lru_entries(self) -> None:
        """测试内存缓存使用LRU（最近最少使用）策略进行淘汰"""
        store = ChatSessionMemoryStore(memory_cache_max_entries=2)
        payload = [{"role": "user", "content": "x"}]

        # 保存session-1和session-2，达到缓存上限
        store.save_session_history("session-1", payload)
        store.save_session_history("session-2", payload)
        # 访问session-1，使其成为最近使用
        store.load_session_history("session-1", [])
        # 再保存session-3，触发LRU淘汰
        store.save_session_history("session-3", payload)

        metrics = store.metrics_snapshot()
        self.assertEqual(metrics["memory_cache_entries"], 2)
        self.assertEqual(metrics["memory_cache_max_entries"], 2)
        self.assertGreaterEqual(metrics["memory_evictions"], 1)  # 至少有1次淘汰
        # session-2被淘汰，重新加载应返回空
        evicted = store.load_session_history("session-2", [])
        self.assertEqual(evicted["source"], "empty")

    def test_redis_failure_falls_back_to_memory_without_blocking(self) -> None:
        """测试Redis故障时自动回退到内存存储，不阻塞业务"""
        class BrokenRedis:
            def get(self, key: str):
                raise RuntimeError("redis get down")

            def setex(self, key: str, ttl: int, value: str):
                raise RuntimeError("redis set down")

        store = ChatSessionMemoryStore()
        store._redis_initialized = True
        store._redis_client = BrokenRedis()

        payload = [{"role": "user", "content": "hello"}]
        store.save_session_history("session-redis-down", payload)
        result = store.load_session_history("session-redis-down", [])

        self.assertEqual(result["source"], "memory")  # 回退到内存
        self.assertEqual(result["history"][0]["content"], "hello")
        self.assertGreaterEqual(store.metrics_snapshot()["redis_write_failures"], 1)

    def test_concurrent_writes_same_session_do_not_crash(self) -> None:
        """测试并发写入同一会话时不会崩溃"""
        store = ChatSessionMemoryStore()

        def write(index: int) -> None:
            store.save_session_history(
                "session-concurrent",
                [{"role": "assistant", "content": f"reply-{index}"}],
            )

        # 使用8个线程并发写入32条记录
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(32)))

        result = store.load_session_history("session-concurrent", [])
        self.assertEqual(result["source"], "memory")
        self.assertEqual(len(result["history"]), 1)  # 最终只保留一条
        self.assertTrue(result["history"][0]["content"].startswith("reply-"))


class RunLlmChatSessionTests(unittest.TestCase):
    """测试run_llm_chat与会话记忆的集成"""

    def test_run_llm_chat_uses_session_history_and_skips_empty_reference_extraction(self) -> None:
        """测试run_llm_chat正确使用会话历史，并在无需提取时跳过引用链接提取"""
        fake_store = FakeSessionStore()
        fake_agent = FakeAgent()

        class FakeApp:
            @staticmethod
            def get_llm_workspace_chat_agent():
                return fake_agent

        with patch("web.services.llm.app_exports", return_value=FakeApp()), patch(
            "web.services.llm.get_chat_session_memory_store",
            return_value=fake_store,
        ), patch("web.services.llm.extract_reference_links_from_tool_observations") as reference_mock:
            result = run_llm_chat(
                {
                    "session_id": "session-1",
                    "message": "帮我继续整理一下",
                    "history": [{"role": "user", "content": "frontend fallback"}],
                    "context": {"field": "科技", "videoLink": ""},
                }
            )

        # 验证session_id传递正确
        self.assertEqual(result["session_id"], "session-1")
        # 验证会话上下文来源于redis
        self.assertEqual(result["session_context_source"], "redis")
        # 验证Agent收到的历史是缓存的上下文，而非前端传入的
        self.assertEqual(fake_agent.kwargs["user_payload"]["history"][0]["content"], "cached context")
        # 验证Agent配置：禁用历史加载和内存保存（因为上下文已通过payload传入）
        self.assertFalse(fake_agent.kwargs["load_history"])
        self.assertFalse(fake_agent.kwargs["save_memory"])
        # 验证Agent响应被保存回会话存储
        self.assertEqual(fake_store.saved[0], "session-1")
        self.assertEqual(fake_store.saved[1][-1]["content"], "ok")
        # 验证引用链接为空时跳过了提取
        self.assertEqual(result["reference_links"], [])
        reference_mock.assert_not_called()

        # 验证action_validator配置正确
        validator = fake_agent.kwargs["action_validator"]
        self.assertTrue(validator("retrieval", {}, [], []))


if __name__ == "__main__":
    unittest.main()
