---
name: langchain version incompatibility
description: langchain-core 与 langchain-openai 版本不兼容导致 LLM 调用失败
type: feedback
---

## 问题

LLM 调用报错：`ImportError: cannot import name 'ContextOverflowError' from 'langchain_core.exceptions'`

错误来源：启动智能对话时，`langchain_openai` 导入时依赖 `langchain_core.exceptions.ContextOverflowError`，但当前安装的 `langchain-core 0.3.83` 中不存在此名称。

**Why:** 不同版本的 langchain 包之间存在严格的版本依赖关系。`langchain-openai 1.1.12` 依赖的 API 在 `langchain-core 0.3.83` 中尚未引入或不兼容。

**How to apply:** 遇到 LLM 不可用且确认 API Key 配置正确时，首先检查依赖版本兼容性。使用 `pip list | grep langchain` 对比版本，必要时执行 `pip install --upgrade -r requirements.txt` 重装依赖。
