from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.topic_agent import TopicAgent
from web.app import (
    build_creator_reason,
    build_hot_peer_market_snapshot,
    build_reference_query_text,
    build_llm_video_payload_from_resolved,
    build_resolved_payload,
    build_video_benchmark_profile,
    build_video_benchmark_queries,
    normalize_analysis_topics,
    select_creator_topic_cue,
)


class VideoAnalysisRuleTests(unittest.TestCase):
    def test_build_resolved_payload_maps_piano_video_to_ent_music(self) -> None:
        info = {
            "owner": {"mid": 1, "name": "Xuannn_er"},
            "title": "《触不可及》Una Mattina钢琴转调",
            "tid": 21,
            "tname": "",
            "keywords": ["钢琴", "练习", "记录"],
            "duration": 39,
            "stat": {
                "view": 130,
                "like": 3,
                "coin": 1,
                "favorite": 4,
                "reply": 2,
                "share": 0,
            },
        }

        resolved = build_resolved_payload(info, "BV19t46ecECy")

        self.assertEqual(resolved["partition"], "ent")
        self.assertEqual(resolved["partition_label"], "娱乐")
        self.assertEqual(resolved["tname"], "音乐")
        self.assertIn("钢琴", resolved["keywords"])

    def test_build_resolved_payload_maps_sea_harvest_video_to_life(self) -> None:
        info = {
            "owner": {"mid": 1, "name": "大庆赶海"},
            "title": "大庆赶海，随着潮水发现和拳头一样的大毛蛤，还正在张着大嘴",
            "tid": 214,
            "tname": "",
            "keywords": [],
            "duration": 193,
            "stat": {
                "view": 1198920,
                "like": 35188,
                "coin": 0,
                "favorite": 3125,
                "reply": 0,
                "share": 0,
            },
        }

        resolved = build_resolved_payload(info, "BV1YWA8zFEbc")

        self.assertEqual(resolved["partition"], "life")
        self.assertEqual(resolved["partition_label"], "生活")
        self.assertEqual(resolved["tname"], "田园美食")
        self.assertEqual(resolved["topic"], "赶海捡到大毛蛤")

    def test_sea_harvest_seed_candidates_use_natural_followups(self) -> None:
        agent = TopicAgent(request_interval=0)

        topics = [topic for _, topic in agent._build_seed_candidates("大庆赶海，随着潮水发现和拳头一样的大毛蛤，还正在张着大嘴")]

        self.assertEqual(
            topics,
            [
                "跟着下一波潮水继续找大毛蛤的一次赶海记录",
                "把这次赶海碰到的大毛蛤单独拍一条近景收获记录",
                "同一片滩涂换个潮位再去一次，看看还能碰到哪些海货",
            ],
        )

    def test_appearance_seed_candidates_stay_in_lane(self) -> None:
        agent = TopicAgent(request_interval=0)

        topics = [topic for _, topic in agent._build_seed_candidates("颜值卡点展示")]

        self.assertEqual(
            topics,
            [
                "近景颜值特写卡点",
                "场景切换颜值卡点",
                "前后反差变装卡点",
            ],
        )
        for topic in topics:
            self.assertNotIn("建议", topic)
            self.assertNotIn("换成", topic)
            self.assertNotIn("会更清楚", topic)

    def test_relationship_seed_candidates_match_intimacy_lane(self) -> None:
        agent = TopicAgent(request_interval=0)

        topics = [topic for _, topic in agent._build_seed_candidates("夫妻坦白局 私密话题 两性差异")]

        self.assertEqual(
            topics,
            [
                "夫妻坦白局：那些平时不会主动聊、但每对伴侣迟早都会碰到的尴尬瞬间",
                "同一个亲密话题，男生和女生的真实感受到底差在哪",
                "关系里最消耗亲密感的，往往不是大矛盾，而是这些说不出口的小事",
            ],
        )
        for topic in topics:
            self.assertNotIn("结果先出", topic)
            self.assertNotIn("连续更新", topic)
            self.assertNotIn("具体片段", topic)

    def test_normalize_analysis_topics_filters_mechanical_template_tail(self) -> None:
        result = normalize_analysis_topics(
            {
                "ideas": [
                    {"topic": "把颜值卡点展示换成结果先出的新版本"},
                    {"topic": "颜值卡点展示拆成三条连续更新会更清楚"},
                    {"topic": "近景颜值特写卡点"},
                ]
            },
            current_title="颜值卡点展示",
        )

        self.assertEqual(result, ["近景颜值特写卡点"])

    def test_safe_metric_int_supports_bilibili_special_units(self) -> None:
        agent = TopicAgent(request_interval=0)

        self.assertEqual(agent._safe_metric_int("10 万 +"), 100000)
        self.assertEqual(agent._safe_metric_int("5.2 万"), 52000)
        self.assertEqual(agent._safe_metric_int("3.4亿"), 340000000)
        self.assertEqual(agent._safe_metric_int("1.2k"), 1200)

    def test_build_video_benchmark_queries_prioritize_specific_direction(self) -> None:
        resolved = {
            "title": "《触不可及》Una Mattina钢琴转调",
            "topic": "《触不可及》Una Mattina钢琴转调",
            "partition": "life",
            "partition_label": "生活",
            "tname": "",
            "keywords": ["钢琴", "练习", "记录"],
        }

        queries = build_video_benchmark_queries(resolved)

        self.assertTrue(any("钢琴" in query for query in queries))
        self.assertTrue(any("Una Mattina" in query for query in queries))
        self.assertFalse(any("爆款" in query or "高播放" in query or "高点赞" in query for query in queries))

    def test_build_video_benchmark_profile_expands_sea_harvest_lane_terms(self) -> None:
        resolved = {
            "title": "法国赶海遇蜘蛛蟹繁殖，徒手能抓十几只，浸油膏蟹鲜美",
            "topic": "赶海收获记录",
            "partition": "life",
            "partition_label": "生活",
            "tname": "田园美食",
            "keywords": ["法国赶海遇蜘蛛蟹繁殖", "徒手能抓十几只", "浸油膏蟹鲜美", "冬捕冬钓大作战", "搞笑", "生活记录", "法国", "海鲜"],
        }

        profile = build_video_benchmark_profile(resolved)
        queries = build_video_benchmark_queries(resolved)
        query_text = build_reference_query_text(resolved)

        self.assertIn("赶海", profile["terms"])
        self.assertIn("海鲜收获", profile["terms"])
        self.assertTrue(any("法国" in term or "海外赶海" in term for term in profile["terms"]))
        self.assertTrue(any("赶海" in query and "海鲜收获" in query for query in queries))
        self.assertTrue(any("法国" in query or "海外赶海" in query for query in queries))
        self.assertEqual(query_text.count("田园美食"), 1)

    def test_build_video_benchmark_profile_extracts_narrative_conflict_terms(self) -> None:
        resolved = {
            "title": "我持续蹲守了两年半，终于抓到了当初骂我几个月的网络喷子",
            "topic": "我持续蹲守了两年半，终于抓到了当初骂我几个月的网络喷子",
            "partition": "life",
            "partition_label": "生活",
            "tname": "",
            "keywords": ["我持续蹲守了两年半", "终于抓到了当初骂我几个月的网络喷子", "发现Call of Silence", "开启2026生活记录", "社会", "生活记录", "潜伏", "人性"],
        }

        profile = build_video_benchmark_profile(resolved)
        queries = build_video_benchmark_queries(resolved)
        query_text = build_reference_query_text(resolved)

        self.assertTrue(any(term in profile["terms"] for term in ["网络喷子", "喷子"]))
        self.assertIn("人性", profile["terms"])
        self.assertTrue(any("网络喷子" in query for query in queries))
        self.assertNotIn("生活 ", query_text)

    def test_fetch_hot_peer_videos_only_keeps_same_direction_recent_hits(self) -> None:
        agent = TopicAgent(request_interval=0)
        now_ts = int(time.time())
        candidates = [
            {
                "bvid": "BV1abcde1234",
                "title": "Una Mattina钢琴演奏版",
                "author": "钢琴UP",
                "view": 120000,
                "like": 5200,
                "favorite": 800,
                "reply": 230,
                "duration": 98,
                "pubdate": now_ts,
                "query": "钢琴 Una Mattina",
            },
            {
                "bvid": "BV2abcde1234",
                "title": "第一次演电影，有点紧张～",
                "author": "路人甲",
                "view": 320000,
                "like": 16000,
                "favorite": 2200,
                "reply": 560,
                "duration": 45,
                "pubdate": now_ts,
                "query": "钢琴 Una Mattina",
            },
        ]
        details = {
            "BV1abcde1234": {
                "bvid": "BV1abcde1234",
                "title": "Una Mattina钢琴演奏版",
                "owner": {"name": "钢琴UP", "mid": 1},
                "stat": {"view": 120000, "like": 5200, "favorite": 800, "reply": 230, "coin": 500, "share": 120},
                "duration": 98,
                "pubdate": now_ts,
                "tname": "音乐",
                "desc": "钢琴演奏",
            },
            "BV2abcde1234": {
                "bvid": "BV2abcde1234",
                "title": "第一次演电影，有点紧张～",
                "owner": {"name": "路人甲", "mid": 2},
                "stat": {"view": 320000, "like": 16000, "favorite": 2200, "reply": 560, "coin": 1200, "share": 320},
                "duration": 45,
                "pubdate": now_ts,
                "tname": "娱乐",
                "desc": "电影日常",
            },
        }

        with patch.object(agent, "_fetch_hot_peer_candidates", return_value=candidates):
            with patch.object(agent, "_fetch_hot_peer_detail", side_effect=lambda bvid: details.get(bvid)):
                videos = agent.fetch_hot_peer_videos(
                    queries=["钢琴 Una Mattina"],
                    exclude_bvid="",
                    limit=3,
                    recent_days=30,
                    min_view=50000,
                    min_like=1000,
                )

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].bvid, "BV1abcde1234")

    def test_build_hot_peer_market_snapshot_filters_unrelated_samples(self) -> None:
        resolved = {
            "bv_id": "BV19t46ecECy",
            "title": "《触不可及》Una Mattina钢琴转调",
            "topic": "《触不可及》Una Mattina钢琴转调",
            "partition": "life",
            "partition_label": "生活",
            "tname": "",
            "keywords": ["钢琴", "练习", "记录"],
        }
        fake_samples = [
            {
                "bvid": "BV1abcde1234",
                "title": "Una Mattina钢琴演奏版",
                "author": "钢琴UP",
                "url": "https://www.bilibili.com/video/BV1abcde1234",
                "view": 120000,
                "like": 5200,
                "favorite": 800,
                "reply": 230,
                "duration": 98,
                "like_rate": 0.043,
                "source": "同方向爆款:钢琴 Una Mattina",
                "extra": {"estimated": False},
            },
            {
                "bvid": "BV2abcde1234",
                "title": "第一次演电影，有点紧张～",
                "author": "路人甲",
                "url": "https://www.bilibili.com/video/BV2abcde1234",
                "view": 320000,
                "like": 16000,
                "favorite": 2200,
                "reply": 560,
                "duration": 45,
                "like_rate": 0.05,
                "source": "同方向爆款:生活",
                "extra": {"estimated": False},
            },
        ]

        with patch("web.app.RAW_TOPIC_AGENT.fetch_hot_peer_videos", return_value=fake_samples):
            snapshot = build_hot_peer_market_snapshot(resolved)

        self.assertEqual(snapshot["partition"], "ent")
        self.assertEqual(snapshot["partition_label"], "娱乐")
        self.assertEqual(len(snapshot["peer_samples"]), 1)
        self.assertEqual(snapshot["peer_samples"][0]["bvid"], "BV1abcde1234")

    def test_build_llm_video_payload_uses_benchmark_direction_context(self) -> None:
        resolved = {
            "bv_id": "BV19t46ecECy",
            "title": "《触不可及》Una Mattina钢琴转调",
            "topic": "《触不可及》Una Mattina钢琴转调",
            "partition": "life",
            "partition_label": "生活",
            "tname": "",
            "keywords": ["钢琴", "练习", "记录"],
            "style": "干货",
            "up_name": "Xuannn_er",
            "mid": 123,
            "up_ids": [123],
            "tid": 21,
            "duration": 39,
            "stats": {"view": 130, "like": 3},
        }

        payload = build_llm_video_payload_from_resolved(resolved, "https://www.bilibili.com/video/BV19t46ecECy")

        self.assertEqual(payload["retrieval_partition"], "ent")
        self.assertEqual(payload["benchmark_lane_label"], "音乐")
        self.assertTrue(any("钢琴" in item for item in payload["benchmark_terms"]))
        self.assertTrue(payload["benchmark_queries"])

    def test_creator_reason_and_cue_drop_old_series_language(self) -> None:
        topic = "夫妻坦白局：那些平时不会主动聊、但每对伴侣迟早都会碰到的尴尬瞬间"

        cue = select_creator_topic_cue(topic)
        reason = build_creator_reason(topic, "life", 12, [], "情绪共鸣", 0)

        self.assertEqual(cue, "关系视角")
        self.assertNotIn("连续更新", reason)
        self.assertNotIn("结果感", reason)
        self.assertIn("关系边界", reason)


if __name__ == "__main__":
    unittest.main()
