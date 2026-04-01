"""Realtime web search wrapper based on SerpAPI when configured."""
from __future__ import annotations

from typing import Any, Dict, List

from config import CONFIG

try:
    from serpapi import GoogleSearch
except Exception:  # pragma: no cover
    GoogleSearch = None


class SearchTool:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else CONFIG.serpapi_api_key).strip()

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"query": clean_query, "results": [], "warning": "empty_query"}
        if not self.api_key:
            return {"query": clean_query, "results": [], "warning": "missing_serpapi_api_key"}
        if GoogleSearch is None:
            return {"query": clean_query, "results": [], "warning": "serpapi_dependency_unavailable"}

        params = {
            "engine": "google",
            "q": clean_query,
            "api_key": self.api_key,
            "num": max(1, min(int(limit or 5), 10)),
            "hl": "zh-cn",
            "gl": "cn",
        }
        try:
            payload = GoogleSearch(params).get_dict()
        except Exception as exc:
            return {"query": clean_query, "results": [], "error": str(exc)}

        results: List[Dict[str, str]] = []
        for item in payload.get("organic_results", [])[: params["num"]]:
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "link": str(item.get("link") or "").strip(),
                    "snippet": str(item.get("snippet") or item.get("snippet_highlighted_words") or "").strip(),
                }
            )
        return {"query": clean_query, "results": results}
