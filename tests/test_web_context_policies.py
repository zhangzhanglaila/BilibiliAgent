from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import (
    KNOWLEDGE_BASE,
    VIDEO_ANALYZE_RETRIEVAL_FILTER,
    VIDEO_ANALYZE_REQUIRED_FINAL_KEYS,
    VIDEO_ANALYZE_REQUIRED_TOOLS,
    build_empty_market_snapshot,
    build_hot_peer_market_snapshot,
    build_video_analyze_preloaded_context,
    finalize_module_analyze_result,
    run_llm_module_analyze,
    video_analyze_action_validator,
    video_analyze_retrieval_tool_handler,
)


class WebContextPolicyTests(unittest.TestCase):
    def test_build_empty_market_snapshot_has_no_hot_board_or_samples(self) -> None:
        snapshot = build_empty_market_snapshot("life")

        self.assertEqual(snapshot["hot_board"], [])
        self.assertEqual(snapshot["partition_samples"], [])
        self.assertEqual(snapshot["peer_samples"], [])
        self.assertEqual(snapshot["source_count"], 0)

    def test_build_hot_peer_market_snapshot_only_keeps_peer_samples(self) -> None:
        resolved = {
            "bv_id": "BV1demo",
            "title": "旗袍展示",
            "topic": "旗袍美女展示",
            "partition": "ent",
            "partition_label": "娱乐",
            "tname": "颜值",
            "keywords": ["旗袍", "美女"],
        }
        with patch("web.app.serialize_video_metric", return_value={"title": "Benchmark Sample", "url": "https://example.com"}):
            with patch("web.app.RAW_TOPIC_AGENT.fetch_hot_peer_videos", return_value=[{"id": 1}, {"id": 2}]):
                snapshot = build_hot_peer_market_snapshot(resolved)

        self.assertEqual(snapshot["hot_board"], [])
        self.assertEqual(snapshot["partition_samples"], [])
        self.assertEqual(len(snapshot["peer_samples"]), 2)
        self.assertEqual(snapshot["source_count"], 2)

    def test_video_analyze_retrieval_applies_static_filter_and_drops_dirty_matches(self) -> None:
        with patch.object(
            KNOWLEDGE_BASE,
            "retrieve",
            return_value={
                "query": "赶海",
                "matches": [
                    {"metadata": {"source": "knowledge_base", "original_source": "bilibili_hot_sync"}, "text": "keep"},
                    {"metadata": {"source": "knowledge_base", "original_source": "video_briefing"}, "text": "drop"},
                ],
            },
        ) as mocked_retrieve:
            result = video_analyze_retrieval_tool_handler({"query": "赶海", "limit": 4})

        self.assertEqual(result["metadata_filter"], VIDEO_ANALYZE_RETRIEVAL_FILTER)
        self.assertEqual(result["match_count"], 1)
        mocked_retrieve.assert_called_once_with("赶海", limit=4, metadata_filter=VIDEO_ANALYZE_RETRIEVAL_FILTER)

    def test_video_analyze_action_validator_only_allows_web_search_when_retrieval_is_insufficient(self) -> None:
        blocked_without_retrieval = video_analyze_action_validator("web_search", {}, [], [])
        blocked_with_enough_matches = video_analyze_action_validator(
            "web_search",
            {},
            [{"action": "retrieval", "observation": {"match_count": 3}}],
            ["retrieval"],
        )
        allowed_with_few_matches = video_analyze_action_validator(
            "web_search",
            {},
            [{"action": "retrieval", "observation": {"match_count": 2}}],
            ["retrieval"],
        )

        self.assertIn("必须先完成 retrieval", blocked_without_retrieval)
        self.assertIn("不允许再调用 web_search", blocked_with_enough_matches)
        self.assertEqual(allowed_with_few_matches, "")

    def test_video_analyze_action_validator_rejects_video_briefing(self) -> None:
        blocked = video_analyze_action_validator("video_briefing", {"url": "https://example.com"}, [], [])

        self.assertIn("不允许再调用 video_briefing", blocked)

    def test_run_llm_module_analyze_uses_video_analyze_agent_and_disables_memory(self) -> None:
        fake_agent = Mock()
        fake_agent.run_structured.return_value = {"analysis": {}}
        market_snapshot = build_empty_market_snapshot("life")
        resolved = {
            "bv_id": "BV1demo",
            "title": "赶海视频",
            "partition": "life",
            "partition_label": "生活",
            "topic": "赶海记录",
            "keywords": ["赶海"],
            "up_name": "测试UP",
        }

        with patch("web.app.get_video_analyze_agent", return_value=fake_agent):
            with patch("web.app.finalize_module_analyze_result", return_value={"ok": True}):
                result = run_llm_module_analyze({"url": "https://www.bilibili.com/video/BV1demo"}, resolved, market_snapshot)

        self.assertEqual(result, {"ok": True})
        kwargs = fake_agent.run_structured.call_args.kwargs
        self.assertEqual(tuple(kwargs["required_tools"]), VIDEO_ANALYZE_REQUIRED_TOOLS)
        self.assertEqual(tuple(kwargs["required_final_keys"]), VIDEO_ANALYZE_REQUIRED_FINAL_KEYS)
        self.assertFalse(kwargs["load_history"])
        self.assertFalse(kwargs["save_memory"])
        self.assertTrue(kwargs["strict_required_tool_order"])
        self.assertIs(kwargs["action_validator"], video_analyze_action_validator)
        self.assertNotIn("video_briefing", kwargs["allowed_tools"])
        self.assertEqual(kwargs["user_payload"]["market_snapshot"], market_snapshot)
        self.assertIn("preloaded_context", kwargs["user_payload"])
        self.assertEqual(kwargs["user_payload"]["preloaded_context"]["video"]["bv_id"], resolved["bv_id"])

    def test_build_video_analyze_preloaded_context_reuses_resolved_without_refetch(self) -> None:
        resolved = {
            "bv_id": "BV1demo",
            "title": "成熟的旗袍女人",
            "topic": "颜值展示视频",
            "partition": "ent",
            "partition_label": "娱乐",
            "tname": "颜值",
            "keywords": ["旗袍", "美女"],
            "style": "干货",
            "up_name": "测试UP",
            "mid": 123,
            "up_ids": [123],
            "tid": 21,
            "duration": 21,
            "stats": {"view": 123456, "like": 2345},
        }
        market_snapshot = {
            "partition": "ent",
            "partition_label": "娱乐",
            "source_count": 1,
            "hot_board": [],
            "partition_samples": [],
            "peer_samples": [{"title": "对标视频", "url": "https://example.com"}],
        }

        preloaded = build_video_analyze_preloaded_context(resolved, "https://b23.tv/demo", market_snapshot)

        self.assertEqual(preloaded["video"]["bv_id"], "BV1demo")
        self.assertEqual(preloaded["video"]["title"], "成熟的旗袍女人")
        self.assertEqual(preloaded["market_snapshot"]["peer_samples"][0]["title"], "对标视频")

    def test_finalize_module_analyze_result_tolerates_non_dict_analysis_sections(self) -> None:
        resolved = {
            "bv_id": "BV1mgQfB3E6m",
            "url": "https://www.bilibili.com/video/BV1mgQfB3E6m",
            "title": "我持续蹲守了两年半，终于抓到了当初骂我几个月的网络喷子",
            "topic": "蹲守两年半抓到网络喷子",
            "partition": "life",
            "partition_label": "生活",
            "style": "干货",
            "up_name": "瑞瑞狸",
            "keywords": ["人性", "生活记录", "喷子"],
            "stats": {
                "view": 1887936,
                "like": 78095,
                "coin": 27577,
                "favorite": 23213,
                "reply": 6111,
                "share": 9836,
                "like_rate": 0.04136527933150276,
                "coin_rate": 0.01460695701549205,
                "favorite_rate": 0.012295437980948508,
            },
        }
        result = {
            "performance": {"label": "待判断", "is_hot": False, "score": 50, "reasons": [], "summary": ""},
            "topic_result": {
                "ideas": [
                    {"topic": "继续追踪喷子后续", "reason": "剧情延展空间大", "video_type": "记录", "keywords": ["喷子", "追踪"]}
                ]
            },
            "optimize_result": ["先把矛盾冲突抛出来", "开头先给结果"],
            "copy_result": {"topic": "错误低表现文案"},
            "analysis": {
                "analysis_points": ["情绪冲突强，故事完成度高"],
                "benchmark_analysis": "具体场景 + 明确结果",
                "remake_script_structure": ["先给冲突", "中段推进", "结尾抛给观众"],
                "advanced_title_sets": "强化结果与冲突",
                "cover_plan": ["人物表情特写", "冲突字幕"],
                "tag_strategy": "生活记录, 真实故事",
                "publish_strategy": ["工作日 18:30-22:30"],
                "reusable_hit_points": "长期蹲守 + 反转抓获",
            },
        }

        final = finalize_module_analyze_result(result, resolved, build_empty_market_snapshot("life"))

        self.assertTrue(final["performance"]["is_hot"])
        self.assertGreaterEqual(final["performance"]["score"], 82)
        self.assertIsNone(final["copy_result"])
        self.assertIsInstance(final["optimize_result"], dict)
        self.assertTrue(final["optimize_result"]["diagnosis"])
        self.assertIn("middle_rhythm", final["analysis"]["remake_script_structure"])
        self.assertTrue(final["analysis"]["remake_script_structure"]["middle_rhythm"])
        self.assertIn("benchmark_videos", final["analysis"]["benchmark_analysis"])


if __name__ == "__main__":
    unittest.main()
