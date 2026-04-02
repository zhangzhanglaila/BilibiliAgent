"""Strict LLM agent runtime for key-enabled mode."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from knowledge_base import retrieve as kb_retrieve
from llm_client import LLMClient
from memory.long_term_memory import LongTermMemory
from observability import end_trace, trace_block, traceable


@dataclass
class AgentTool:
    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


class RetrievalTool(AgentTool):
    def __init__(self, description: str | None = None) -> None:
        super().__init__(
            name="retrieval",
            description=description
            or "从本地知识库检索爆款案例、选题经验、行业信息与历史沉淀。输入: {query, limit, metadata_filter}",
            handler=lambda payload: kb_retrieve(
                str(payload.get("query") or ""),
                limit=max(1, min(int(payload.get("limit") or 4), 8)),
                metadata_filter=payload.get("metadata_filter") if isinstance(payload.get("metadata_filter"), dict) else None,
            ),
        )


class LLMWorkspaceAgent:
    # 初始化受控 Agent 运行时，注册工具并设置最大推理步数。
    def __init__(
        self,
        tools: Sequence[AgentTool],
        max_steps: int = 4,
        llm_client: LLMClient | None = None,
        memory_store: LongTermMemory | None = None,
    ) -> None:
        self.llm = llm_client or LLMClient()
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.memory_store = memory_store or LongTermMemory()

    # 把允许使用的工具列表渲染成提示词里的工具说明块。
    def _tool_block(self, allowed_tools: Sequence[str]) -> str:
        lines = []
        for name in allowed_tools:
            tool = self.tools[name]
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    # 把历史工具调用记录整理成提示词，供模型基于已有观察继续决策。
    def _scratchpad_block(self, scratchpad: List[Dict[str, Any]]) -> str:
        if not scratchpad:
            return "暂无工具调用记录。"

        blocks = []
        for index, item in enumerate(scratchpad, start=1):
            observation = json.dumps(item.get("observation", {}), ensure_ascii=False)
            if len(observation) > 3500:
                observation = observation[:3500] + "...(truncated)"
            blocks.append(
                f"第 {index} 步\n"
                f"action: {item.get('action')}\n"
                f"action_input: {json.dumps(item.get('action_input', {}), ensure_ascii=False)}\n"
                f"observation: {observation}"
            )
        return "\n\n".join(blocks)

    # 校验最终结果里是否包含调用方要求的关键字段。
    def _validate_final(self, final: Dict[str, Any], required_final_keys: Sequence[str]) -> List[str]:
        missing = []
        for key in required_final_keys:
            if key not in final:
                missing.append(key)
        return missing

    def _payload_text(self, payload: Any) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload)

    def _build_history_key(self, task_name: str, user_payload: Dict[str, Any]) -> str:
        for key in ["user_id", "memory_user_id", "session_id"]:
            value = str(user_payload.get(key) or "").strip()
            if value:
                return value
        creator_context = user_payload.get("creator_context")
        if isinstance(creator_context, dict):
            focus = "|".join(
                str(creator_context.get(key) or "").strip()
                for key in ["field", "direction", "partition"]
                if str(creator_context.get(key) or "").strip()
            )
            if focus:
                return f"creator:{focus}"
        return f"default:{task_name}"

    def _build_query_text(self, task_name: str, task_goal: str, user_payload: Dict[str, Any]) -> str:
        parts = [task_name, task_goal]
        for key, value in user_payload.items():
            if isinstance(value, dict):
                parts.append(self._payload_text(value))
            elif isinstance(value, list):
                parts.append(self._payload_text(value[:3]))
            else:
                text = str(value or "").strip()
                if text:
                    parts.append(text)
        return " ".join(part for part in parts if part)[:1000]

    def _history_block(self, user_id: str, query_text: str) -> str:
        if not self.memory_store:
            return "暂无历史上下文。"
        try:
            history = self.memory_store.retrieve_user_history(user_id, query_text, limit=4).get("history", [])
        except Exception:
            history = []
        if not history:
            return "暂无历史上下文。"

        lines = []
        for index, item in enumerate(history, start=1):
            text = str(item.get("text") or "")
            if len(text) > 600:
                text = text[:600] + "...(truncated)"
            lines.append(f"{index}. {text}")
        return "\n".join(lines)

    def _auto_retrieve(
        self,
        *,
        allowed_tools: Sequence[str],
        query_text: str,
        scratchpad: List[Dict[str, Any]],
        used_tools: List[str],
    ) -> None:
        if "retrieval" not in allowed_tools or "retrieval" not in self.tools:
            return
        with trace_block(
            "agent_tool.retrieval",
            run_type="retriever",
            inputs={"query": query_text, "limit": 4},
            tags=["agent_tool", "retrieval", "rag"],
        ) as run:
            try:
                observation = self.tools["retrieval"].handler({"query": query_text, "limit": 4})
            except Exception as exc:
                raise RuntimeError(f"知识库检索失败，无法继续执行当前任务：{exc}") from exc
            end_trace(
                run,
                {
                    "match_count": len((observation or {}).get("matches") or []) if isinstance(observation, dict) else 0,
                },
            )
        if isinstance(observation, dict) and str(observation.get("error") or "").strip():
            raise RuntimeError(f"知识库检索失败，无法继续执行当前任务：{observation['error']}")
        used_tools.append("retrieval")
        scratchpad.append(
            {
                "action": "retrieval",
                "action_input": {"query": query_text, "limit": 4},
                "observation": observation,
            }
        )

    def _score_candidate(self, candidate: Dict[str, Any], required_final_keys: Sequence[str]) -> float:
        missing_keys = self._validate_final(candidate, required_final_keys)
        score = 100.0 - len(missing_keys) * 25
        payload_text = self._payload_text(candidate)
        if "error" in payload_text.lower():
            score -= 15
        if len(payload_text) < 120:
            score -= 10
        return max(score, 0.0)

    def _self_score_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        response_contract: str,
        required_final_keys: Sequence[str],
    ) -> float:
        heuristic_score = self._score_candidate(candidate, required_final_keys)
        try:
            review = self.llm.invoke_json_required(
                "你是结果评分器，只返回 JSON。",
                (
                    "请给下面这个候选结果打分，衡量它是否完整、贴合任务、是否像真实可用输出。\n"
                    "只返回 JSON：{score:number, reason:string}\n\n"
                    f"响应契约：{response_contract}\n"
                    f"候选结果：{self._payload_text(candidate)}"
                ),
            )
            llm_score = float(review.get("score") or 0)
            if llm_score <= 0:
                return heuristic_score
            return max(0.0, min(100.0, (heuristic_score + llm_score) / 2))
        except Exception:
            return heuristic_score

    def generate_multiple(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_contract: str,
        required_final_keys: Sequence[str] | None = None,
        candidate_count: int = 3,
    ) -> Dict[str, Any]:
        required_final_keys = list(required_final_keys or [])
        candidates: List[Dict[str, Any]] = []
        for _ in range(max(1, candidate_count)):
            candidate = self.llm.invoke_json_required(
                system_prompt,
                f"{user_prompt}\n\n最终响应契约：\n{response_contract}",
            )
            if isinstance(candidate, dict):
                candidates.append(candidate)

        if not candidates:
            raise RuntimeError("未生成可用候选结果")

        scored = sorted(
            (
                {
                    "candidate": candidate,
                    "score": self._self_score_candidate(
                        candidate=candidate,
                        response_contract=response_contract,
                        required_final_keys=required_final_keys,
                    ),
                }
                for candidate in candidates
            ),
            key=lambda item: item["score"],
            reverse=True,
        )
        best = dict(scored[0]["candidate"])
        best.setdefault("candidate_scores", [item["score"] for item in scored])
        return best

    def _reflect_final(
        self,
        *,
        task_name: str,
        task_goal: str,
        user_payload: Dict[str, Any],
        response_contract: str,
        final: Dict[str, Any],
        scratchpad: List[Dict[str, Any]],
        required_final_keys: Sequence[str],
    ) -> Dict[str, Any]:
        review_prompt = (
            "你是结果质检器，需要判断下面这个 JSON 最终结果是否满足任务要求。\n"
            "如果满足，pass 返回 true。\n"
            "如果不满足，pass 返回 false，并直接给出 rewritten_final。\n"
            "只返回 JSON：{pass:boolean, issues:string[], rewritten_final:object|null}\n\n"
            f"任务名称：{task_name}\n"
            f"任务目标：{task_goal}\n"
            f"用户输入：{self._payload_text(user_payload)}\n"
            f"响应契约：{response_contract}\n"
            f"工具观察：{self._scratchpad_block(scratchpad)}\n"
            f"候选最终结果：{self._payload_text(final)}"
        )
        try:
            review = self.llm.invoke_json_required(
                "你是一个严格的 B 站创作结果审查与重写助手，只返回 JSON。",
                review_prompt,
            )
        except Exception:
            return final

        if bool(review.get("pass")):
            return final

        rewritten = review.get("rewritten_final")
        if isinstance(rewritten, dict) and not self._validate_final(rewritten, required_final_keys):
            rewritten.setdefault("reflection_issues", review.get("issues", []))
            return rewritten
        return final

    def _save_memory(self, user_id: str, task_name: str, user_payload: Dict[str, Any], final: Dict[str, Any]) -> None:
        if not self.memory_store:
            return
        record = {
            "task_name": task_name,
            "user_payload": user_payload,
            "final": final,
        }
        try:
            self.memory_store.save_user_data(user_id, record, memory_type=task_name)
        except Exception:
            return

    def _save_memory_async(self, user_id: str, task_name: str, user_payload: Dict[str, Any], final: Dict[str, Any]) -> None:
        if not self.memory_store:
            return

        def worker() -> None:
            self._save_memory(user_id, task_name, user_payload, final)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    # 运行受限的工具调用循环，直到模型给出合法最终结果或步数耗尽。
    @traceable(run_type="chain", name="llm_workspace_agent.run_structured", tags=["llm_agent", "rag"])
    def run_structured(
        self,
        *,
        task_name: str,
        task_goal: str,
        user_payload: Dict[str, Any],
        response_contract: str,
        allowed_tools: Sequence[str],
        required_tools: Sequence[str] | None = None,
        required_final_keys: Sequence[str] | None = None,
        max_steps: int | None = None,
        load_history: bool = True,
        save_memory: bool = True,
        enable_reflection: bool = True,
    ) -> Dict[str, Any]:
        self.llm.require_available()

        missing_tools = [name for name in allowed_tools if name not in self.tools]
        if missing_tools:
            raise ValueError(f"未注册的工具: {', '.join(missing_tools)}")

        required_tools = list(required_tools or [])
        required_final_keys = list(required_final_keys or [])
        scratchpad: List[Dict[str, Any]] = []
        used_tools: List[str] = []
        limit = max_steps or self.max_steps
        user_id = self._build_history_key(task_name, user_payload)
        query_text = self._build_query_text(task_name, task_goal, user_payload)
        history_block = self._history_block(user_id, query_text) if load_history else "当前任务已跳过历史上下文检索。"
        self._auto_retrieve(allowed_tools=allowed_tools, query_text=query_text, scratchpad=scratchpad, used_tools=used_tools)

        system_prompt = (
            "你是 B 站创作工作台的 LLM Agent 中枢。\n"
            "当前处于严格 LLM 模式：所有分析、判断、决策、生成都必须基于用户输入、历史上下文和工具返回信息实时完成。\n"
            "不要套用固定阈值、预设模板、硬编码结论，也不要把任务退回规则引擎。\n"
            "如需历史经验、案例、文案沉淀，优先使用 retrieval。\n"
            "如需实时信息、热点、平台活动、竞品情况，可以使用 web_search。\n"
            "如需执行代码、做数据处理或可视化，可以使用 code_interpreter。\n"
            "你可以多步调用工具；当信息足够时，再输出最终 JSON。"
        )

        # 整个 Agent 只允许在“调工具”或“给最终结果”之间有限次循环，方便排查问题，
        # 也避免模型在开放式推理里越跑越偏。
        for _ in range(limit):
            user_prompt = (
                f"任务名称：{task_name}\n"
                f"任务目标：{task_goal}\n"
                f"用户输入：{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                f"长期记忆：\n{history_block}\n\n"
                f"可用工具：\n{self._tool_block(allowed_tools)}\n\n"
                f"必须至少使用的工具：{json.dumps(required_tools, ensure_ascii=False)}\n"
                f"已经使用的工具：{json.dumps(used_tools, ensure_ascii=False)}\n\n"
                f"历史观察：\n{self._scratchpad_block(scratchpad)}\n\n"
                "你必须只返回 JSON 对象，格式如下：\n"
                "{\n"
                '  "action": "工具名 或 final",\n'
                '  "action_input": {},\n'
                '  "final": null 或 最终结果对象\n'
                "}\n\n"
                "规则：\n"
                "1. 如果信息还不够，action 必须是某个工具名，final 必须为 null。\n"
                "2. 如果 action=final，final 必须完整满足下面的响应契约。\n"
                "3. 不要输出 markdown，不要输出解释，不要输出多余字段。\n\n"
                f"最终响应契约：\n{response_contract}"
            )
            decision = self.llm.invoke_json_required(system_prompt, user_prompt)
            action = str(decision.get("action", "")).strip()
            action_input = decision.get("action_input") if isinstance(decision.get("action_input"), dict) else {}

            if action == "final":
                final = decision.get("final")
                if not isinstance(final, dict):
                    scratchpad.append(
                        {
                            "action": "validation_error",
                            "action_input": {},
                            "observation": {"error": "final 必须是 JSON 对象"},
                        }
                    )
                    continue

                missing_required_tools = [name for name in required_tools if name not in used_tools]
                if missing_required_tools:
                    scratchpad.append(
                        {
                            "action": "validation_error",
                            "action_input": {},
                            "observation": {"error": f"仍需先调用工具: {', '.join(missing_required_tools)}"},
                        }
                    )
                    continue

                missing_keys = self._validate_final(final, required_final_keys)
                if missing_keys:
                    scratchpad.append(
                        {
                            "action": "validation_error",
                            "action_input": {},
                            "observation": {"error": f"最终结果缺少字段: {', '.join(missing_keys)}"},
                        }
                    )
                    continue

                if enable_reflection:
                    final = self._reflect_final(
                        task_name=task_name,
                        task_goal=task_goal,
                        user_payload=user_payload,
                        response_contract=response_contract,
                        final=dict(final),
                        scratchpad=scratchpad,
                        required_final_keys=required_final_keys,
                    )
                final.setdefault("agent_trace", used_tools)
                final.setdefault("tool_observations", scratchpad)
                final.setdefault("runtime_mode", "llm_agent")
                if save_memory:
                    self._save_memory_async(user_id, task_name, user_payload, final)
                return final

            if action not in allowed_tools:
                scratchpad.append(
                    {
                        "action": "validation_error",
                        "action_input": {},
                        "observation": {"error": f"非法工具: {action}"},
                    }
                )
                continue

            tool = self.tools[action]
            with trace_block(
                f"agent_tool.{action}",
                run_type="tool",
                inputs=action_input,
                metadata={"task_name": task_name},
                tags=["agent_tool", action],
            ) as run:
                try:
                    observation = tool.handler(action_input)
                except Exception as exc:
                    observation = {"error": str(exc)}
                end_trace(run, {"observation_preview": self._payload_text(observation)[:1000]})
            used_tools.append(action)
            scratchpad.append(
                {
                    "action": action,
                    "action_input": action_input,
                    "observation": observation,
                }
            )

        raise RuntimeError("LLM Agent 未能在限定步骤内完成任务。")
