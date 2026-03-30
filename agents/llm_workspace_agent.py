"""Strict LLM agent runtime for key-enabled mode."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Sequence

from llm_client import LLMClient


@dataclass
class AgentTool:
    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]


class LLMWorkspaceAgent:
    # 初始化受控 Agent 运行时，注册工具并设置最大推理步数。
    def __init__(self, tools: Sequence[AgentTool], max_steps: int = 4, llm_client: LLMClient | None = None) -> None:
        self.llm = llm_client or LLMClient()
        self.tools = {tool.name: tool for tool in tools}
        self.max_steps = max_steps

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

    # 运行受限的工具调用循环，直到模型给出合法最终结果或步数耗尽。
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

        system_prompt = (
            "你是 B 站创作工作台的 LLM Agent 中枢。\n"
            "当前处于严格 LLM 模式：所有分析、判断、决策、生成都必须基于用户输入和工具返回信息实时完成。\n"
            "不要套用固定阈值、预设模板、硬编码结论，也不要把任务退回规则引擎。\n"
            "你可以多步调用工具；当信息足够时，再输出最终 JSON。"
        )

        # 整个 Agent 只允许在“调工具”或“给最终结果”之间有限次循环，方便排查问题，
        # 也避免模型在开放式推理里越跑越偏。
        for _ in range(limit):
            user_prompt = (
                f"任务名称：{task_name}\n"
                f"任务目标：{task_goal}\n"
                f"用户输入：{json.dumps(user_payload, ensure_ascii=False)}\n\n"
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

                # 把执行轨迹带回去，前端或排障时可以看到这次回答到底用了哪些工具。
                final.setdefault("agent_trace", used_tools)
                final.setdefault("tool_observations", scratchpad)
                final.setdefault("runtime_mode", "llm_agent")
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
            try:
                observation = tool.handler(action_input)
            except Exception as exc:
                observation = {"error": str(exc)}
            used_tools.append(action)
            scratchpad.append(
                {
                    "action": action,
                    "action_input": action_input,
                    "observation": observation,
                }
            )

        raise RuntimeError("LLM Agent 未能在限定步骤内完成任务。")
