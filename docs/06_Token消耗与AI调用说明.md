# Token 消耗与双链路说明

## 1. 先说结论

这个项目现在是明确的双模式架构，不再是“部分地方可选调模型”的混合写法。

### 逻辑链路（无 Key）

- 条件：没有配置 `LLM_API_KEY`
- 结果：模块一和模块二都走现有纯代码逻辑
- 特点：不消耗 token，聊天助手关闭

### LLM 链路（有 Key）

- 条件：已经配置 `LLM_API_KEY`
- 结果：Web 端切换到 `LLMWorkspaceAgent`
- 特点：模块一、模块二、聊天助手都由 LLM Agent 主导，会消耗 token

## 2. 一般用户怎么判断当前会不会消耗 token

判断原则很简单：

- 当前是 `逻辑链路（无 Key）` -> 不会消耗 token
- 当前是 `LLM 链路（有 Key）` -> 会消耗 token

最直接的判断方式有 3 种：

### 方式 1：看页面顶部的运行模式

页面会显示：

- `运行模式：无 Key 规则模式`
- 或 `运行模式：LLM Agent 模式`

### 方式 2：看 `.env` 里的 `LLM_API_KEY`

如果 `.env` 中：

- `LLM_API_KEY=` 为空 -> 逻辑链路
- `LLM_API_KEY=` 有值 -> LLM 链路

### 方式 3：看运行时接口

访问：

- `/api/runtime-info`

如果返回：

- `mode=rules` -> 逻辑链路
- `mode=llm_agent` -> LLM 链路

如果你想看一步一步怎么切换，见：

- [07_运行模式切换说明.md](D:/agent/docs/07_运行模式切换说明.md)

## 3. 模式是怎么判断的

相关代码：

- [config.py](D:/agent/config.py#L58) 的 `llm_enabled()`
- [config.py](D:/agent/config.py#L61) 的 `runtime_mode()`
- [web/app.py](D:/agent/web/app.py#L645) 的 `/api/runtime-info`

判断规则非常直接：

- 有 `LLM_API_KEY` -> `llm_agent`
- 没有 `LLM_API_KEY` -> `rules`

## 4. 逻辑链路（无 Key）到底走什么

### 模块一

入口：

- [web/app.py](D:/agent/web/app.py#L839)

链路：

1. `api_module_create`
2. `build_seed_topic`
3. `run_topic`
4. `build_creator_topic_result`
5. `run_copy`

这条链路本质上还是：

- 纯代码整理输入
- 纯代码抓取样本
- 纯代码规则组装选题
- 文案 Agent 在无 Key 时走 fallback

### 模块二

入口：

- [web/app.py](D:/agent/web/app.py#L882)

链路：

1. `api_module_analyze`
2. `resolve_video_payload`
3. `run_topic`
4. `run_optimize`
5. `classify_video_performance`
6. `build_hot_analysis` 或 `build_low_performance_analysis`

这条链路本质上还是：

- 纯代码解析视频
- 纯代码判断数据表现
- 纯代码生成优化建议

### 聊天助手

逻辑链路下不可用：

- [web/app.py](D:/agent/web/app.py#L926)

如果没有 Key，`/api/chat` 会直接返回不可用提示。

## 5. LLM 链路（有 Key）到底走什么

### LLM Agent 中枢

核心文件：

- [agents/llm_workspace_agent.py](D:/agent/agents/llm_workspace_agent.py)

它负责：

- 决定先调用哪个工具
- 读取工具返回数据
- 再决定是否继续调用工具
- 最终输出模块结果或聊天回复

### 模块一

入口：

- [web/app.py](D:/agent/web/app.py#L839)
- [web/app.py](D:/agent/web/app.py#L752) 的 `run_llm_module_create`

链路：

1. `api_module_create`
2. `run_llm_module_create`
3. `LLMWorkspaceAgent`
4. `creator_briefing` 工具
5. LLM 输出选题和整套文案

### 模块二

入口：

- [web/app.py](D:/agent/web/app.py#L882)
- [web/app.py](D:/agent/web/app.py#L781) 的 `run_llm_module_analyze`

链路：

1. `api_module_analyze`
2. `run_llm_module_analyze`
3. `LLMWorkspaceAgent`
4. `video_briefing` / `hot_board_snapshot` 工具
5. LLM 输出判断、拆解、优化建议和新文案

### 聊天助手

入口：

- [web/app.py](D:/agent/web/app.py#L926)
- [web/app.py](D:/agent/web/app.py#L810) 的 `run_llm_chat`

链路：

1. 前端发送自然语言问题
2. `LLMWorkspaceAgent` 判断意图
3. 自主调用工具
4. 组织最终回答

## 6. LLM 链路下哪些地方会消耗 token

只要已经配置 `LLM_API_KEY`，下面这些 Web 端入口都会走模型：

- `/api/module-create`
- `/api/module-analyze`
- `/api/chat`

调用模型的底层封装在：

- [llm_client.py](D:/agent/llm_client.py)

严格调用方法是：

- [llm_client.py](D:/agent/llm_client.py#L73) 的 `invoke_json_required`
- [llm_client.py](D:/agent/llm_client.py#L88) 的 `invoke_text_required`

## 7. 逻辑链路和 LLM 链路的边界

### 逻辑链路

负责：

- 固定规则
- 固定判断
- 固定模板
- 无 token 运行

### LLM 链路

负责：

- 任务理解
- 工具选择
- 结果分析
- 判断结论
- 文案生成
- 聊天回复

工具层只负责提供数据，不负责最终结论。

## 8. 最后一句

如果你只是想本地跑通项目，不配 Key 也完全可以。

如果你想启用真正的 LLM Agent 工作流，再配置：

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

配置后，Web 端就会从逻辑链路切到 LLM 链路，并开始消耗 token。
