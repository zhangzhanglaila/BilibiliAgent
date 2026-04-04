# Token 消耗与 AI 调用说明

## 1. 先说结论

这个项目当前不能简单理解成“有 Key 就所有地方都走大模型”。

必须拆成两层看：

1. Web 主界面的运行模式
2. CLI / 兼容接口自身是否会调用 `LLMClient`

## 2. Web 主界面两种模式

### 2.1 无 Key 逻辑模式

当页面当前处于这个模式时：

- `/api/module-create` 走规则链路
- `/api/module-analyze` 走规则链路
- `/api/chat` 不可用
- 知识库接口不走 LLM

结果：

- Web 主界面不消耗 token

### 2.2 LLM Agent 模式

当页面当前处于这个模式时：

- `/api/module-create` 调 `LLMWorkspaceAgent`
- `/api/module-analyze` 调 `LLMWorkspaceAgent`
- `/api/chat` 可用
- 但三条链路的工具集合并不相同：
  - `module-create`：`retrieval`、`web_search`
  - `module-analyze`：`retrieval`、`web_search`
  - `chat`：`retrieval`、`web_search`、`video_briefing`、`hot_board_snapshot`

补充说明：

- `module-analyze` 当前视频信息和同方向爆款 `peer_samples` 由后端代码在进入 Agent 前预加载
- 因此当前 `module-analyze` 不再开放 `video_briefing`、`hot_board_snapshot`、`code_interpreter`

结果：

- Web 主界面会消耗 token

## 3. 页面里哪些操作不会消耗 token

无论是否启用知识库，下面这些接口当前都不依赖 LLM：

- `/api/knowledge/status`
- `/api/knowledge/sample`
- `/api/knowledge/search`
- `/api/knowledge/upload`
- `/api/knowledge/update/start`
- `/api/knowledge/update/<job_id>`
- `/api/knowledge/update`
- `/api/resolve-bili-link`

也就是说：

- 知识库上传
- 热门样本同步
- 知识库查看
- 一级分类检索

都不走大模型。

## 4. 页面里哪些操作可能消耗 token

只有当页面当前已经切到 `LLM Agent 模式` 时，下面这些操作才会消耗 token：

- 内容创作
- 视频分析
- 智能助手

对应接口：

- `/api/module-create`
- `/api/module-analyze`
- `/api/chat`

## 5. 页面模式怎么判断

有 3 种办法：

### 5.1 看页面顶部

页面会显示：

- `当前运行中：无 Key 逻辑模式`
- 或 `当前运行中：LLM Agent 模式`

### 5.2 看运行时接口

```text
GET /api/runtime-info
```

关键字段：

- `mode`
- `llm_enabled`
- `chat_available`

### 5.3 看页面开关状态

运行模式区域的开关只反映 Web 当前运行时状态。

## 6. 一个容易误解的点

页面模式开关不等于整个项目的唯一 LLM 开关。

### 6.1 Web 工作台

受运行模式区域的开关控制。

### 6.2 CLI 和兼容接口

不受页面运行时开关直接控制。

例如：

- `python main.py copy ...`
- `python main.py optimize ...`
- `/api/copy`
- `/api/optimize`
- `/api/pipeline`

这类入口内部使用的 Agent 自带 `LLMClient`，如果环境里有可用配置，可能会尝试走 LLM；如果不可用，则回退规则结果。

## 7. 当前各入口与 LLM 的关系

### 7.1 一定不走 LLM

- 知识库全部接口
- 视频链接解析接口
- `TopicAgent`
- 规则模式下的 Web 内容创作
- 规则模式下的 Web 视频分析

### 7.2 可能走 LLM

- Web 的 `module-create`，仅在 LLM Agent 模式
- Web 的 `module-analyze`，仅在 LLM Agent 模式
- Web 的 `chat`，仅在 LLM Agent 模式
- CLI / `/api/copy`
- CLI / `/api/optimize`
- CLI / `/api/pipeline`

## 8. 当前推荐理解方式

如果你只关心“页面现在会不会消耗 token”，就只看：

- 顶部模式文字
- 运行模式开关

如果你关心“这个项目整体会不会调 LLM”，还要额外看：

- `.env`
- CLI 调用路径
- 兼容接口调用路径

## 9. 当前文档对齐说明

本说明已按现有实现更新：

- 知识库功能明确归类为“不走 LLM”
- 页面当前热门样本同步链路已按 `start + 轮询 job` 更新
- Web 运行模式与 CLI / 兼容接口分开说明
- 不再把三条 Agent 链路的工具能力混写成一套固定配置
- 不再把所有接口都笼统写成“有 Key 就一定消耗 token”
