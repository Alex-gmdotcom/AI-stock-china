# -*- coding: utf-8 -*-
"""
datasource/base.py — Provider 抽象 + 能力矩阵 (Phase 1)
=====================================================
Provider 只负责"取数 + 归一化",不管路由/熔断/缓存(那是 Router 的事)。
用 CallableProvider 可把现有函数(baostock_data / akshare / quote)注入成 Provider,
无需重写。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Optional


class FieldGroup(str, Enum):
    """字段组:按"哪些字段一起取、由哪些源服务"切分。"""
    PRICE = "price"              # OHLCV / 前复权日线
    VALUATION = "valuation"     # PE / PB / PS / 市值 / 换手
    RATIO_FIN = "ratio_fin"     # 净利率/ROE/周转/杜邦 等比率
    ABS_BALANCE = "abs_balance"   # 营收/总资产/权益 等绝对值
    ABS_CASHFLOW = "abs_cashflow" # capex/折旧/分红/经营现金流 绝对值
    NEWS = "news"
    FLOW = "flow"               # 北向/融资融券/主力


# 字段组 → Provider 上的取数方法名(多个组可共用一个方法,如财报三组都走 financials)
GROUP_METHOD: dict[FieldGroup, str] = {
    FieldGroup.PRICE: "prices",
    FieldGroup.VALUATION: "valuation",
    FieldGroup.RATIO_FIN: "financials",
    FieldGroup.ABS_BALANCE: "financials",
    FieldGroup.ABS_CASHFLOW: "financials",
    FieldGroup.NEWS: "news",
    FieldGroup.FLOW: "flow",
}

# 接入机制档:故障按档聚集,失效转移优先跳到"不同档"
TIER_A = "A"   # socket / 协议原生(Baostock, mootdx)—— 无反爬
TIER_B = "B"   # 官方 REST + token(Tushare 等)—— 限频
TIER_C = "C"   # web JSON(腾讯/东财/新浪/百度)—— 反爬风险
TIER_D = "D"   # 浏览器自动化 —— 慢、不可并行


class Provider(ABC):
    """数据源抽象。子类声明 name / capabilities / tier,实现 serve()。"""
    name: str = "base"
    capabilities: set[FieldGroup] = set()
    tier: str = TIER_C
    needs_cn_ip: bool = False

    def available(self) -> bool:
        """源是否就绪(如依赖包已装、token 已配)。默认 True。"""
        return True

    def can_serve(self, group: FieldGroup) -> bool:
        return group in self.capabilities

    @abstractmethod
    def serve(self, method: str, **kwargs) -> Any:
        """执行取数。method ∈ {prices,valuation,financials,news,flow}。
        返回归一化结果(list/dict);无数据返回空(不是异常)。
        连接级失败应 raise(由 Router 记熔断)。"""
        raise NotImplementedError


class CallableProvider(Provider):
    """把一组注入的可调用对象包成 Provider。

    methods: {"prices": fn, "valuation": fn, "financials": fn, ...}
    每个 fn 的签名由 Router 传入的 kwargs 决定(见 router._call_args)。
    这样 api_china 可以把它已有的 baostock_data / _orig_* / quote 直接注册,
    不必重写实现。
    """
    def __init__(
        self,
        name: str,
        tier: str,
        capabilities: set[FieldGroup] | list[FieldGroup],
        methods: dict[str, Callable],
        available_fn: Optional[Callable[[], bool]] = None,
        needs_cn_ip: bool = False,
    ):
        self.name = name
        self.tier = tier
        self.capabilities = set(capabilities)
        self.needs_cn_ip = needs_cn_ip
        self._methods = dict(methods)
        self._available_fn = available_fn

    def available(self) -> bool:
        if self._available_fn is None:
            return True
        try:
            return bool(self._available_fn())
        except Exception:
            return False

    def serve(self, method: str, **kwargs) -> Any:
        fn = self._methods.get(method)
        if fn is None:
            # 该源不提供这个方法 → 视作"无数据",让 Router 跳下一个(不算故障)
            return None
        return fn(**kwargs)

    def __repr__(self):
        caps = ",".join(sorted(g.value for g in self.capabilities))
        return f"<CallableProvider {self.name} tier={self.tier} caps=[{caps}]>"
