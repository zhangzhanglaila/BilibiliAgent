from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import fetch_text, fetch_video_info, fetch_video_preview_info


class FakeHeaders(dict):
    def get_content_charset(self) -> str | None:
        content_type = str(self.get("Content-Type") or "")
        marker = "charset="
        if marker not in content_type.lower():
            return None
        return content_type.split("=", 1)[1].split(";", 1)[0].strip()


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = FakeHeaders(headers or {})

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class VideoResolveTests(unittest.TestCase):
    def test_fetch_text_decompresses_gzip_html_response(self) -> None:
        html = "<html><head><title>demo</title></head><body>ok</body></html>"
        response = FakeResponse(
            gzip.compress(html.encode("utf-8")),
            headers={
                "Content-Encoding": "gzip",
                "Content-Type": "text/html; charset=utf-8",
            },
        )

        with patch("web.app.urlopen", return_value=response):
            content = fetch_text("https://example.com/video")

        self.assertEqual(content, html)

    def test_fetch_video_info_keeps_public_api_payload_when_enrichment_fails(self) -> None:
        base_info = {
            "bvid": "BV1dcX5BYESE",
            "title": "demo title",
            "owner": {"mid": 1, "name": "demo up"},
            "stat": {"view": 1, "like": 0, "coin": 0, "favorite": 0, "reply": 0, "share": 0},
            "duration": 1,
            "tname": "",
        }

        with patch("web.app.fetch_video_info_via_public_api", return_value=base_info):
            with patch("web.app.enrich_video_info_with_html_hints", side_effect=TimeoutError("timeout")):
                result = fetch_video_info("https://www.bilibili.com/video/BV1dcX5BYESE", "BV1dcX5BYESE")

        self.assertEqual(result, base_info)

    def test_fetch_video_preview_info_prefers_html_fast_path(self) -> None:
        html_info = {
            "bvid": "BV1dcX5BYESE",
            "title": "html title",
            "owner": {"mid": 1, "name": "html up"},
            "stat": {"view": 1, "like": 0, "coin": 0, "favorite": 0, "reply": 0, "share": 0},
            "duration": 1,
        }

        with patch("web.app.fetch_video_info_via_html", return_value=html_info):
            with patch("web.app.fetch_video_info_via_public_api", side_effect=AssertionError("should not call public api")):
                result = fetch_video_preview_info("https://www.bilibili.com/video/BV1dcX5BYESE", "BV1dcX5BYESE")

        self.assertEqual(result, html_info)

    def test_fetch_video_preview_info_falls_back_to_public_api(self) -> None:
        base_info = {
            "bvid": "BV1dcX5BYESE",
            "title": "api title",
            "owner": {"mid": 1, "name": "api up"},
            "stat": {"view": 1, "like": 0, "coin": 0, "favorite": 0, "reply": 0, "share": 0},
            "duration": 1,
        }

        with patch("web.app.fetch_video_info_via_html", side_effect=ValueError("html failed")):
            with patch("web.app.fetch_video_info_via_public_api", return_value=base_info):
                result = fetch_video_preview_info("https://www.bilibili.com/video/BV1dcX5BYESE", "BV1dcX5BYESE")

        self.assertEqual(result, base_info)


if __name__ == "__main__":
    unittest.main()
