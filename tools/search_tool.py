"""Realtime web search wrapper with Tavily primary and SerpAPI fallback.
实时网络搜索工具，以Tavily为主搜索引擎，SerpAPI为备选。
当Tavily不可用或失败时，自动回退到SerpAPI进行搜索。
"""
from __future__ import annotations

from typing import Any, Dict, List

import requests

from config import CONFIG

# 尝试导入SerpAPI的GoogleSearch类
# 如果导入失败，GoogleSearch将被设为None
try:
    from serpapi import GoogleSearch
except Exception:  # pragma: no cover
    GoogleSearch = None


class SearchTool:
    """网络搜索工具类。

    支持两种搜索引擎：
    1. Tavily（优先）：现代化的AI搜索API，专注于提供高质量的搜索结果
    2. SerpAPI（备选）：基于Google搜索的API，当Tavily不可用时使用

    搜索流程：
    - 优先尝试Tavily搜索
    - 如果Tavily失败或无可用API key，回退到SerpAPI
    - 如果都没有，返回缺少API key的警告
    """
    def __init__(
        self,
        api_key: str | None = None,
        tavily_api_key: str | None = None,
        timeout_seconds: int = 12,
    ) -> None:
        """初始化搜索工具。

        Args:
            api_key: SerpAPI的API密钥。如果为None，则从CONFIG中获取。
            tavily_api_key: Tavily的API密钥。如果为None，则从CONFIG中获取。
            timeout_seconds: 请求超时时间（秒），最小值为3秒，默认12秒。
        """
        # 获取SerpAPI密钥，优先使用传入值，否则从配置中读取
        self.api_key = (api_key if api_key is not None else CONFIG.serpapi_api_key).strip()
        # 获取Tavily API密钥，优先使用传入值，否则从配置中读取
        self.tavily_api_key = (tavily_api_key if tavily_api_key is not None else CONFIG.tavily_api_key).strip()
        # 设置超时时间，确保至少为3秒
        self.timeout_seconds = max(3, int(timeout_seconds or 12))

    def _search_with_tavily(self, query: str, limit: int) -> Dict[str, Any]:
        """使用Tavily API执行搜索（私有方法）。

        Args:
            query: 搜索查询字符串。
            limit: 返回结果的数量限制（1-10之间）。

        Returns:
            包含搜索结果的字典：
                - query: 原始查询字符串
                - results: 搜索结果列表，每项包含title、link、snippet
                - provider: 提供商标识（"tavily"）
                - warning/error: 如果发生问题，包含相应的警告或错误信息
        """
        # 检查是否有有效的Tavily API密钥
        if not self.tavily_api_key:
            return {"query": query, "results": [], "warning": "missing_tavily_api_key"}

        # 构建Tavily API请求载荷
        payload = {
            "api_key": self.tavily_api_key,
            "query": query,
            "topic": "general",  # 使用通用主题搜索
            "search_depth": "advanced",  # 使用高级搜索深度
            "max_results": max(1, min(int(limit or 5), 10)),  # 限制结果数量在1-10之间
            "include_answer": False,  # 不请求AI生成的答案
            "include_raw_content": False,  # 不包含原始内容
        }
        try:
            # 发送POST请求到Tavily搜索API
            response = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=self.timeout_seconds,
            )
            # 检查HTTP响应状态
            response.raise_for_status()
            # 解析JSON响应
            data = response.json()
        except Exception as exc:
            # 请求失败，返回错误信息
            return {"query": query, "results": [], "error": str(exc), "provider": "tavily"}

        # 解析搜索结果
        results: List[Dict[str, str]] = []
        # 遍历结果列表，提取需要的信息
        for item in data.get("results", [])[: payload["max_results"]]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),  # 提取标题
                    "link": str(item.get("url") or "").strip(),  # 提取链接
                    "snippet": str(item.get("content") or "").strip(),  # 提取内容摘要
                }
            )
        return {"query": query, "results": results, "provider": "tavily"}

    def _search_with_serpapi(self, query: str, limit: int) -> Dict[str, Any]:
        """使用SerpAPI（Google搜索）执行搜索（私有方法）。

        当Tavily不可用时的备选搜索引擎。

        Args:
            query: 搜索查询字符串。
            limit: 返回结果的数量限制（1-10之间）。

        Returns:
            包含搜索结果的字典：
                - query: 原始查询字符串
                - results: 搜索结果列表，每项包含title、link、snippet
                - provider: 提供商标识（"serpapi"）
                - warning/error: 如果发生问题，包含相应的警告或错误信息
        """
        # 检查是否有有效的SerpAPI密钥
        if not self.api_key:
            return {"query": query, "results": [], "warning": "missing_serpapi_api_key"}
        # 检查SerpAPI依赖是否可用
        if GoogleSearch is None:
            return {"query": query, "results": [], "warning": "serpapi_dependency_unavailable"}

        # 构建SerpAPI请求参数
        params = {
            "engine": "google",  # 使用Google搜索引擎
            "q": query,  # 搜索查询
            "api_key": self.api_key,  # API密钥
            "num": max(1, min(int(limit or 5), 10)),  # 结果数量限制在1-10之间
            "hl": "zh-cn",  # 界面语言设为简体中文
            "gl": "cn",  # 搜索区域设为中国
        }
        try:
            # 执行Google搜索
            payload = GoogleSearch(params).get_dict()
        except Exception as exc:
            # 搜索失败，返回错误信息
            return {"query": query, "results": [], "error": str(exc), "provider": "serpapi"}

        # 解析搜索结果
        results: List[Dict[str, str]] = []
        # 遍历有机搜索结果（自然搜索结果，非广告）
        for item in payload.get("organic_results", [])[: params["num"]]:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "title": str(item.get("title") or "").strip(),  # 提取标题
                    "link": str(item.get("link") or "").strip(),  # 提取链接
                    # 提取摘要，优先使用snippet，其次使用snippet_highlighted_words
                    "snippet": str(item.get("snippet") or item.get("snippet_highlighted_words") or "").strip(),
                }
            )
        return {"query": query, "results": results, "provider": "serpapi"}

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """执行网络搜索的主入口方法。

        搜索策略：
        1. 首先清理查询字符串，空查询直接返回警告
        2. 根据可用的API密钥构建搜索方法列表
        3. 依次尝试每个搜索方法，直到成功获取结果
        4. 如果所有方法都失败，返回错误信息汇总

        Args:
            query: 搜索查询字符串。
            limit: 返回结果的数量限制，默认为5。

        Returns:
            包含搜索结果的字典：
                - query: 原始查询字符串
                - results: 搜索结果列表
                - warning: 警告信息（如无结果、缺少API密钥等）
                - errors: 错误信息列表（当所有搜索方法都失败时）
        """
        # 清理查询字符串
        clean_query = (query or "").strip()
        # 空查询返回警告
        if not clean_query:
            return {"query": clean_query, "results": [], "warning": "empty_query"}

        # 构建可用的搜索方法列表
        attempts = []
        if self.tavily_api_key:
            attempts.append(self._search_with_tavily)
        if self.api_key:
            attempts.append(self._search_with_serpapi)
        # 如果没有任何可用的API密钥，返回警告
        if not attempts:
            return {"query": clean_query, "results": [], "warning": "missing_web_search_api_key"}

        # 收集所有搜索方法的错误信息
        errors: List[Dict[str, str]] = []
        # 依次尝试每个搜索方法
        for search_impl in attempts:
            result = search_impl(clean_query, limit)
            # 如果该方法成功获取结果，立即返回
            if result.get("results"):
                return result
            # 如果失败，收集错误信息
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

        # 所有搜索方法都失败，返回最终结果
        payload = {"query": clean_query, "results": [], "warning": "no_search_results"}
        # 如果有收集到错误信息，一并返回
        if errors:
            payload["errors"] = errors
        return payload
