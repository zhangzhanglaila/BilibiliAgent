# B站内容策划与视频分析工作台

这是一个基于 Python、LangGraph、LangChain 和 `bilibili-api-python` 的 B 站创作辅助项目。

当前 Web 端保持两个核心业务模块，并新增一个仅在 LLM 模式下可用的智能对话助手：

1. 模块一：还没发布视频，不知道做什么内容
   - 输入你的领域、方向、想法
   - 结合当前热门结构生成更容易起量的选题
   - 自动生成标题、脚本、简介、标签、置顶评论

2. 模块二：已经发布了视频，想分析和优化
   - 输入 B 站视频链接
   - 后台自动解析视频信息和公开数据
   - 判断更像热门爆款还是播放偏低
   - 给出爆款拆解或优化建议

3. 智能对话助手
   - 直接自然语言提问
   - Agent 按意图自主调用工具
   - 仅在配置 `LLM_API_KEY` 后启用

项目底层仍然保留原有 4 个 Agent，并新增一个 LLM Agent 中枢用于有 Key 模式：

- `TopicAgent`：选题分析
- `CopywritingAgent`：文案生成
- `OperationAgent`：互动运营建议
- `OptimizationAgent`：数据优化建议
- `LLMWorkspaceAgent`：有 Key 时统一接管选题、分析、生成和聊天工具调用

Web 层采用双模式架构：

- 无 Key：完全沿用现有纯代码规则逻辑，不消耗 token
- 有 Key：切换到 LLM Agent 中枢 + 工具集，模块一、模块二、聊天面板都由 LLM 主导

### 逻辑链路（无 Key）

- `module-create`：纯代码选题与文案链路
- `module-analyze`：纯代码视频解析与优化链路
- 聊天助手：关闭

### LLM 链路（有 Key）

- `module-create`：`LLMWorkspaceAgent` + `creator_briefing`
- `module-analyze`：`LLMWorkspaceAgent` + `video_briefing`
- `chat`：`LLMWorkspaceAgent` 自主调用工具

## 安装

```bash
pip install -r requirements.txt
```

## 配置

复制环境变量模板：

### Windows

```bash
copy .env.example .env
```

### macOS / Linux

```bash
cp .env.example .env
```

```env
# edit D:\agent\.env
LLM_PROVIDER=openai
LLM_API_KEY=your-real-key
LLM_BASE_URL=https://zapi.aicc0.com/v1
LLM_MODEL=gpt-5.4
```

可选配置：

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `DEFAULT_PARTITION`
- `DEFAULT_PEER_UPS`

如果没有填写 LLM Key，系统仍可运行，会自动使用纯代码规则模式。

如果填写了 `LLM_API_KEY`，Web 层会自动切换到 LLM Agent 模式。

## 启动

### Web 页面

```bash
python web/app.py
```

浏览器访问：

```text
http://127.0.0.1:8000
```

### CLI

```bash
python main.py topic --partition knowledge --topic "AI 剪辑效率"
python main.py copy --topic "AI 剪辑第一条视频先拍什么更容易起量" --style 干货
python main.py optimize --bv BV1xx411c7mD
python main.py pipeline --bv BV1xx411c7mD --partition knowledge --style 干货 --topic "AI 剪辑效率"
```

## Web 模块说明

### 模块一：选题与文案

输入：

- 领域
- 方向
- 想法
- 可选分区（知识、科技、生活、游戏、娱乐，以及美妆颜值、舞蹈、音乐、影视、动漫、美食、Vlog、穿搭、情感、萌宠、运动、汽车、商业职场等）
- 文案风格

输出：

- 整理后的创作方向
- 3 个更适合做的选题方向
- 自动生成的标题
- 视频脚本
- 简介、标签、置顶评论

### 模块二：视频解析与优化

输入：

- B 站视频链接

输出：

- BV 号、UP 主、分区、播放、点赞、投币、收藏等解析结果
- 热门爆款 / 播放偏低 判断
- 爆款拆解或优化建议
- 可继续延展的选题方向
- 对于低表现视频，额外生成一版新的文案参考

### 智能对话助手

输入：

- 自然语言问题
- 页面当前上下文（领域、方向、想法、视频链接）

输出：

- Agent 自主调用工具后的直接回答
- 可继续执行的下一步建议
- 仅在 `LLM_API_KEY` 已配置时可用

## 项目结构

```text
D:\agent
├─ agents/
│  ├─ topic_agent.py
│  ├─ copywriting_agent.py
│  ├─ operation_agent.py
│  ├─ optimization_agent.py
│  └─ llm_workspace_agent.py
├─ web/
│  ├─ app.py
│  ├─ templates/
│  │  └─ index.html
│  └─ static/
│     ├─ style.css
│     └─ app.js
├─ docs/
├─ config.py
├─ db.py
├─ graph.py
├─ llm_client.py
├─ main.py
├─ models.py
└─ requirements.txt
```

## 说明

- 默认使用 SQLite，本地会生成 `bilibili_agents.db`
- 评论互动类动作默认 `dry-run`
- 公开数据接口失败时，会自动尝试其他解析方式
- Web 页面保留两个业务模块，并新增聊天面板
- 无 Key 时不会消耗 token
- 有 Key 时 Web 层会切到严格的 LLM Agent 模式

更详细说明见 `docs/` 目录。 
