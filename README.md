# B 站全自动运营多 Agent 系统

基于 Python + LangGraph + LangChain + bilibili-api-python 的多 Agent 项目，现已同时提供：
- 命令行运行方式
- 轻量级前端页面
- 面向 Java 开发者的完整文档

---

## 1. 核心能力

项目包含 4 个核心 Agent：
- 选题 Agent
- 文案 Agent
- 运营 Agent
- 数据优化 Agent

支持两种使用方式：
- CLI：适合开发调试
- Web 页面：适合可视化操作和演示

---

## 2. 安装依赖

```bash
pip install -r requirements.txt
```

---

## 3. 配置环境变量

复制模板：

### Windows
```bash
copy .env.example .env
```

### macOS / Linux
```bash
cp .env.example .env
```

如果你有 DeepSeek / Qwen 的 OpenAI 兼容接口，可填写：
- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

如果不填，也能运行，系统会自动进入本地降级模式。

---

## 4. CLI 启动方式

### 选题 Agent
```bash
python main.py topic --partition knowledge
```

### 文案 Agent
```bash
python main.py copy --topic "AI 视频剪辑提效" --style 干货
```

### 运营 Agent
```bash
python main.py operate --bv BV1xx411c7mD --dry-run
```

### 数据优化 Agent
```bash
python main.py optimize --bv BV1xx411c7mD
```

### 全流程
```bash
python main.py pipeline --bv BV1xx411c7mD --partition knowledge --style 干货
```

---

## 5. 前端启动方式

```bash
python web/app.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

页面支持：
- 单独运行 4 大 Agent
- 一键运行全流程
- 直接查看结构化结果

---

## 6. 文档目录

详细文档请看 `docs/`：

- `docs/01_项目说明.md`
- `docs/02_完整部署文档.md`
- `docs/03_Java开发者学习教程.md`
- `docs/04_前端说明.md`
- `docs/05_前端使用手册.md`

---

## 7. 说明

- 默认使用 SQLite，本地自动生成 `bilibili_agents.db`
- 默认对敏感互动动作用 `dry-run`
- 完播率、平均观看时长为估算值
- 若 B 站接口访问失败，会自动使用演示数据保证流程可跑通

如果你是 Java / SpringBoot 开发者，建议先看：

```text
docs/03_Java开发者学习教程.md
```
