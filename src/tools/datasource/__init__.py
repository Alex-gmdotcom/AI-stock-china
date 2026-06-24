# -*- coding: utf-8 -*-
"""
datasource — A 股数据源抽象层 (Phase 1, v1.0.0)
================================================
按"字段组 + 能力矩阵 + 熔断 + 健康"路由多数据源,消除单点。
详见 data_layer_architecture.md。

- base   : FieldGroup / Provider / CallableProvider
- breaker: CircuitBreaker(熔断器)
- health : HealthReporter(成功率/延迟/告警)
- router : DataSourceRouter(按字段组失效转移)
"""
from .base import FieldGroup, Provider, CallableProvider
from .breaker import CircuitBreaker
from .health import HealthReporter
from .router import DataSourceRouter

__all__ = ["FieldGroup", "Provider", "CallableProvider",
           "CircuitBreaker", "HealthReporter", "DataSourceRouter"]
__version__ = "1.0.0"
