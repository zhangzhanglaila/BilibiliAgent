from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_base import Document, KnowledgeBase


class KnowledgeBaseSanitizationTests(unittest.TestCase):
    def _make_tempdir(self) -> Path:
        temp_root = ROOT / "tests" / ".tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        target = temp_root / f"kb_{uuid4().hex}"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def test_reopen_sanitizes_dirty_runtime_rows_and_normalizes_static_samples(self) -> None:
        tempdir = self._make_tempdir()
        try:
            kb = KnowledgeBase(persist_directory=str(tempdir), collection_name="bilibili_knowledge")
            kb.add_document(
                Document(
                    id="hot:BV1demo",
                    text="赶海爆款样本",
                    metadata={"source": "bilibili_hot_sync", "board_type": "全站热门榜", "bvid": "BV1demo"},
                )
            )
            kb.add_document(
                Document(
                    id="tool:BV1demo",
                    text="tool payload",
                    metadata={"source": "video_briefing", "partition": "life"},
                )
            )

            reopened = KnowledgeBase(persist_directory=str(tempdir), collection_name="bilibili_knowledge")
            collection = reopened._active_collection()
            payload = collection.get(include=["metadatas"])
            metadatas = [dict(item or {}) for item in payload.get("metadatas") or []]

            self.assertTrue(metadatas)
            self.assertTrue(all(item.get("source") != "video_briefing" for item in metadatas))
            self.assertTrue(any(item.get("data_type") == "static_hot_case" for item in metadatas))
            self.assertTrue(any(item.get("original_source") == "bilibili_hot_sync" for item in metadatas))
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_init_migrates_legacy_fallback_records_into_chroma(self) -> None:
        tempdir = self._make_tempdir()
        try:
            fallback_path = tempdir / "bilibili_knowledge__fallback_store.json"
            fallback_path.write_text(
                json.dumps(
                    {
                        "collection_name": "bilibili_knowledge",
                        "items": [
                            {
                                "id": "legacy:0",
                                "document_id": "legacy:doc",
                                "text": "赶海静态样本",
                                "metadata": {
                                    "document_id": "legacy:doc",
                                    "chunk_index": 0,
                                    "source": "bilibili_hot_sync",
                                    "board_type": "全站热门榜",
                                    "bvid": "BV1legacy",
                                },
                                "embedding": [0.1, 0.2],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            kb = KnowledgeBase(persist_directory=str(tempdir), collection_name="bilibili_knowledge")
            result = kb.retrieve("赶海 静态样本", limit=2, metadata_filter={"source": "knowledge_base", "data_type": "static_hot_case"})

            self.assertTrue(result["matches"])
            self.assertEqual(result["matches"][0]["metadata"]["original_source"], "bilibili_hot_sync")
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
