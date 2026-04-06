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
    def __init__(self) -> None:
        self.loaded: tuple[str, list[dict]] | None = None
        self.saved: tuple[str, list[dict]] | None = None

    def load_session_history(self, session_id: str, frontend_history) -> dict:
        history = list(frontend_history or [])
        self.loaded = (session_id, history)
        return {
            "history": [{"role": "assistant", "content": "cached context"}],
            "source": "redis",
            "load_ms": 1.5,
        }

    def save_session_history_async(self, session_id: str, history) -> None:
        self.saved = (session_id, list(history or []))


class FakeAgent:
    def __init__(self) -> None:
        self.kwargs = {}

    def run_structured(self, **kwargs):
        self.kwargs = kwargs
        return {
            "reply": "ok",
            "suggested_next_actions": [],
            "mode": "llm_agent",
            "tool_observations": [],
        }


class ChatSessionMemoryStoreTests(unittest.TestCase):
    def test_ensure_session_id_generates_for_missing_value(self) -> None:
        session_id, generated = ensure_session_id("")
        self.assertTrue(generated)
        self.assertTrue(session_id)

    def test_load_session_history_falls_back_then_roundtrips_in_memory(self) -> None:
        store = ChatSessionMemoryStore()
        frontend_history = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]

        first = store.load_session_history("session-a", frontend_history)
        self.assertEqual(first["source"], "frontend_session")
        self.assertEqual(len(first["history"]), 2)

        store.save_session_history("session-a", frontend_history)
        second = store.load_session_history("session-a", [])
        self.assertEqual(second["source"], "memory")
        self.assertEqual(second["history"][0]["content"], "one")
        self.assertGreaterEqual(store.metrics_snapshot()["avg_context_load_ms"], 0.0)

    def test_load_session_history_truncates_large_history(self) -> None:
        store = ChatSessionMemoryStore()
        frontend_history = [{"role": "user", "content": f"msg-{index}"} for index in range(100)]

        result = store.load_session_history("session-large", frontend_history)

        self.assertEqual(len(result["history"]), 10)
        self.assertEqual(result["history"][-1]["content"], "msg-99")

    def test_memory_cache_evicts_lru_entries(self) -> None:
        store = ChatSessionMemoryStore(memory_cache_max_entries=2)
        payload = [{"role": "user", "content": "x"}]

        store.save_session_history("session-1", payload)
        store.save_session_history("session-2", payload)
        store.load_session_history("session-1", [])
        store.save_session_history("session-3", payload)

        metrics = store.metrics_snapshot()
        self.assertEqual(metrics["memory_cache_entries"], 2)
        self.assertEqual(metrics["memory_cache_max_entries"], 2)
        self.assertGreaterEqual(metrics["memory_evictions"], 1)
        evicted = store.load_session_history("session-2", [])
        self.assertEqual(evicted["source"], "empty")

    def test_redis_failure_falls_back_to_memory_without_blocking(self) -> None:
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

        self.assertEqual(result["source"], "memory")
        self.assertEqual(result["history"][0]["content"], "hello")
        self.assertGreaterEqual(store.metrics_snapshot()["redis_write_failures"], 1)

    def test_concurrent_writes_same_session_do_not_crash(self) -> None:
        store = ChatSessionMemoryStore()

        def write(index: int) -> None:
            store.save_session_history(
                "session-concurrent",
                [{"role": "assistant", "content": f"reply-{index}"}],
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(32)))

        result = store.load_session_history("session-concurrent", [])
        self.assertEqual(result["source"], "memory")
        self.assertEqual(len(result["history"]), 1)
        self.assertTrue(result["history"][0]["content"].startswith("reply-"))


class RunLlmChatSessionTests(unittest.TestCase):
    def test_run_llm_chat_uses_session_history_and_skips_empty_reference_extraction(self) -> None:
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

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["session_context_source"], "redis")
        self.assertEqual(fake_agent.kwargs["user_payload"]["history"][0]["content"], "cached context")
        self.assertFalse(fake_agent.kwargs["load_history"])
        self.assertFalse(fake_agent.kwargs["save_memory"])
        self.assertEqual(fake_store.saved[0], "session-1")
        self.assertEqual(fake_store.saved[1][-1]["content"], "ok")
        self.assertEqual(result["reference_links"], [])
        reference_mock.assert_not_called()

        validator = fake_agent.kwargs["action_validator"]
        self.assertTrue(validator("retrieval", {}, [], []))


if __name__ == "__main__":
    unittest.main()
