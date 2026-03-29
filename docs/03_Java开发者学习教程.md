# Java 开发者学习教程

这份教程面向已经熟悉 Java / Spring Boot，但第一次接触 Python、Flask、LangGraph 和 LLM Agent 项目的开发者。

目标不是让你“会一点 Python 语法”，而是让你能真正看懂这个项目、跑通它、修改它、继续开发它。

## 1. 学完之后你应该能做到什么

建议学习周期：

- 标准路线：14 天
- 每天投入：1.5 到 3 小时

完成后你至少应该能做到：

1. 看懂这个项目的目录结构和主调用链
2. 能区分规则模式和 LLM Agent 模式
3. 能读懂 `web/app.py`、`main.py`、`graph.py`、`agents/*.py`
4. 能自己改一个接口字段
5. 能自己加一个简单业务规则
6. 能把这个 Python 服务接到你自己的 Java 服务里

## 2. 先用 Java 思维理解这个项目

你可以先把当前项目映射成下面这套心智模型：

```text
web/app.py                  -> Controller / API 层
main.py                     -> Service Facade
graph.py                    -> Flow Orchestrator / Pipeline
agents/*.py                 -> 业务 Service
agents/llm_workspace_agent.py -> Agent Orchestrator
llm_client.py               -> LLM Client / SDK Wrapper
models.py                   -> DTO / VO
config.py                   -> 配置类
db.py                       -> Repository / DAO
web/templates + static      -> 前端页面
```

如果你熟悉 Spring Boot，可以这样对应：

- `@RestController` -> Flask 路由
- `@Service` -> `main.py` 和 `agents/*.py`
- `@ConfigurationProperties` -> `config.py`
- `Entity / DTO / VO` -> `models.py` dataclass
- `Repository` -> `db.py`
- 编排器 / 工作流 -> `graph.py`
- LLM 工具调用控制器 -> `agents/llm_workspace_agent.py`

## 3. Java 开发者必须先补的 Python 知识

如果你直接跳进 `web/app.py`，大概率会被 Python 的写法绊住。先补下面这些基础。

### 3.1 必学语法清单

第一批一定要会：

- 变量、字符串、数字、布尔值
- `list`、`dict`
- `if / elif / else`
- `for`
- 函数定义 `def`
- 模块导入 `import`
- 异常处理 `try / except`

第二批必须补上：

- 类与对象
- `__init__`
- 实例方法里的 `self`
- `@dataclass`
- 类型标注 `str | None`、`list[int]`、`dict[str, Any]`
- 列表推导式
- 上下文管理器 `with`

第三批建议尽快补：

- 虚拟环境 `.venv`
- `pip install`
- 包结构和相对导入
- `pathlib.Path`
- `lambda`
- Python 中的 truthy / falsy

### 3.2 Java 开发者最容易卡住的点

下面这些是最常见的思维差异：

- Python 没有强制 getter / setter，很多属性直接读写
- `dict` 在这个项目里非常常见，不是所有地方都包装成类
- `None` 相当于 Java 里的 `null`
- `if value:` 在 Python 里会顺带判断空字符串、空数组、空字典
- 函数可以返回任意结构，不一定先定义接口再实现
- 类型标注存在，但不是 Java 那种强制编译约束
- 列表和字典字面量非常常用，代码会比 Java 短很多

## 4. 学习前的准备动作

开始前先完成这些动作：

1. 安装 Python 3.10+
2. 在项目根目录安装依赖
3. 复制 `.env.example` 为 `.env`
4. 跑一次 Web 页面
5. 跑一次 CLI

建议命令：

```bash
pip install -r requirements.txt
python web/app.py
python main.py topic --partition knowledge --topic "AI 剪辑效率"
```

你的第一目标不是理解全部代码，而是确认：

- 项目能启动
- 页面能打开
- CLI 能出结果

## 5. 推荐阅读顺序

如果你想最快建立全局认识，按这个顺序读：

1. `config.py`
2. `models.py`
3. `main.py`
4. `graph.py`
5. `web/app.py`
6. `agents/topic_agent.py`
7. `agents/copywriting_agent.py`
8. `agents/optimization_agent.py`
9. `agents/operation_agent.py`
10. `llm_client.py`
11. `agents/llm_workspace_agent.py`
12. `web/templates/index.html`
13. `web/static/app.js`

原因很简单：

- 先看配置和数据模型
- 再看调用入口
- 再看业务逻辑
- 最后看 LLM Agent 和前端

## 6. 14 天学习路线

下面是最推荐的路线。每一天都尽量带着“我要解决什么问题”来学。

### Day 1：把 Python 运行起来

学习目标：

- 会创建和使用虚拟环境
- 会安装依赖
- 会运行 `.py` 文件
- 看懂最基本的 Python 语法

当天要学：

- `print`
- 变量
- 字符串
- `list`
- `dict`
- `if`
- `for`
- `def`

当天要看：

- `requirements.txt`
- `config.py`

当天练习：

- 用 Python 写一个函数，把 `"1,2,3"` 转成 `[1, 2, 3]`
- 对照 `main.py` 的 `parse_up_ids()` 自己写一遍

### Day 2：函数、模块、异常

学习目标：

- 会拆函数
- 会导入模块
- 会处理异常

当天要学：

- `import`
- `from ... import ...`
- `try / except`
- 返回值
- 可选参数

当天要看：

- `main.py`
- `llm_client.py` 前半部分

当天练习：

- 写一个函数，输入视频链接，尝试提取 BV 号
- 为错误输入抛出异常

### Day 3：类、dataclass、类型标注

学习目标：

- 看懂 Python 类
- 看懂 `@dataclass`
- 知道这个项目为什么大量使用 dataclass

当天要学：

- `class`
- `__init__`
- `self`
- `@dataclass`
- `field(default_factory=...)`
- `list[int]`
- `dict[str, Any]`

当天要看：

- `models.py`
- `config.py`

Java 对应理解：

- `@dataclass` 可以类比成“更轻量的 DTO + 构造器”
- 但它不是 Lombok 的完全等价物

当天练习：

- 自己写一个 `VideoInfo` dataclass
- 写一个 `to_dict()` 方法

### Day 4：先理解项目主入口

学习目标：

- 知道项目不是从 Agent 开始读，而是先看入口

当天要看：

- `main.py`
- `graph.py`

你要搞清楚：

- CLI 从哪里进
- Web 复用的是哪些函数
- LangGraph 在这里只是“流程编排器”，不是魔法

当天练习：

- 手动画出 `pipeline` 的执行顺序

当前顺序是：

```text
topic -> copy -> operate -> optimize
```

### Day 5：规则模式下的业务链路

学习目标：

- 看懂没有 Key 时项目怎么跑

当天要看：

- `web/app.py` 里这些函数
- `build_seed_topic`
- `classify_video_performance`
- `build_hot_analysis`
- `build_low_performance_analysis`

你要搞清楚：

- 规则模式如何生成选题
- 规则模式如何判断“热门爆款 / 播放偏低”
- 规则模式如何拼出分析结果

当天练习：

- 手动构造一个 `resolved` 字典，调用 `classify_video_performance()`

### Day 6：TopicAgent 怎么工作

学习目标：

- 看懂选题不是拍脑袋，而是基于 B 站公开样本

当天要看：

- `agents/topic_agent.py`

重点看这些能力：

- 抓全站热榜
- 抓分区样本
- 抓同类 UP 主视频
- 关键词提取
- 选题打分
- 种子主题扩展

你要搞清楚：

- `TopicAgent` 不直接依赖 LLM
- 它输出的是数据化的选题候选

当天练习：

- 改一个 `_pick_video_type()` 的规则，看结果怎么变

### Day 7：Copywriting / Optimization / Operation 三个 Agent

学习目标：

- 看懂老的业务 Agent 还在，而且依然重要

当天要看：

- `agents/copywriting_agent.py`
- `agents/optimization_agent.py`
- `agents/operation_agent.py`

你要搞清楚：

- `CopywritingAgent` 会先构造 fallback，再尝试调用 LLM
- `OptimizationAgent` 会先算规则建议，再尝试 LLM 优化
- `OperationAgent` 在主页面里没有单独入口，但接口还保留

当天练习：

- 分别运行：

```bash
python main.py copy --topic "AI 剪辑第一条视频先拍什么更容易起量" --style 干货
python main.py optimize --bv BV1xx411c7mD
python main.py operate --bv BV1xx411c7mD --dry-run
```

### Day 8：Flask 路由和 Web 主流程

学习目标：

- 看懂 Flask 在这个项目里扮演的角色

当天要看：

- `web/app.py`

先重点看这些路由：

- `/api/runtime-info`
- `/api/resolve-bili-link`
- `/api/module-create`
- `/api/module-analyze`
- `/api/chat`

你要搞清楚：

- 路由只是入口
- 真正逻辑分散在多个函数里
- `web/app.py` 其实更像 Java 项目里的 Controller + Facade + 一部分 Service

当天练习：

- 给 `/api/runtime-info` 返回值多加一个只读字段
- 然后前端把它显示出来

### Day 9：前端页面怎么驱动后端

学习目标：

- 看懂前端不是纯展示，而是有一整套状态机

当天要看：

- `web/templates/index.html`
- `web/static/app.js`

重点关注：

- 模块标签切换
- 自动解析视频链接
- 进度条
- 复制按钮
- 清空结果
- 助手快捷提问
- 助手发送逻辑

你要搞清楚：

- 左侧是两个模块切换，不是两个页面
- 右侧助手独立维护对话历史
- 前端会先调 `/api/runtime-info` 决定当前模式

当天练习：

- 新增一个快捷提问按钮

### Day 10：LLM 客户端封装

学习目标：

- 看懂项目不是直接 everywhere 调 OpenAI SDK

当天要看：

- `llm_client.py`

重点关注：

- `ChatOpenAI` 包装
- `invoke_json`
- `invoke_json_required`
- 错误分类
- 重试逻辑
- JSON 提取逻辑

你要搞清楚：

- 为什么项目统一走 `LLMClient`
- 为什么要区分“可失败 fallback”和“必须成功 required”
- 为什么 provider 慢的时候，超时和重试配置很关键

当天练习：

- 改 `LLM_TIMEOUT_SECONDS`
- 观察错误提示变化

### Day 11：LLMWorkspaceAgent 怎么组织工具调用

学习目标：

- 真正理解“Agent”在这个项目里是什么意思

当天要看：

- `agents/llm_workspace_agent.py`
- `web/app.py` 里的 `get_llm_workspace_agent`

重点关注：

- `AgentTool`
- `allowed_tools`
- `required_tools`
- `required_final_keys`
- `scratchpad`
- `tool_observations`
- `agent_trace`

你要搞清楚：

- 这里不是简单问答模型
- 是一个“让模型决定先调哪个工具，再输出结构化结果”的流程

当天练习：

- 在脑子里模拟一次 `module-create`
- 看它为什么必须先调 `creator_briefing`

### Day 12：视频参考检索和聊天助手

学习目标：

- 看懂现在项目里新增的“参考视频”链路

当天要看：

- `web/app.py` 里这些函数
- `fetch_direct_related_reference_videos`
- `fetch_search_reference_videos`
- `select_reference_videos`
- `extract_reference_links_from_tool_observations`
- `run_llm_chat`

你要搞清楚：

- 参考视频不只来自本地样本
- 还会用当前视频相关推荐和 B 站搜索结果
- 聊天结果里的 `reference_links` 就是从工具观察里提炼出来的

当天练习：

- 跑一次聊天助手
- 看返回里有没有 `reference_links`

### Day 13：做一次最小二次开发

学习目标：

- 不再只是读代码，开始改代码

推荐做 3 选 1：

1. 在内容创作结果里新增一个说明字段
2. 在视频分析结果里新增一个统计字段
3. 在前端再加一个快捷提示词按钮

如果你是 Java 开发者，推荐从第 1 个开始，因为它最像：

- 改 DTO
- 改 Service
- 改 Controller 返回
- 改前端渲染

### Day 14：考虑怎么接进自己的 Java 系统

学习目标：

- 知道正确的集成方式

推荐做法：

- 保留 Python 项目为独立服务
- Java 通过 HTTP 调用它

不推荐：

- 直接把 Python 文件硬塞进 Java 工程
- 试图把 LangGraph 和 Flask 逻辑全部翻译成 Java 再维护两套

如果你自己的系统是 Spring Boot，推荐集成点：

- `POST /api/module-create`
- `POST /api/module-analyze`
- `POST /api/chat`

## 7. 你每天到底该看什么文件

如果你是那种喜欢“今天我到底读哪个文件”的人，按下面来。

### 第 1 阶段：先把 Python 补起来

- `config.py`
- `models.py`
- `main.py`

### 第 2 阶段：先看无 LLM 主流程

- `graph.py`
- `agents/topic_agent.py`
- `agents/copywriting_agent.py`
- `agents/optimization_agent.py`

### 第 3 阶段：再看 Web 层

- `web/app.py`
- `web/templates/index.html`
- `web/static/app.js`

### 第 4 阶段：最后看 LLM Agent

- `llm_client.py`
- `agents/llm_workspace_agent.py`

## 8. Java 到 Python 的常见映射

### 8.1 DTO / VO

Java：

- `class XxxDTO {}`

Python：

- `@dataclass`

项目位置：

- `models.py`

### 8.2 Service

Java：

- `@Service`

Python：

- 普通 class 或普通函数

项目位置：

- `main.py`
- `agents/*.py`

### 8.3 Controller

Java：

- `@RestController`

Python：

- Flask `@app.get` / `@app.post`

项目位置：

- `web/app.py`

### 8.4 Pipeline / Flow

Java：

- 手写流程编排器

Python：

- `LangGraph`

项目位置：

- `graph.py`

### 8.5 Agent Orchestrator

Java 里没有完全一一对应的经典组件。

你可以暂时把它理解成：

- 一个“由 LLM 参与决策的流程控制器”

项目位置：

- `agents/llm_workspace_agent.py`

## 9. Java 开发者最容易踩的坑

### 9.1 以为所有东西都会封装成类

不会。

这个项目大量使用：

- `dict`
- 函数
- dataclass

### 9.2 以为有 Key 后所有接口都统一走 Agent 中枢

不是。

当前只有主界面核心流程切到 `LLMWorkspaceAgent`：

- `/api/module-create`
- `/api/module-analyze`
- `/api/chat`

兼容接口保留旧实现。

### 9.3 以为前端还是旧的交互

不是。

现在真实前端是：

- 左侧模块标签切换
- 视频链接自动解析
- 右侧聊天助手
- 语音输入
- 打字机回复

### 9.4 以为 `config.py` 默认值就是最终运行值

不是。

最终以 `.env` 为准。

## 10. 适合你的学习节奏

如果你平时很忙，可以按下面拆：

### 保底版

- 每天 1 小时
- 学 14 天

### 常规版

- 每天 2 小时
- 学 10 到 14 天

### 冲刺版

- 每天 3 到 4 小时
- 5 到 7 天先跑通并改一次小需求

## 11. 学完后的下一步建议

学完这份教程后，推荐按这个顺序做真实改造：

1. 改一个返回字段
2. 改一个前端按钮
3. 改一个 Agent 规则
4. 新增一个兼容接口
5. 再考虑给 `LLMWorkspaceAgent` 增加新工具

先完成前 3 步，再去改 Agent 工具体系，成功率更高。
