# Token 消耗与 AI 调用说明

## 1. 先说结论

这套项目不是“所有 Agent 都必须调用 AI 才能运行”。

它是两层结构：

1. 底层先用纯代码规则完成数据抓取、链接解析、指标计算、逻辑判断和基础生成
2. 只有部分“生成型”环节在配置了 `LLM_API_KEY` 之后，才会额外调用大模型做增强

所以：

- 如果没有配置 `LLM_API_KEY`，项目可以正常运行，而且不会消耗 token
- 如果配置了 `LLM_API_KEY`，只有文案生成、评论回复、优化建议这些环节可能消耗 token

## 2. 当前这个项目现在会不会消耗 token

按当前目录的实际情况看：

- `D:\\agent\\.env` 文件目前不存在
- 当前运行环境里的 `LLM_API_KEY` 也是空的
- `.env.example` 里默认也是空值

这意味着当前项目直接运行时，默认不会发起大模型请求，因此不会消耗 token。

相关代码：

- [llm_client.py](D:/agent/llm_client.py#L17) 里 `LLMClient` 会先判断 `CONFIG.llm_api_key` 是否存在
- [llm_client.py](D:/agent/llm_client.py#L33) 的 `invoke_json()` 在模型不可用时直接返回本地 fallback
- [llm_client.py](D:/agent/llm_client.py#L50) 的 `invoke_text()` 也是同样逻辑
- [.env.example](D:/agent/.env.example#L4) 里 `LLM_API_KEY` 默认是空的
- [config.py](D:/agent/config.py#L47) 里 `llm_api_key` 就是从环境变量里读取

## 3. 哪些部分是纯代码逻辑

下面这些核心流程，不依赖大模型也能跑：

- B 站链接解析、BV 提取、公开接口兜底、HTML 兜底解析
  - 见 [web/app.py](D:/agent/web/app.py#L553)
- 模块一里用户输入的整理、方向归一化、选题结果组装
  - 见 [web/app.py](D:/agent/web/app.py#L159)
  - 见 [web/app.py](D:/agent/web/app.py#L275)
- 视频表现判断，比如热门爆款还是播放偏低
  - 见 [web/app.py](D:/agent/web/app.py#L574)
- 热门视频抓取、同类视频抓取、关键词提取、竞争度估算、选题打分
  - 见 [agents/topic_agent.py](D:/agent/agents/topic_agent.py#L17)
  - 见 [agents/topic_agent.py](D:/agent/agents/topic_agent.py#L390)
- 数据优化里的规则诊断部分
  - 见 [agents/optimization_agent.py](D:/agent/agents/optimization_agent.py#L76)

简单说，系统最核心的“判断流程”本身并不是靠 AI 自己思考出来的，而是你项目里写死的规则、阈值、模板和数据处理逻辑。

## 4. 哪些部分会在有 Key 时调用 AI

只有下面这些生成型环节，会在配置了 `LLM_API_KEY` 时调用大模型：

### 4.1 文案生成

- 见 [agents/copywriting_agent.py](D:/agent/agents/copywriting_agent.py#L369)

这里会把选题、风格传给 `LLMClient.invoke_json()`。

如果没有 Key，就直接走本地 fallback 文案模板，不会调用 AI。

### 4.2 评论回复

- 见 [agents/operation_agent.py](D:/agent/agents/operation_agent.py#L41)

这里只在生成回复文案时可能调 AI。

如果没有 Key，就直接使用本地回复模板：

- 感谢类
- 问答类
- 互动类

### 4.3 优化建议润色

- 见 [agents/optimization_agent.py](D:/agent/agents/optimization_agent.py#L103)

这里会先跑一套本地规则诊断，再把结果交给大模型增强表达。

如果没有 Key，就直接返回规则生成的 fallback 建议。

## 5. 两个业务模块各自会不会消耗 token

### 模块一：还没发布视频，不知道做什么内容

调用链：

1. 前端提交到 [web/app.py](D:/agent/web/app.py#L670)
2. 后端先做输入整理和方向归一化
3. 再调用 [main.py](D:/agent/main.py#L17) 的 `run_topic()`
4. `run_topic()` 进入 [agents/topic_agent.py](D:/agent/agents/topic_agent.py#L390)
5. 最后调用 [main.py](D:/agent/main.py#L34) 的 `run_copy()`

这里面：

- `run_topic()` 主要是纯代码 + B 站数据抓取，不消耗 token
- `run_copy()` 只有在配置了 `LLM_API_KEY` 时才可能消耗 token

所以模块一是否消耗 token，关键只看文案生成那一步有没有启用大模型。

### 模块二：已经发布了视频，想分析和优化

调用链：

1. 前端提交到 [web/app.py](D:/agent/web/app.py#L715)
2. 先解析 B 站链接和公开数据
3. 再做视频表现判断
4. 再调用 `run_topic()`
5. 再调用 `run_optimize()`
6. 如果视频表现偏低，还会额外调用 `run_copy()`

这里面：

- 链接解析、数据解析、表现判断、选题分析，本质上是纯代码逻辑
- `run_optimize()` 在有 Key 时可能消耗 token
- 低表现视频额外生成新文案时，`run_copy()` 在有 Key 时也可能消耗 token

所以模块二不一定消耗 token，是否消耗取决于你有没有启用 LLM。

## 6. 你可以把它理解成什么架构

可以把这套系统理解成：

`规则引擎 / 数据处理` + `可选的大模型润色和增强`

不是：

`所有判断都交给 AI 黑盒完成`

这也是为什么即使不配 Key，这个项目仍然能跑起来。

## 7. 如果以后你配置了 Key，会发生什么

当你后面补上这些配置时：

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

系统就会开始在文案生成、回复生成、优化建议这些环节调用模型。

这时候才会产生 token 消耗。

注意：

- 是否计费，最终取决于你接入的模型服务商
- 如果请求已经发出但对方接口报错，是否计费也由服务商决定，不由本项目代码决定

## 8. 最后一句

按你现在这个仓库的当前状态，答案是：

当前默认不会消耗 token。

因为现在没有配置 `LLM_API_KEY`，系统运行时会自动走本地 fallback 逻辑。
