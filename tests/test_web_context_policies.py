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
    app,
    build_empty_market_snapshot,
    build_hot_peer_market_snapshot,
    build_module_analyze_reference_videos,
    build_reference_query_text,
    build_video_analyze_preloaded_context,
    execute_module_analyze_request,
    finalize_module_analyze_result,
    run_llm_module_create,
    run_llm_module_analyze,
    video_analyze_action_validator,
    video_analyze_retrieval_tool_handler,
)


class WebContextPolicyTests(unittest.TestCase):
    def test_execute_module_analyze_request_reports_rules_progress(self) -> None:
        resolved = {
            "bv_id": "BV1demo12345",
            "url": "https://www.bilibili.com/video/BV1demo12345",
            "title": "赶海记录",
            "topic": "赶海记录",
            "partition": "life",
            "partition_label": "生活",
            "style": "干货",
            "up_name": "测试UP",
            "up_ids": [1],
            "stats": {"view": 1200, "like": 80},
        }
        progress_events: list[dict] = []

        with patch("web.app.runtime_llm_enabled", return_value=False):
            with patch("web.app.is_resolved_payload_usable", return_value=True):
                with patch("web.app.run_topic", return_value={"ideas": [], "videos": []}):
                    with patch(
                        "web.app.classify_video_performance",
                        return_value={"label": "爆款", "is_hot": True, "score": 88, "summary": "表现较好", "reasons": ["互动率高"]},
                    ):
                        with patch("web.app.build_hot_analysis", return_value={"analysis_points": ["情绪到位"], "followup_topics": ["继续赶海"]}):
                            with patch("web.app.select_reference_videos", return_value=[]):
                                result = execute_module_analyze_request(
                                    {"url": resolved["url"], "resolved": resolved},
                                    progress_callback=lambda item: progress_events.append(dict(item)),
                                )

        self.assertEqual(result["runtime_mode"], "rules")
        self.assertEqual(result["resolved"]["bv_id"], resolved["bv_id"])
        self.assertEqual(result["reference_videos"], [])
        self.assertEqual(result["reference_videos_notice"], "当前题材公开可用的对标样本不足，暂未整理出可直接展示的参考视频。")
        self.assertEqual(
            [item["stage"] for item in progress_events],
            ["resolve_video", "video_resolved", "classify_performance", "generate_suggestions", "finalizing_result"],
        )

    def test_execute_module_analyze_request_rejects_empty_url(self) -> None:
        with self.assertRaisesRegex(Exception, "请先输入 B 站视频链接"):
            execute_module_analyze_request({})

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
        serialized_samples = [
            {
                "bvid": "BV1abcde1234",
                "title": "旗袍美女变装展示",
                "author": "测试UP1",
                "url": "https://example.com/1",
                "view": 100000,
                "like": 5000,
                "source": "同方向爆款:旗袍",
            },
            {
                "bvid": "BV2abcde1234",
                "title": "旗袍近景颜值卡点",
                "author": "测试UP2",
                "url": "https://example.com/2",
                "view": 90000,
                "like": 4200,
                "source": "同方向爆款:旗袍",
            },
        ]
        metrics_by_id = {item["bvid"]: item for item in serialized_samples}

        def fake_serialize(item: dict) -> dict:
            return dict(metrics_by_id[item["bvid"]])

        with patch("web.app.serialize_video_metric", side_effect=fake_serialize):
            with patch(
                "web.app.RAW_TOPIC_AGENT.fetch_hot_peer_videos",
                side_effect=[
                    [{"bvid": "BV1abcde1234"}],
                    [{"bvid": "BV2abcde1234"}],
                ],
            ):
                snapshot = build_hot_peer_market_snapshot(resolved)

        self.assertEqual(snapshot["hot_board"], [])
        self.assertEqual(snapshot["partition_samples"], [])
        self.assertEqual(len(snapshot["peer_samples"]), 2)
        self.assertEqual(snapshot["source_count"], 2)

    def test_build_hot_peer_market_snapshot_relaxes_fetch_when_strict_samples_are_insufficient(self) -> None:
        resolved = {
            "bv_id": "BV1demo",
            "title": "钢琴练习记录",
            "topic": "钢琴练习记录",
            "partition": "life",
            "partition_label": "生活",
            "tname": "音乐",
            "keywords": ["钢琴", "练习"],
        }
        strict_sample = {
            "bvid": "BV1strict001",
            "title": "钢琴练习日常",
            "author": "UP1",
            "url": "https://www.bilibili.com/video/BV1strict001",
            "cover": "https://example.com/1.jpg",
            "view": 150000,
            "like": 8000,
            "source": "同方向爆款:钢琴",
        }
        relaxed_sample = {
            "bvid": "BV1relaxed01",
            "title": "Una Mattina钢琴演奏版",
            "author": "UP2",
            "url": "https://www.bilibili.com/video/BV1relaxed01",
            "cover": "https://example.com/2.jpg",
            "view": 42000,
            "like": 1600,
            "source": "同方向爆款:钢琴",
        }
        metrics_by_id = {
            strict_sample["bvid"]: strict_sample,
            relaxed_sample["bvid"]: relaxed_sample,
        }

        def fake_serialize(item: dict) -> dict:
            return dict(metrics_by_id[item["bvid"]])

        with patch("web.app.serialize_video_metric", side_effect=fake_serialize):
            with patch(
                "web.app.RAW_TOPIC_AGENT.fetch_hot_peer_videos",
                side_effect=[
                    [{"bvid": strict_sample["bvid"]}],
                    [{"bvid": strict_sample["bvid"]}, {"bvid": relaxed_sample["bvid"]}],
                ],
            ) as mocked_fetch:
                snapshot = build_hot_peer_market_snapshot(resolved)

        self.assertEqual(len(snapshot["peer_samples"]), 2)
        self.assertEqual(snapshot["peer_samples"][0]["bvid"], strict_sample["bvid"])
        self.assertEqual(snapshot["peer_samples"][1]["bvid"], relaxed_sample["bvid"])
        self.assertEqual(mocked_fetch.call_count, 2)

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
        self.assertFalse(kwargs["enable_reflection"])
        self.assertTrue(kwargs["strict_required_tool_order"])
        self.assertIs(kwargs["action_validator"], video_analyze_action_validator)
        self.assertNotIn("video_briefing", kwargs["allowed_tools"])
        self.assertNotIn("market_snapshot", kwargs["user_payload"])
        self.assertIn("preloaded_context", kwargs["user_payload"])
        self.assertEqual(kwargs["user_payload"]["preloaded_context"]["video"]["bv_id"], resolved["bv_id"])

    def test_run_llm_module_create_disables_memory(self) -> None:
        fake_agent = Mock()
        fake_agent.run_structured.return_value = {
            "normalized_profile": "AI效率",
            "seed_topic": "AI剪辑提效",
            "partition": "knowledge",
            "style": "干货",
            "chosen_topic": "AI自动剪辑怎么真正省时间",
            "topic_result": {"ideas": []},
            "copy_result": {"topic": "AI自动剪辑怎么真正省时间", "style": "干货"},
        }

        with patch("web.app.get_llm_workspace_agent", return_value=fake_agent):
            result = run_llm_module_create(
                {
                    "field": "AI效率",
                    "direction": "教程",
                    "idea": "自动剪辑",
                    "partition": "knowledge",
                    "style": "干货",
                }
            )

        self.assertEqual(result["partition"], "knowledge")
        kwargs = fake_agent.run_structured.call_args.kwargs
        self.assertFalse(kwargs["load_history"])
        self.assertFalse(kwargs["save_memory"])
        self.assertNotIn("memory_user_id", kwargs["user_payload"])
        self.assertIn("preloaded_context", kwargs["user_payload"])

    def test_build_module_analyze_reference_videos_drops_unrelated_retrieval_hits(self) -> None:
        resolved = {
            "bv_id": "BV1travel123",
            "title": "【新疆旅行记录】一路向北",
            "topic": "新疆旅行记录",
            "partition": "life",
            "partition_label": "生活",
            "keywords": ["新疆", "旅行", "风景", "生活记录"],
            "up_name": "Xuannn_er",
        }
        tool_observations = [
            {
                "action": "retrieval",
                "observation": {
                    "matches": [
                        {
                            "metadata": {
                                "title": "收集了100个人的梦，我们发现了奇怪的关联",
                                "url": "https://www.bilibili.com/video/BV1PyQzB7ER5",
                                "bvid": "BV1PyQzB7ER5",
                                "author": "影视飓风",
                                "cover": "https://example.com/unrelated.jpg",
                                "partition": "知识",
                                "board_type": "每周必看",
                                "view": 7338698,
                                "like": 372614,
                            },
                            "text": "",
                            "score": 1.07,
                        }
                    ]
                },
            }
        ]

        with patch("web.app.fetch_direct_related_reference_videos", return_value=[]):
            with patch("web.app.fetch_search_reference_videos", return_value=[]):
                result = build_module_analyze_reference_videos(
                    build_empty_market_snapshot("life"),
                    tool_observations=tool_observations,
                    exclude_bvid=resolved["bv_id"],
                    query_text=build_reference_query_text(resolved),
                    resolved=resolved,
                )

        self.assertEqual(result, [])

    def test_build_module_analyze_reference_videos_uses_local_ready_samples_without_detail_fetch(self) -> None:
        resolved = {
            "bv_id": "BV1travel123",
            "title": "【新疆旅行记录】一路向北",
            "topic": "新疆旅行记录",
            "partition": "life",
            "partition_label": "生活",
            "keywords": ["新疆", "旅行", "风景", "生活记录"],
            "up_name": "Xuannn_er",
        }
        market_snapshot = build_empty_market_snapshot("life")
        market_snapshot["peer_samples"] = [
            {
                "bvid": "BV1peer12345",
                "title": "新疆自驾旅行记录，草原和雪山都在路上",
                "author": "旅行阿泽",
                "url": "https://www.bilibili.com/video/BV1peer12345",
                "cover": "https://example.com/peer.jpg",
                "view": 268000,
                "like": 16200,
                "like_rate": 0.0604,
                "source": "同方向爆款:新疆旅行",
                "partition": "life",
                "partition_label": "生活",
                "topic": "新疆旅行记录",
                "keywords": ["新疆", "旅行", "自驾", "风景"],
            }
        ]

        with patch("web.app.fetch_direct_related_reference_videos", return_value=[]):
            with patch("web.app.fetch_search_reference_videos", return_value=[]):
                with patch("web.app.fetch_reference_video_detail", side_effect=AssertionError("should not fetch detail")):
                    result = build_module_analyze_reference_videos(
                        market_snapshot,
                        tool_observations=[],
                        exclude_bvid=resolved["bv_id"],
                        query_text=build_reference_query_text(resolved),
                        resolved=resolved,
                    )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "新疆自驾旅行记录，草原和雪山都在路上")
        self.assertEqual(result[0]["source"], "同方向爆款:新疆旅行")

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

        with patch("web.app.select_reference_videos", return_value=[]):
            final = finalize_module_analyze_result(result, resolved, build_empty_market_snapshot("life"))

        self.assertTrue(final["performance"]["is_hot"])
        self.assertGreaterEqual(final["performance"]["score"], 82)
        self.assertIsNone(final["copy_result"])
        self.assertIsInstance(final["optimize_result"], dict)
        self.assertTrue(final["optimize_result"]["diagnosis"])
        self.assertIn("middle_rhythm", final["analysis"]["remake_script_structure"])
        self.assertTrue(final["analysis"]["remake_script_structure"]["middle_rhythm"])
        self.assertIn("benchmark_videos", final["analysis"]["benchmark_analysis"])

    def test_finalize_module_analyze_result_keeps_reference_videos_empty_without_llm_fallback(self) -> None:
        resolved = {
            "bv_id": "BV1Qg411r7a7",
            "url": "https://www.bilibili.com/video/BV1Qg411r7a7",
            "title": "巴哥：什么？！！人类还有这么好吃点东西！！！",
            "topic": "巴哥：什么？！！人类还有这么好吃点东西！！！",
            "partition": "life",
            "partition_label": "生活",
            "style": "干货",
            "up_name": "Xuannn_er",
            "keywords": ["生活", "狗狗", "宠物"],
            "stats": {"view": 859, "like": 18, "coin": 4, "favorite": 3, "reply": 2, "share": 1},
        }
        result = {
            "performance": {"label": "低表现", "is_hot": False, "score": 23, "reasons": [], "summary": "当前数据远低于同赛道爆款"},
            "topic_result": {"ideas": []},
            "optimize_result": {"diagnosis": "测试"},
            "copy_result": {"topic": "测试文案"},
            "analysis": {
                "benchmark_analysis": {
                    "benchmark_videos": [
                        {
                            "bvid": "BV1X5ZtYJExQ",
                            "title": "小羊把可乐赶跑了。可能是流浪过的原因，小羊居然会护食",
                            "source": "分区热门榜:动物圈",
                        },
                        {
                            "bvid": "BV1qjZpYCEGk",
                            "title": "连猫带孩子都满脸苦相",
                            "source": "分区热门榜:动物圈",
                        },
                    ]
                }
            },
        }

        with patch("web.app.select_reference_videos", return_value=[]):
            final = finalize_module_analyze_result(result, resolved, build_empty_market_snapshot("life"))

        self.assertEqual(final["reference_videos"], [])
        self.assertEqual(final["analysis"]["benchmark_analysis"]["benchmark_videos"], [])
        self.assertEqual(final["reference_videos_notice"], "暂时无法获取对标样本，请稍后重试。")

    def test_build_module_analyze_reference_videos_backfills_real_search_candidates_to_six(self) -> None:
        resolved = {
            "bv_id": "BV1KeA8zpEvD",
            "title": "法国赶海遇蜘蛛蟹繁殖，徒手能抓十几只，浸油膏蟹鲜美",
            "topic": "赶海收获记录",
            "partition": "life",
            "partition_label": "生活",
            "tname": "田园美食",
            "keywords": ["法国赶海遇蜘蛛蟹繁殖", "徒手能抓十几只", "浸油膏蟹鲜美", "冬捕冬钓大作战", "搞笑", "生活记录", "法国", "海鲜"],
            "up_name": "Yooupi食途",
        }

        def fake_search(query: str, limit: int = 8) -> list[dict]:
            items = []
            for index in range(1, 7):
                bvid = f"BV1sea{index:06d}"
                items.append(
                    {
                        "bvid": bvid,
                        "title": f"法国赶海捡海鲜第{index}集，蜘蛛蟹和海货收获满满",
                        "author": f"赶海UP{index}",
                        "cover": f"https://example.com/{index}.jpg",
                        "mid": index,
                        "view": 300000 + index * 1000,
                        "like": 18000 + index * 100,
                        "coin": 0,
                        "favorite": 5000 + index,
                        "reply": 800 + index,
                        "share": 0,
                        "duration": 600 + index,
                        "like_rate": 0.05,
                        "competition_score": 0.0,
                        "source": f"相关搜索:{query}",
                        "url": f"https://www.bilibili.com/video/{bvid}",
                        "estimated": False,
                        "partition": "life",
                        "partition_label": "生活",
                        "tname": "田园美食",
                        "topic": "赶海收获记录",
                        "keywords": ["法国", "赶海", "海鲜", "蜘蛛蟹"],
                    }
                )
            return items[:limit]

        with patch("web.app.fetch_direct_related_reference_videos", return_value=[]):
            with patch("web.app.fetch_search_reference_videos", side_effect=fake_search):
                result = build_module_analyze_reference_videos(
                    build_empty_market_snapshot("life"),
                    tool_observations=[],
                    exclude_bvid=resolved["bv_id"],
                    query_text=build_reference_query_text(resolved),
                    resolved=resolved,
                )

        self.assertEqual(len(result), 6)
        self.assertTrue(all("赶海" in item["title"] for item in result))

    def test_build_module_analyze_reference_videos_uses_current_related_fallback_when_search_unavailable(self) -> None:
        resolved = {
            "bv_id": "BV1mgQfB3E6m",
            "title": "我持续蹲守了两年半，终于抓到了当初骂我几个月的网络喷子",
            "topic": "我持续蹲守了两年半，终于抓到了当初骂我几个月的网络喷子",
            "partition": "life",
            "partition_label": "生活",
            "keywords": ["人性", "喷子", "蹲守"],
            "up_name": "瑞瑞狸",
        }

        current_related = []
        for index in range(1, 7):
            bvid = f"BV1reA{index:06d}"
            current_related.append(
                {
                    "bvid": bvid,
                    "title": f"相关故事第{index}条",
                    "author": f"相关UP{index}",
                    "cover": f"https://example.com/story-{index}.jpg",
                    "mid": index,
                    "view": 200000 + index,
                    "like": 10000 + index,
                    "coin": 0,
                    "favorite": 1000 + index,
                    "reply": 100 + index,
                    "share": 0,
                    "duration": 300 + index,
                    "like_rate": 0.05,
                    "competition_score": 0.0,
                    "source": "当前视频相关推荐",
                    "url": f"https://www.bilibili.com/video/{bvid}",
                    "estimated": False,
                }
            )

        with patch("web.app.fetch_direct_related_reference_videos", return_value=current_related):
            with patch("web.app.fetch_search_reference_videos", return_value=[]):
                with patch("web.app.fetch_reference_video_detail", return_value=None):
                    result = build_module_analyze_reference_videos(
                        build_empty_market_snapshot("life"),
                        tool_observations=[],
                        exclude_bvid=resolved["bv_id"],
                        query_text=build_reference_query_text(resolved),
                        resolved=resolved,
                    )

        self.assertEqual(len(result), 6)
        self.assertTrue(all(item["source"] == "当前视频相关推荐" for item in result))

    def test_build_module_analyze_reference_videos_uses_same_up_fallback_when_related_and_search_empty(self) -> None:
        resolved = {
            "bv_id": "BV1dcX5BYESE",
            "title": "《离 家 的 诱 惑》7.0",
            "topic": "《离 家 的 诱 惑》7.0",
            "partition": "life",
            "partition_label": "生活",
            "keywords": ["搞笑", "高能", "蝙蝠侠", "DC联名"],
            "up_name": "托马斯家的",
            "mid": 12816241,
            "up_ids": [12816241],
        }

        same_up_metrics = []
        metrics_by_id = {}
        for index in range(1, 7):
            bvid = f"BV1same{index:05d}"
            item = {
                "bvid": bvid,
                "title": f"离家的诱惑系列第{index}条",
                "author": "托马斯家的",
                "cover": f"https://example.com/same-up-{index}.jpg",
                "mid": 12816241,
                "view": 500000 + index,
                "like": 30000 + index,
                "coin": 0,
                "favorite": 1000 + index,
                "reply": 100 + index,
                "share": 10 + index,
                "duration": 180 + index,
                "avg_view_duration": 0.0,
                "like_rate": 0.06,
                "completion_rate": 0.0,
                "competition_score": 0.0,
                "source": "同类UP:12816241",
                "url": f"https://www.bilibili.com/video/{bvid}",
                "estimated": False,
            }
            same_up_metrics.append({"bvid": bvid})
            metrics_by_id[bvid] = item

        def fake_serialize(item: dict) -> dict:
            return dict(metrics_by_id[item["bvid"]])

        with patch("web.app.fetch_direct_related_reference_videos", return_value=[]):
            with patch("web.app.fetch_search_reference_videos", return_value=[]):
                with patch("web.app.serialize_video_metric", side_effect=fake_serialize):
                    with patch("web.app.fetch_reference_video_detail", return_value=None):
                        with patch("web.app.RAW_TOPIC_AGENT.fetch_peer_up_videos", return_value=same_up_metrics):
                            result = build_module_analyze_reference_videos(
                                build_empty_market_snapshot("life"),
                                tool_observations=[],
                                exclude_bvid=resolved["bv_id"],
                                query_text=build_reference_query_text(resolved),
                                resolved=resolved,
                            )

        self.assertEqual(len(result), 6)
        self.assertTrue(all(item["author"] == "托马斯家的" for item in result))
        self.assertTrue(all(item["source"] == "当前UP主近期视频" for item in result))

    def test_api_module_analyze_start_returns_job_snapshot(self) -> None:
        with app.test_client() as client:
            with patch("web.app.start_module_analyze_job", return_value={"id": "job123", "status": "queued"}):
                response = client.post("/api/module-analyze/start", json={"url": "https://www.bilibili.com/video/BV1demo"})

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["data"]["job"]["id"], "job123")

    def test_api_module_analyze_job_events_returns_404_for_unknown_job(self) -> None:
        with app.test_client() as client:
            response = client.get("/api/module-analyze/jobs/missing/events")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
