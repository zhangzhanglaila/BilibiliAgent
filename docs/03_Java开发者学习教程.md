# Java 开发者学习教程

这份文档面向熟悉 Java / SpringBoot，但第一次接触这个 Python Agent 项目的开发者。

## 1. 先用 Java 的思维理解这个项目

可以把它看成一套典型的“控制层 + 服务层 + 编排层 + 模型层”结构：

```text
web/app.py          -> Controller / API 层
main.py             -> 可复用服务入口
graph.py            -> 流程编排层
agents/*.py         -> 具体业务能力
models.py           -> DTO / 数据模型
config.py           -> 配置类
db.py               -> 持久化层
```

## 2. 两个业务模块对应什么

### 模块一：还没发布视频

等价于：

- Controller 接收用户输入
- Service 组装一个主题种子
- TopicAgent 生成选题
- CopywritingAgent 生成文案

### 模块二：已经发布视频

等价于：

- Controller 接收视频链接
- Service 解析 BV 和公开数据
- TopicAgent 给出后续选题方向
- OptimizationAgent 给出优化建议
- 必要时 CopywritingAgent 生成新文案参考

## 3. 你真正要看的入口文件

如果你想快速看懂项目，顺序建议如下：

1. `web/app.py`
2. `main.py`
3. `graph.py`
4. `agents/topic_agent.py`
5. `agents/copywriting_agent.py`
6. `agents/optimization_agent.py`

## 4. 和 SpringBoot 的常见映射

### 4.1 Controller

对应：

- `@app.post("/api/module-create")`
- `@app.post("/api/module-analyze")`

这两个路由就是当前 Web 端的两个业务入口。

### 4.2 Service

对应：

- `run_topic()`
- `run_copy()`
- `run_optimize()`
- `run_pipeline()`

这些函数位于 `main.py`，作用和 Java 项目里的 Service / Facade 很像。

### 4.3 Flow / Orchestration

对应：

- `graph.py`

这里类似你在 Java 里写的一段流程编排器，只是现在用 LangGraph 来表达。

## 5. 如果你想二次开发

### 场景一：改前端交互

主要改：

- `web/templates/index.html`
- `web/static/app.js`
- `web/static/style.css`

### 场景二：加新的业务判断

主要改：

- `web/app.py`
- `agents/optimization_agent.py`
- `agents/topic_agent.py`

### 场景三：把项目接进你自己的后端

最直接的方式不是直接抄前端，而是复用：

- `main.py`
- `graph.py`
- `agents/*.py`

你可以把这些 Python 逻辑封装成独立服务，再由 Java 后端通过 HTTP 调用。

## 6. 当前最值得注意的点

1. Web 端已经从“4 个 Agent 工作台”改成“2 个业务模块”
2. 但底层 4 个 Agent 仍然保留
3. 如果文档和旧截图不一致，以当前代码为准
