"""路由辅助模块

本模块为 Bilibili 工作流提供任务路由功能，根据请求内容自动识别任务类型、
确定内容分区，并生成相应的执行策略。

主要功能:
    - RouterChain: 核心路由类，用于分析请求负载并生成路由决策
    - route_request: 便捷的路由函数，接收请求返回标准化决策

使用方式:
    from chains import RouterChain, route_request

    decision = route_request({"bv_id": "BV123", "partition": "tech"})
"""

from .router_chain import RouterChain, RouteDecision, route_request
