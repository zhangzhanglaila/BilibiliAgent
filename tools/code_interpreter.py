"""Simple code interpreter wrapper for analysis and data processing."""
from __future__ import annotations

import contextlib
import io
import traceback
from typing import Any, Dict

try:
    from langchain_experimental.tools.python.tool import PythonAstREPLTool
except Exception:  # pragma: no cover
    PythonAstREPLTool = None


class CodeInterpreterTool:
    def __init__(self) -> None:
        self._tool = None
        if PythonAstREPLTool is not None:
            try:
                self._tool = PythonAstREPLTool()
            except Exception:
                self._tool = None

    def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        code = str(payload.get("code") or "").strip()
        variables = payload.get("variables") if isinstance(payload.get("variables"), dict) else {}
        if not code:
            return {"stdout": "", "result": "", "error": "missing_code"}

        if self._tool is not None:
            try:
                self._tool.locals.update(variables)
                result = self._tool.run(code)
                return {"stdout": "", "result": str(result or "").strip(), "error": ""}
            except Exception as exc:
                return {"stdout": "", "result": "", "error": str(exc)}

        buffer = io.StringIO()
        local_scope = dict(variables)
        try:
            with contextlib.redirect_stdout(buffer):
                exec(code, {"__builtins__": __builtins__}, local_scope)
        except Exception:
            return {"stdout": buffer.getvalue(), "result": "", "error": traceback.format_exc(limit=1).strip()}
        return {"stdout": buffer.getvalue(), "result": str(local_scope.get("result", "")).strip(), "error": ""}
