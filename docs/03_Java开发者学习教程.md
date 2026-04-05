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
12. [08_Agent智能体模块专项技术文档.md](./08_Agent智能体模块专项技术文档.md)

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
2. 页面当前会先调 `POST /api/knowledge/update/start`
3. 再轮询 `GET /api/knowledge/update/<job_id>`
4. `/api/knowledge/sample`
5. `/api/knowledge/search`
6. 如果直接调用后端同步接口，仍保留 `POST /api/knowledge/update`

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
- 最后再看 [08_Agent智能体模块专项技术文档.md](./08_Agent智能体模块专项技术文档.md) 里的 Agent 工具循环、RAG、记忆和 Reflection

如果你只打算先改一个需求，推荐从：

- 改一个接口字段
- 改一个前端卡片展示
- 改一个知识库标签文案

这三类改动开始。

## 10. `/api/resolve-bili-link` 当前逻辑与最近修复

这个接口现在不是“只把链接里的 BV 号正则提出来”这么简单，而是分成两条链路：

1. 预览快速链路：给 `/api/resolve-bili-link` 用
2. 完整解析链路：给 `/api/module-analyze` 和 `resolve_video_payload()` 用

两条链路都会先统一抽取标准 `BV`：
   - 支持标准视频链接
   - 支持 `b23.tv`
   - 支持 `cm.bilibili.com` 这类追踪链接
   - 支持旧版 `av` 链接转 `BV`

### 10.1 当前两条链路分别怎么走

#### A. `/api/resolve-bili-link` 预览快速链路

只为了让前端先看到标题、封面、时长、UP、统计这些基础信息。

顺序是：

1. 先走 HTML 快速解析
2. HTML 失败才退到 B 站公开视频接口
3. 再失败才退到 `bilibili_api`
4. 成功后直接整理成 `resolved`

这条链路的目标是“尽快出基础预览”，不会主动为了补更多标签信息去等待慢接口。

#### B. `resolve_video_payload()` 完整解析链路

这条链路是给后续分析模块用的，仍然会优先追求结构化和补充信息完整度。

顺序是：

1. 先走 B 站公开视频接口
2. 再走 `bilibili_api`
3. 最后才走 HTML 兜底
4. 主信息拿到后，再做 best-effort 补充：
   - 补 `tags / keywords`
   - 必要时用 HTML hints 补 `tname`
5. 最后统一整理成 `resolved`

### 10.2 为什么看起来像“明明不复杂却会失败”

最近一次典型现象是：

- 输入 `BV1dcX5BYESE`
- 前端报错里同时出现：
  - `public api: <urlopen error _ssl.c:989: The handshake operation timed out>`
  - `html: 网页源码中未找到视频标题`

根因其实有两个：

1. HTML 兜底解析以前没有处理 B 站当前返回的压缩页面。
   - B 站视频页会返回 `gzip`
   - 旧逻辑直接把压缩字节流按 UTF-8 解码
   - 所以即使页面真实有 `<title>`、`og:title`、`__INITIAL_STATE__`，代码也读不到
2. “公开接口”这层以前把“主信息获取”和“附加补充”绑在了同一个 `try` 里。
   - 主信息其实可能已经拿到了
   - 但后续补 `tags` 或 HTML hints 时一旦超时，整次解析仍然会被记成 `public api` 失败

### 10.3 为什么后来又出现“只解析基础信息却要 30 多秒”

这次性能问题的根因不是 HTML 解析慢，而是预览接口曾经直接复用了完整解析链路。

在当前网络环境里，实测出现过：

- 公开视频接口等待约 `11s` 后超时
- `fetch_video_tags()` 再等待约 `10s` 超时
- `bilibili_api` 再等待约 `20s` 左右失败
- 真正能拿到基础信息的 HTML 解析本身只要不到 `1s`

所以“30 多秒”本质上是在等几个已经失败的慢接口超时，不是解析基础信息本身复杂。

### 10.4 当前修复后的行为

- `fetch_text()` 已兼容 `gzip / deflate` 响应体，HTML 兜底能真正读到页面源码。
- 补 `tags / HTML hints` 改成 best-effort。
- `/api/resolve-bili-link` 已切到“HTML 优先”的预览快速链路。
- 也就是说：
  - 预览阶段优先秒出基础字段
  - 完整分析阶段仍然允许走更完整但更慢的补充逻辑

### 10.5 现在还会不会调用那些复杂接口

会，但分场景：

- `/api/resolve-bili-link`
  - 正常情况下优先走 HTML 快速解析
  - 只有 HTML 失败时，才会退到公开视频接口和 `bilibili_api`
- `/api/module-analyze`
  - 如果前端传来的 `resolved` 可复用，就不需要重新完整解析
  - 如果没有可用 `resolved`，仍会走完整解析链路

这些较复杂的接口主要能额外带来：

- 更稳定的结构化字段来源
- 额外的 `tags / keywords`
- 更有机会补齐 `tname`
- 在 HTML 结构变化时，提供另一条备用来源

排查这条链路时，优先看：

- `extract_bvid()`
- `fetch_video_preview_info()`
- `fetch_video_info()`
- `build_resolved_payload()`
- `/api/resolve-bili-link`
