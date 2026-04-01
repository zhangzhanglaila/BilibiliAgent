from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.topic_agent import TopicAgent
from web.app import build_creator_reason, build_resolved_payload, normalize_analysis_topics, select_creator_topic_cue


class VideoAnalysisRuleTests(unittest.TestCase):
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
