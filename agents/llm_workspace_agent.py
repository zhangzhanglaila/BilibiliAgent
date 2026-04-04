"""Strict LLM agent runtime for key-enabled mode."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Sequence

from config import CONFIG
from knowledge_base import retrieve as kb_retrieve
from llm_client import LLMClient
from observability import end_trace, trace_block, traceable

if TYPE_CHECKING:
    from memory.long_term_memory import LongTermMemory


@dataclass
class AgentTool:
    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


class RetrievalTool(AgentTool):
    def __init__(self, description: str | None = None) -> None:
        super().__init__(
            name="retrieval",
            description=description or "从本地知识库检索案例、经验和结构化资料。输入: {query, limit, metadata_filter}",
            handler=lambda payload: kb_retrieve(
                str(payload.get("query") or ""),
                limit=max(1, min(int(payload.get("limit") or 4), 8)),
                metadata_filter=payload.get("metadata_filter") if isinstance(payload.get("metadata_filter"), dict) else None,
            ),
        )


class LLMWorkspaceAgent:
    """Constrained ReAct runtime driven by the LLM."""

    def __init__(
        self,
        tools: Sequence[AgentTool],
        max_steps: int = 4,
        llm_client: LLMClient | None = None,
        memory_store: "LongTermMemory | None" = None,
        enable_memory: bool = True,
    ) -> None:
        self.llm = llm_client or LLMClient()
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps
        self.memory_store = memory_store
        if enable_memory and self.memory_store is None:
            from memory.long_term_memory import LongTermMemory

            self.memory_store = LongTermMemory()

    def _tool_block(self, allowed_tools: Sequence[str]) -> str:
        lines = []
        for name in allowed_tools:
            tool = self.tools[name]
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    def _scratchpad_block(self, scratchpad: List[Dict[str, Any]]) -> str:
        if not scratchpad:
            return "暂无工具调用记录。"

        blocks = []
        for index, item in enumerate(scratchpad, start=1):
            observation = json.dumps(item.get("observation", {}), ensure_ascii=False)
            if len(observation) > 3500:
                observation = observation[:3500] + "...(truncated)"
            blocks.append(
                f"第{index}步\n"
                f"action: {item.get('action')}\n"
                f"action_input: {json.dumps(item.get('action_input', {}), ensure_ascii=False)}\n"
                f"observation: {observation}"
            )
        return "\n\n".join(blocks)

    def _validate_final(self, final: Dict[str, Any], required_final_keys: Sequence[str]) -> List[str]:
        return [key for key in required_final_keys if key not in final]

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
        for _, value in user_payload.items():
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

    def _budget_for_task(
        self,
        *,
        task_name: str,
        allowed_tools: Sequence[str],
        max_steps: int | None = None,
    ) -> Dict[str, Any]:
        budget = dict(CONFIG.llm_agent_budget(task_name))
        if max_steps is not None:
            budget["max_steps"] = max(1, int(max_steps))
        else:
            budget["max_steps"] = max(1, int(budget.get("max_steps") or self.max_steps or 1))
        budget["max_tool_calls"] = max(1, int(budget.get("max_tool_calls") or 1))
        budget["repeat_action_limit"] = max(1, int(budget.get("repeat_action_limit") or 1))
        raw_tool_limits = dict(budget.get("tool_limits") or {})
        default_tool_limit = budget["max_tool_calls"]
        budget["tool_limits"] = {
            name: max(1, int(raw_tool_limits.get(name, default_tool_limit)))
            for name in allowed_tools
        }
        return budget

    def _tool_budget_block(self, budget: Dict[str, Any]) -> str:
        tool_limits = ", ".join(
            f"{name}<={limit}"
            for name, limit in dict(budget.get("tool_limits") or {}).items()
        ) or "none"
        return (
            f"- max_steps: {budget.get('max_steps')}\n"
            f"- max_tool_calls: {budget.get('max_tool_calls')}\n"
            f"- repeat_action_limit: {budget.get('repeat_action_limit')}\n"
            f"- tool_limits: {tool_limits}"
        )

    def _normalize_action_input(self, action_input: Dict[str, Any]) -> str:
        try:
            return json.dumps(action_input, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(action_input)

    def _tool_budget_error(
        self,
        *,
        action: str,
        action_input: Dict[str, Any],
        budget: Dict[str, Any],
        total_tool_calls: int,
        tool_usage_counts: Dict[str, int],
        repeated_action_inputs: Dict[str, int],
        recent_tool_actions: List[str],
    ) -> str:
        max_tool_calls = int(budget.get("max_tool_calls") or 1)
        if total_tool_calls >= max_tool_calls:
            return f"工具调用总次数已达到上限 {max_tool_calls}，请基于现有 observation 完成判断。"

        tool_limit = int(dict(budget.get("tool_limits") or {}).get(action, max_tool_calls))
        if tool_usage_counts.get(action, 0) >= tool_limit:
            return f"工具 {action} 调用次数已达上限 {tool_limit}，请改用其他工具或直接输出 final。"

        repeat_limit = int(budget.get("repeat_action_limit") or 1)
        action_signature = f"{action}:{self._normalize_action_input(action_input)}"
        if repeated_action_inputs.get(action_signature, 0) >= repeat_limit:
            return f"工具 {action} 使用相同参数重复调用已达上限 {repeat_limit}，请调整 query 或直接输出 final。"

        if len(recent_tool_actions) >= repeat_limit and all(
            previous_action == action
            for previous_action in recent_tool_actions[-repeat_limit:]
        ):
            return f"工具 {action} 连续重复调用已达上限 {repeat_limit}，请整合已有 observation 后再决策。"
        return ""

    def _required_tool_order_error(
        self,
        *,
        action: str,
        required_tools: Sequence[str],
        used_tools: Sequence[str],
        strict_required_tool_order: bool,
    ) -> str:
        if not strict_required_tool_order or not required_tools or action == "final":
            return ""
        remaining_required_tools = [name for name in required_tools if name not in used_tools]
        if not remaining_required_tools:
            return ""
        expected_tool = remaining_required_tools[0]
        if action != expected_tool:
            return f"当前必须先调用工具 {expected_tool}，然后才能继续调用 {action}。"
        return ""

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
            raise RuntimeError("未生成可用候选结果。")

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
        system_prompt_override: str | None = None,
        strict_required_tool_order: bool = False,
        action_validator: Callable[[str, Dict[str, Any], List[Dict[str, Any]], List[str]], str] | None = None,
    ) -> Dict[str, Any]:
        self.llm.require_available()

        missing_tools = [name for name in allowed_tools if name not in self.tools]
        if missing_tools:
            raise ValueError(f"未注册的工具: {', '.join(missing_tools)}")

        required_tools = list(required_tools or [])
        required_final_keys = list(required_final_keys or [])
        scratchpad: List[Dict[str, Any]] = []
        used_tools: List[str] = []
        tool_usage_counts: Dict[str, int] = {}
        repeated_action_inputs: Dict[str, int] = {}
        recent_tool_actions: List[str] = []
        user_id = self._build_history_key(task_name, user_payload)
        query_text = self._build_query_text(task_name, task_goal, user_payload)
        history_block = self._history_block(user_id, query_text) if load_history else ""
        budget = self._budget_for_task(task_name=task_name, allowed_tools=allowed_tools, max_steps=max_steps)

        required_tools_guidance = ""
        if required_tools:
            required_tools_guidance = (
                "你必须先调用工具获取信息，严禁直接输出 final 结果。"
                f"本任务至少要调用这些工具：{json.dumps(required_tools, ensure_ascii=False)}。"
            )
            if strict_required_tool_order:
                required_tools_guidance += f"必须严格按顺序调用：{json.dumps(required_tools, ensure_ascii=False)}。"
            elif "retrieval" in required_tools and "video_briefing" in required_tools:
                required_tools_guidance += "优先调用 retrieval 查询本地知识库，再调用 video_briefing 解析视频详情。"

        default_system_prompt = (
            "你是 B 站创作工作台的 LLM Agent 中枢。\n"
            "你必须采用 ReAct 范式：先基于用户输入和已有 observation 思考，再决定是否调用工具，最后输出结构化 JSON。\n"
            "所有判断都由你自主完成，不使用硬编码阈值，不依赖固定规则链。\n"
            "检索优先但不强制：当本地知识库可能包含历史经验、沉淀资料、案例或已知结构化信息时，优先考虑 retrieval。\n"
            "如果问题明显依赖最新公开信息，或 retrieval 返回的信息不足、不匹配、已过期，再调用 web_search。\n"
            "如果要使用 web_search，你需要自己生成最优搜索关键词再发起调用。\n"
        )
        system_prompt = (
            (system_prompt_override.strip() if isinstance(system_prompt_override, str) and system_prompt_override.strip() else default_system_prompt)
            + "\n"
            + required_tools_guidance
            + "\n严格遵守工具预算，不要为了凑步骤而无意义调用工具。"
        )

        for _ in range(int(budget.get("max_steps") or 1)):
            history_section = f"长期记忆:\n{history_block}\n\n" if load_history else ""
            user_prompt = (
                f"任务名称：{task_name}\n"
                f"任务目标：{task_goal}\n"
                f"用户输入：{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                f"{history_section}"
                f"可用工具：\n{self._tool_block(allowed_tools)}\n\n"
                f"工具预算：\n{self._tool_budget_block(budget)}\n\n"
                f"必须至少使用的工具：{json.dumps(required_tools, ensure_ascii=False)}\n"
                f"已经使用的工具：{json.dumps(used_tools, ensure_ascii=False)}\n\n"
                f"历史 observation：\n{self._scratchpad_block(scratchpad)}\n\n"
                "你必须只返回一个 JSON 对象，格式如下：\n"
                "{\n"
                '  "action": "工具名 或 final",\n'
                '  "action_input": {},\n'
                '  "final": null 或 最终结果对象\n'
                "}\n\n"
                "规则：\n"
                "1. 如果信息仍然不足，action 必须是某个工具名，final 必须为 null。\n"
                "2. 如果 action=final，final 必须完整满足下面的响应契约。\n"
                "3. 不要输出 markdown，不要输出解释，不要输出多余字段。\n"
                "4. 如果 retrieval 和 web_search 都已经返回了有效信息，final 中应整合两者，不要只引用其中一边。\n"
                "5. 如果 required_tools 非空，在这些工具都真正调用完成之前，禁止直接输出 final。\n\n"
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

            order_error = self._required_tool_order_error(
                action=action,
                required_tools=required_tools,
                used_tools=used_tools,
                strict_required_tool_order=strict_required_tool_order,
            )
            if order_error:
                scratchpad.append(
                    {
                        "action": "validation_error",
                        "action_input": {"blocked_action": action, "blocked_input": action_input},
                        "observation": {"error": order_error},
                    }
                )
                continue

            budget_error = self._tool_budget_error(
                action=action,
                action_input=action_input,
                budget=budget,
                total_tool_calls=len(used_tools),
                tool_usage_counts=tool_usage_counts,
                repeated_action_inputs=repeated_action_inputs,
                recent_tool_actions=recent_tool_actions,
            )
            if budget_error:
                scratchpad.append(
                    {
                        "action": "validation_error",
                        "action_input": {"blocked_action": action, "blocked_input": action_input},
                        "observation": {"error": budget_error},
                    }
                )
                continue

            if action_validator is not None:
                validator_error = str(action_validator(action, action_input, scratchpad, used_tools) or "").strip()
                if validator_error:
                    scratchpad.append(
                        {
                            "action": "validation_error",
                            "action_input": {"blocked_action": action, "blocked_input": action_input},
                            "observation": {"error": validator_error},
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
            tool_usage_counts[action] = tool_usage_counts.get(action, 0) + 1
            action_signature = f"{action}:{self._normalize_action_input(action_input)}"
            repeated_action_inputs[action_signature] = repeated_action_inputs.get(action_signature, 0) + 1
            recent_tool_actions.append(action)
            scratchpad.append(
                {
                    "action": action,
                    "action_input": action_input,
                    "observation": observation,
                }
            )

        raise RuntimeError("LLM Agent 未能在限定步数内完成任务。")
