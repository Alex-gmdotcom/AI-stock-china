"""
src/eval/fees.py — A股/港股交易费用模型（纯计算）
======================================================================
对齐审计 🟡1（无摩擦回测）。IC 本身与费用无关；费用只在做
top-k 多空回测、算净收益时才需要。这里先备好，harness 用得上。

⚠️ 费率待核校（同 MarketConfig 里贴现率的处理：给合理默认值，
   标注可调）。数值为 2026 年常见档位，非交易所官方最新，请以
   券商实际费率为准。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AShareFeeModel:
    """A 股沪深主板费用（单位：比例，非百分比）。

    - commission_rate 佣金：双边，默认万 2.5，单笔最低 5 元
    - stamp_duty_rate  印花税：**仅卖出**，2023 年减半后 0.05%
    - transfer_rate    过户费：双边，约万分之 0.1
    """
    commission_rate: float = 0.00025
    commission_min: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_rate: float = 0.00001

    def buy_cost(self, notional: float) -> float:
        comm = max(notional * self.commission_rate, self.commission_min)
        return comm + notional * self.transfer_rate

    def sell_cost(self, notional: float) -> float:
        comm = max(notional * self.commission_rate, self.commission_min)
        return comm + notional * self.stamp_duty_rate + notional * self.transfer_rate

    def round_trip_cost(self, notional: float) -> float:
        return self.buy_cost(notional) + self.sell_cost(notional)

    def round_trip_bps(self, notional: float) -> float:
        """往返成本占本金的 bps（便于和收益对比）。"""
        if notional <= 0:
            return 0.0
        return self.round_trip_cost(notional) / notional * 1e4


@dataclass
class HKFeeModel:
    """港股费用（简化，待核校）。印花税双边 0.1%，另有交易/结算费。"""
    commission_rate: float = 0.0005
    commission_min: float = 3.0        # HKD，示意
    stamp_duty_rate: float = 0.001     # 双边
    trading_fee_rate: float = 0.0000565
    settlement_rate: float = 0.00002

    def _one_side(self, notional: float) -> float:
        comm = max(notional * self.commission_rate, self.commission_min)
        levies = notional * (self.stamp_duty_rate + self.trading_fee_rate + self.settlement_rate)
        return comm + levies

    def round_trip_cost(self, notional: float) -> float:
        return 2 * self._one_side(notional)

    def round_trip_bps(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        return self.round_trip_cost(notional) / notional * 1e4


def fee_model_for(ticker: str):
    """按 ticker 后缀路由费用模型（与 MarketConfig 同思路：按市场路由）。"""
    t = ticker.upper()
    if t.endswith(".HK"):
        return HKFeeModel()
    return AShareFeeModel()
