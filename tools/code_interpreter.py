"""Simple code interpreter wrapper for analysis and data processing.
代码解释器工具，用于执行和分析Python代码。
主要通过LangChain的PythonAstREPLTool或原生exec来执行代码。
"""
from __future__ import annotations

import contextlib
import io
import traceback
from typing import Any, Dict

# 尝试导入LangChain的Python AST REPL工具
# 该工具提供更安全的Python代码执行环境
try:
    from langchain_experimental.tools.python.tool import PythonAstREPLTool
except Exception:  # pragma: no cover
    PythonAstREPLTool = None


class CodeInterpreterTool:
    """代码解释器类，提供安全的Python代码执行能力。

    支持两种执行方式：
    1. LangChain的PythonAstREPLTool（优先使用，更安全）
    2. 原生exec（备选方案）
    """
    def __init__(self) -> None:
        """初始化代码解释器工具。

        尝试创建PythonAstREPLTool实例。
        如果导入失败或创建失败，则self._tool为None，
        后续将使用原生exec作为备选执行方式。
        """
        self._tool = None
        if PythonAstREPLTool is not None:
            try:
                self._tool = PythonAstREPLTool()
            except Exception:
                self._tool = None

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """执行Python代码的核心方法。

        Args:
            payload: 包含以下键的字典：
                - code: 要执行的Python代码字符串
                - variables: 可选的变量字典，会被注入到执行环境中

        Returns:
            包含执行结果的字典：
                - stdout: 标准输出内容
                - result: 执行结果（如果有的话）
                - error: 错误信息（如果有的话）
        """
        # 提取并清理代码字符串
        code = str(payload.get("code") or "").strip()
        # 提取变量字典，确保是dict类型
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        # 如果没有提供代码，返回错误信息
        if not code:
            return {"stdout": "", "result": "", "error": "missing_code"}

        # 优先使用LangChain的PythonAstREPLTool执行代码
        if self._tool is not None:
            try:
                # 将变量注入到工具的本地作用域
                self._tool.locals.update(variables)
                # 执行代码
                result = self._tool.run(code)
                return {"stdout": "", "result": str(result or "").strip(), "error": ""}
            except Exception as exc:
                # 执行出错，返回错误信息
                return {"stdout": "", "result": "", "error": str(exc)}

        # 备选方案：使用原生exec执行代码
        # 创建StringIO缓冲区来捕获标准输出
        buffer = io.StringIO()
        # 创建独立的本地作用域，避免污染全局环境
        local_scope = dict(variables)
        try:
            # 重定向标准输出到缓冲区
            with contextlib.redirect_stdout(buffer):
                # 在隔离的作用域中执行代码
                exec(code, {"__builtins__": __builtins__}, local_scope)
        except Exception:
            # 执行出错，捕获异常信息并返回
            return {"stdout": buffer.getvalue(), "result": "", "error": traceback.format_exc(limit=1).strip()}
        # 执行成功，返回stdout和result（如果有的话）
        return {"stdout": buffer.getvalue(), "result": str(local_scope.get("result", "")).strip(), "error": ""}
