# Token 消耗与 AI 调用说明

## 1. 先说结论

这个项目现在不是“所有地方都统一走大模型”，而是分成：

- 主界面双模式工作流
- 兼容接口 / 旧 Agent 直调 LLM

所以判断 token 是否消耗，必须分两层看：

1. Web 主界面当前是不是 LLM Agent 模式
2. 你调用的是不是兼容接口

## 2. Web 主界面两种模式

### 2.1 规则模式

触发条件：

- `.env` 中 `LLM_API_KEY` 为空

结果：

- `/api/module-create` 不调用 LLM Agent 中枢
- `/api/module-analyze` 不调用 LLM Agent 中枢
- `/api/chat` 不可用

对于主界面来说：

- 不消耗 token

### 2.2 LLM Agent 模式

触发条件：

- `.env` 中 `LLM_API_KEY` 有值

结果：

- `/api/module-create` 调用 `LLMWorkspaceAgent`
- `/api/module-analyze` 调用 `LLMWorkspaceAgent`
- `/api/chat` 可用

对于主界面来说：

- 会消耗 token

## 3. 主界面怎么判断当前会不会消耗 token

最直接有 3 种方式。

### 3.1 看页面顶部

页面会显示：

- `无 Key 规则模式`
- 或 `LLM Agent 模式`

只有第二种会让主界面进入 token 消耗状态。

### 3.2 看 `.env`

如果：

```env
LLM_API_KEY=
```

则主界面规则模式，不消耗 token。

如果 `LLM_API_KEY` 有值，则主界面进入 LLM 模式。

### 3.3 看运行时接口

访问：

```text
http://127.0.0.1:8000/api/runtime-info
```

如果返回：

- `mode=rules` -> 主界面不消耗 token
- `mode=llm_agent` -> 主界面会消耗 token

## 4. 主界面里哪些操作会消耗 token

当且仅当当前处于 LLM Agent 模式时，下面这些入口会消耗 token：

- 内容创作模块 `/api/module-create`
- 视频分析模块 `/api/module-analyze`
- 智能对话助手 `/api/chat`

## 5. 兼容接口不是完全跟主界面模式绑定

这部分是旧文档最容易漏掉的。

虽然主界面是双模式，但兼容接口仍然保留，并且其中一部分在有 Key 时会直接调用 `LLMClient`。

### 5.1 `/api/topic`

对应：

- `run_topic()`
- `TopicAgent`

特点：

- 不直接调用 LLM
- 主要走公开样本和规则逻辑

### 5.2 `/api/copy`

对应：

- `run_copy()`
- `CopywritingAgent`

特点：

- 无 Key 时走 fallback
- 有 Key 时会尝试调用 `LLMClient`

所以：

- 有 Key 时，这个接口可能消耗 token

### 5.3 `/api/operate`

对应：

- `run_operate()`
- `OperationAgent`

特点：

- `generate_reply()` 内部会尝试调用 `LLMClient`

所以：

- 有 Key 时，这个接口也可能消耗 token

### 5.4 `/api/optimize`

对应：

- `run_optimize()`
- `OptimizationAgent`

特点：

- 先生成规则建议
- 再尝试调用 `LLMClient`

所以：

- 有 Key 时，这个接口也可能消耗 token

### 5.5 `/api/pipeline`

对应：

- `run_pipeline()`
- LangGraph 串联的 `topic -> copy -> operate -> optimize`

因为它会串上：

- `copy`
- `operate`
- `optimize`

所以当有 Key 时：

- 这个接口很可能会消耗 token

## 6. 当前 LLM 调用的代码入口

### 6.1 统一客户端

项目底层统一封装在：

- [llm_client.py](D:/agent/llm_client.py)

关键方法：

- `invoke_json()`
- `invoke_text()`
- `invoke_json_required()`
- `invoke_text_required()`

### 6.2 LLM Agent 中枢

主界面 LLM 模式下的中枢在：

- [agents/llm_workspace_agent.py](D:/agent/agents/llm_workspace_agent.py)

它不是直接产出固定结果，而是：

1. 先决定调用哪个工具
2. 记录工具观察结果
3. 再输出最终结构化结果

### 6.3 当前已注册工具

主界面当前的工具包括：

- `creator_briefing`
- `video_briefing`
- `hot_board_snapshot`

## 7. LLM 模式下的真实链路

### 7.1 内容创作模块

链路是：

1. `/api/module-create`
2. `run_llm_module_create`
3. `LLMWorkspaceAgent`
4. `creator_briefing`
5. LLM 生成选题和文案

失败时可能回退到：

- `run_llm_module_create_fallback`

### 7.2 视频分析模块

链路是：

1. `/api/module-analyze`
2. `run_llm_module_analyze`
3. `LLMWorkspaceAgent`
4. `hot_board_snapshot`
5. LLM 输出表现判断、原因、优化建议和新文案

失败时可能回退到：

- `run_llm_module_analyze_fallback`

### 7.3 智能对话助手

链路是：

1. `/api/chat`
2. `run_llm_chat`
3. `LLMWorkspaceAgent`
4. 自主决定调用：
   - `creator_briefing`
   - `video_briefing`
   - `hot_board_snapshot`
5. 输出回答和参考链接

## 8. 当前 token 消耗的边界

### 8.1 一定不会消耗 token 的情况

- 主界面处于规则模式
- 调用 `/api/topic`

### 8.2 一定会消耗 token 的情况

只要主界面已经进入 LLM Agent 模式：

- 调用 `/api/module-create`
- 调用 `/api/module-analyze`
- 调用 `/api/chat`

### 8.3 可能消耗 token 的情况

即使不是主界面双模式主链路，只要你配置了 `LLM_API_KEY`，这些接口也可能消耗 token：

- `/api/copy`
- `/api/operate`
- `/api/optimize`
- `/api/pipeline`

## 9. 超时、重试和 provider 速度

当前项目的 LLM 行为受这几个环境变量影响：

- `LLM_TIMEOUT_SECONDS`
- `LLM_MAX_RETRIES`
- `LLM_RETRY_BACKOFF_SECONDS`

这意味着：

- provider 慢时，请求时间会拉长
- 重试次数越多，失败总耗时越长
- 某些 provider 对复杂 JSON 分析 prompt 可能明显慢于简单问答

## 10. 为什么“简单测试能通，但复杂分析会失败”

这是当前项目里非常现实的一类问题。

原因通常不是单一的“key 错了”，而更可能是：

- key 有效
- base_url 可连通
- 但 provider 对复杂分析 prompt 响应更慢
- 最终在网关或 provider 侧超时

所以要区分：

- 最小化请求是否能通
- 复杂分析链路是否也能稳定返回

## 11. 如果你想尽量少消耗 token

建议：

1. 不配置 `LLM_API_KEY`
2. 先用规则模式跑主界面
3. 只有需要聊天或更强分析时，再切到 LLM 模式

如果你只想调试业务结构，也可以优先用：

- `/api/topic`

因为它本身不直接依赖 LLM。

## 12. 最后一句

主界面是否消耗 token，核心判断看：

- `LLM_API_KEY`

但项目整体是否完全不消耗 token，不能只看主界面，还要看你有没有调用这些兼容接口：

- `/api/copy`
- `/api/operate`
- `/api/optimize`
- `/api/pipeline`
