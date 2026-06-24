# -*- coding: utf-8 -*-
"""
datasource/router.py — 按字段组失效转移 (Phase 1)
================================================
每个字段组持一条有序 Provider 链。resolve 依次尝试:
  - 源不可用 / 熔断打开 / 缺能力 / 需国内IP但当前非国内 → 跳过
  - 调用抛异常 → 记熔断失败 + 健康,继续下一个
  - 返回空(无数据)→ 记健康(非故障),继续下一个
  - 返回非空 → 记成功,返回 (data, source_name)
全链落空 → (None, None),由上层 fail-soft。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from .base import FieldGroup, GROUP_METHOD, Provider
from .breaker import CircuitBreaker
from .health import HealthReporter


# 默认优先级链(可在实例上覆盖)。只列 Phase 1 收编的源 + 预留扩展位。
DEFAULT_CHAINS: dict[FieldGroup, list[str]] = {
    FieldGroup.PRICE:        ["baostock", "tencent", "akshare"],
    FieldGroup.VALUATION:    ["tencent", "baostock", "akshare"],
    FieldGroup.RATIO_FIN:    ["baostock", "akshare"],
    FieldGroup.ABS_BALANCE:  ["baostock", "akshare"],
    FieldGroup.ABS_CASHFLOW: ["akshare"],          # Baostock 无;Phase 2 加 tushare 到首位
    FieldGroup.NEWS:         ["eastmoney", "tencent"],
    FieldGroup.FLOW:         ["eastmoney"],
}


class DataSourceRouter:
    def __init__(
        self,
        breaker: Optional[CircuitBreaker] = None,
        health: Optional[HealthReporter] = None,
        chains: Optional[dict] = None,
        is_cn_ip: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ):
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self.health = health or HealthReporter(clock=clock)
        self.chains = {k: list(v) for k, v in (chains or DEFAULT_CHAINS).items()}
        self.is_cn_ip = is_cn_ip
        self._clock = clock
        self.providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self.providers[provider.name] = provider

    def set_chain(self, group: FieldGroup, names: list[str]) -> None:
        self.chains[group] = list(names)

    def resolve(self, group: FieldGroup, **kwargs) -> tuple[Any, Optional[str]]:
        """对一个字段组取数,返回 (data, source_name)。全链落空 → (None, None)。"""
        method = GROUP_METHOD[group]
        for name in self.chains.get(group, []):
            p = self.providers.get(name)
            if p is None or not p.available():
                continue
            if not p.can_serve(group):
                continue
            if p.needs_cn_ip and not self.is_cn_ip():
                continue
            if self.breaker.is_open(name):
                continue
            t0 = self._clock()
            try:
                res = p.serve(method, **kwargs)
            except Exception as exc:
                self.breaker.record_failure(name)
                self.health.report(name, group, ok=False,
                                   latency_ms=(self._clock() - t0) * 1000.0,
                                   note=str(exc)[:120])
                continue
            self.breaker.record_success(name)
            lat = (self._clock() - t0) * 1000.0
            if res:
                self.health.report(name, group, ok=True, latency_ms=lat)
                return res, name
            # 非空判定:空 list/dict/None 都算"无数据",转下一个源(不算故障)
            self.health.report(name, group, ok=True, latency_ms=lat, note="empty")
        return None, None

    def status(self) -> dict:
        return {"breaker": self.breaker.snapshot(), "health": self.health.snapshot()}
