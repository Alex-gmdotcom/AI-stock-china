"""
markets/market_config.py — 按市场路由的资本成本/贴现率参数。

背景:上游 valuation 把无风险利率 / required_return / cost_of_equity 写死成美股口径
(risk_free 4.5% / required_return 15% / cost_of_equity 10%),对 A 股系统性低估内在值
→ valuation 系统性偏空。本模块把这些参数按市场(CN/HK/US)路由,与 api_bridge 按市场
路由数据源同一思路 —— 修类不修例。

CN 初值依据:中国 10Y 国债 ~1.7-1.8% (2026-06),A 股 ERP ~5-6% → CAPM 权益成本 ~7.3%;
            业主收益门槛由美股 15% 降至 10%。
US 保留上游原值(零回归)。HK 用 USD-linked 口径(港币锚美元)。
⚠️ 数值为可调参数,非投资建议,待核校。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
__version__ = "1.0.0"


@dataclass(frozen=True)
class MarketConfig:
    """某市场的资本成本参数。所有 valuation 贴现率从这里读,不再写死美股值。"""
    market: str
    risk_free_rate: float        # → calculate_wacc(risk_free_rate=)
    equity_risk_premium: float   # → calculate_wacc(market_risk_premium=)
    required_return: float       # → calculate_owner_earnings_value(required_return=)
    cost_of_equity: float        # → calculate_residual_income_value(cost_of_equity=)


# CN: 10Y≈1.8%, ERP≈5.5% → CAPM 权益成本≈7.3%;业主收益门槛降至 10%
_CN = MarketConfig("CN", risk_free_rate=0.018, equity_risk_premium=0.055,
                   required_return=0.10, cost_of_equity=0.075)
# HK: 港币锚美元 → 无风险接近美元口径
_HK = MarketConfig("HK", risk_free_rate=0.040, equity_risk_premium=0.060,
                   required_return=0.12, cost_of_equity=0.100)
# US: 上游原值,零回归
_US = MarketConfig("US", risk_free_rate=0.045, equity_risk_premium=0.060,
                   required_return=0.15, cost_of_equity=0.100)


def for_ticker(ticker: str) -> MarketConfig:
    """按 ticker 解析市场 → 对应资本成本配置。无法解析时退回 US(零回归)。"""
    try:
        from src.markets.ticker import parse_ticker
        m = parse_ticker(ticker).market
        if m.is_china:
            return _CN
        if m.is_hk:
            return _HK
    except Exception as exc:  # 解析失败不应拖垮估值,退回 US 原口径
        logger.debug("MarketConfig.for_ticker(%s) 退回 US: %s", ticker, str(exc)[:80])
    return _US


if __name__ == "__main__":
    for t in ("688019.SH", "002594.SZ", "300308.SZ", "09880.HK", "AAPL", "garbage"):
        c = for_ticker(t)
        print(f"{t:12s} → {c.market}  rf={c.risk_free_rate}  req={c.required_return}  coe={c.cost_of_equity}")
