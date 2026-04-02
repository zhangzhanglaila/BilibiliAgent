# Java 开发者学习教程

这份文档面向已经熟悉 Java / Spring Boot，但第一次接触当前 Python + Flask + LangGraph 项目的开发者。

目标不是补 Python 语法课，而是帮你尽快看懂当前项目、跑通它、改动它。

## 1. 先用 Java 心智模型理解项目

你可以先这样映射：

```text
web/app.py                     -> Controller / API 层
main.py                        -> CLI Facade
graph.py                       -> Workflow Orchestrator
agents/*.py                    -> Service
knowledge_sync.py              -> Knowledge Sync Service
knowledge_base.py              -> Vector Store Service
llm_client.py                  -> LLM SDK Wrapper
config.py                      -> ConfigurationProperties
models.py                      -> DTO / VO
db.py                          -> Repository / DAO
web/templates + web/static     -> 前端页面
```

## 2. 当前项目和常见 Spring Boot 项目的主要差异

### 2.1 Flask 路由更直接

这里没有注解式控制器，接口直接写成函数：

- `@app.get(...)`
- `@app.post(...)`

入口在：[web/app.py](../web/app.py)

### 2.2 配置不是分层 Bean，而是一个 dataclass

环境变量统一从 [config.py](../config.py) 读取，核心类是：

- `AppConfig`

### 2.3 工作流编排不是 `@Transactional` 风格，而是 LangGraph

[graph.py](../graph.py) 负责把：

- 选题
- 文案
- 运营
- 优化

串成完整流程。

### 2.4 知识库不是 ES / PGVector，而是 Chroma

知识库实现分两层：

- [knowledge_base.py](../knowledge_base.py)：向量库读写
- [knowledge_sync.py](../knowledge_sync.py)：文件导入、热门样本同步

## 3. 当前项目建议阅读顺序

推荐按下面顺序读：

1. [config.py](../config.py)
2. [web/app.py](../web/app.py)
3. [web/templates/index.html](../web/templates/index.html)
4. [web/static/app.js](../web/static/app.js)
5. [knowledge_base.py](../knowledge_base.py)
6. [knowledge_sync.py](../knowledge_sync.py)
7. [agents/topic_agent.py](../agents/topic_agent.py)
8. [agents/copywriting_agent.py](../agents/copywriting_agent.py)
9. [agents/optimization_agent.py](../agents/optimization_agent.py)
10. [graph.py](../graph.py)
11. [main.py](../main.py)

## 4. 当前 Web 页面到底有哪些模块

不是旧版双模块。

现在是：

- 视频分析
- 内容创作
- 知识库管理
- 右侧智能助手

你在读代码时，优先看：

- [web/templates/index.html](../web/templates/index.html)
- [web/static/app.js](../web/static/app.js)

## 5. 当前最重要的 4 条业务链路

### 5.1 视频分析链路

1. 前端输入视频链接
2. `/api/resolve-bili-link` 解析视频
3. `/api/module-analyze` 做分析
4. 规则模式或 LLM Agent 模式返回结果

### 5.2 内容创作链路

1. 前端输入领域、方向、想法
2. `/api/module-create`
3. 选题结果 + 文案结果一起返回

### 5.3 知识库链路

1. `/api/knowledge/upload`
2. `/api/knowledge/update`
3. `/api/knowledge/sample`
4. `/api/knowledge/search`

### 5.4 聊天链路

1. `/api/chat`
2. `LLMWorkspaceAgent`
3. 按需调工具
4. 返回自然语言结果

## 6. 当前运行模式怎么理解

项目有两套概念，不要混：

### 6.1 环境默认状态

取决于 `.env` 有没有可用 LLM 配置。

### 6.2 页面运行时状态

页面顶部有开关，可以在当前会话内切换：

- 无 Key 逻辑模式
- LLM Agent 模式

这个设计和 Java 项目里的“配置中心 + 前端开关”组合更接近，不是单纯看启动参数。

## 7. 如果你要改需求，优先去哪一层

### 7.1 改页面展示

看：

- `web/templates/index.html`
- `web/static/app.js`
- `web/static/style.css`

### 7.2 改接口入参 / 出参

看：

- `web/app.py`

### 7.3 改规则逻辑

看：

- `agents/*.py`
- `knowledge_sync.py`
- `web/app.py` 里的规则辅助函数

### 7.4 改知识库行为

看：

- `knowledge_base.py`
- `knowledge_sync.py`
- `web/app.py` 的知识库接口

## 8. 当前项目最容易踩坑的点

### 8.1 页面模块数量已经变了

很多旧文档还写“两模块 + 聊天”，现在是“三模块 + 聊天”。

### 8.2 页面模式不等于 CLI 模式

页面的运行模式开关，只影响 Web 工作台。

CLI 和兼容接口要单独看。

### 8.3 知识库热门样本同步已经改成真实分区榜

不要再按旧理解把它当成 `knowledge / tech / life / game / ent` 的五个粗分区。

### 8.4 Chroma 文档是会分片的

如果你在展示层直接把每个命中分片都当成独立文档显示，就会出现同一篇文档重复展示的问题。

## 9. 给 Java 开发者的实际建议

- 不要一开始就钻进所有 Agent 细节里
- 先把 Web 主链路和知识库接口跑通
- 先理解页面输入如何一路传到 `web/app.py`
- 再理解规则模式和 LLM Agent 模式的分叉
- 最后再看 Agent 内部策略和提示词

如果你只打算先改一个需求，推荐从：

- 改一个接口字段
- 改一个前端卡片展示
- 改一个知识库标签文案

这三类改动开始。
