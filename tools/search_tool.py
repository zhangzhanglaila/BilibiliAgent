"""Realtime web search wrapper with Tavily primary and SerpAPI fallback."""
from __future__ import annotations

from typing import Any, Dict, List

import requests

from config import CONFIG

try:
    from serpapi import GoogleSearch
except Exception:  # pragma: no cover
    GoogleSearch = None


class SearchTool:
    def __init__(
        self,
        api_key: str | None = None,
        tavily_api_key: str | None = None,
        timeout_seconds: int = 12,
    ) -> None:
        self.api_key = (api_key if api_key is not None else CONFIG.serpapi_api_key).strip()
        self.tavily_api_key = (tavily_api_key if tavily_api_key is not None else CONFIG.tavily_api_key).strip()
        self.timeout_seconds = max(3, int(timeout_seconds or 12))

    def _search_with_tavily(self, query: str, limit: int) -> Dict[str, Any]:
        if not self.tavily_api_key:
            return {"query": query, "results": [], "warning": "missing_tavily_api_key"}

        payload = {
            "api_key": self.tavily_api_key,
            "query": query,
            "topic": "general",
            "search_depth": "advanced",
            "max_results": max(1, min(int(limit or 5), 10)),
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return {"query": query, "results": [], "error": str(exc), "provider": "tavily"}

        results: List[Dict[str, str]] = []
        for item in data.get("results", [])[: payload["max_results"]]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "link": str(item.get("url") or "").strip(),
                    "snippet": str(item.get("content") or "").strip(),
                }
            )
        return {"query": query, "results": results, "provider": "tavily"}

    def _search_with_serpapi(self, query: str, limit: int) -> Dict[str, Any]:
        if not self.api_key:
            return {"query": query, "results": [], "warning": "missing_serpapi_api_key"}
        if GoogleSearch is None:
            return {"query": query, "results": [], "warning": "serpapi_dependency_unavailable"}

        params = {
            "engine": "google",
            "q": query,
            "api_key": self.api_key,
            "num": max(1, min(int(limit or 5), 10)),
            "hl": "zh-cn",
            "gl": "cn",
        }
        try:
            payload = GoogleSearch(params).get_dict()
        except Exception as exc:
            return {"query": query, "results": [], "error": str(exc), "provider": "serpapi"}

        results: List[Dict[str, str]] = []
        for item in payload.get("organic_results", [])[: params["num"]]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "link": str(item.get("link") or "").strip(),
                    "snippet": str(item.get("snippet") or item.get("snippet_highlighted_words") or "").strip(),
                }
            )
        return {"query": query, "results": results, "provider": "serpapi"}

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        clean_query = (query or "").strip()
        if not clean_query:
            return {"query": clean_query, "results": [], "warning": "empty_query"}

        attempts = []
        if self.tavily_api_key:
            attempts.append(self._search_with_tavily)
        if self.api_key:
            attempts.append(self._search_with_serpapi)
        if not attempts:
            return {"query": clean_query, "results": [], "warning": "missing_web_search_api_key"}

        errors: List[Dict[str, str]] = []
        for search_impl in attempts:
            result = search_impl(clean_query, limit)
            if result.get("results"):
                return result
            error_text = str(result.get("error") or "").strip()
            warning_text = str(result.get("warning") or "").strip()
            provider = str(result.get("provider") or "").strip()
            if error_text or warning_text:
                errors.append(
                    {
                        "provider": provider,
                        "error": error_text,
                        "warning": warning_text,
                    }
                )

        payload = {"query": clean_query, "results": [], "warning": "no_search_results"}
        if errors:
            payload["errors"] = errors
        return payload
