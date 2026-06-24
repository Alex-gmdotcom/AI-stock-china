"""
Market-specific trading rules and configuration.

Each market has distinct rules for price limits, settlement cycles,
trading hours, short-selling availability, and lot sizes that
fundamentally affect trading strategy and risk management.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from src.markets.ticker import MarketType


@dataclass(frozen=True)
class MarketConfig:
    """Trading rules and constraints for a specific market."""
    market: MarketType
    name: str

    # Price limits (daily)
    price_limit_pct: float              # e.g. 0.10 for ±10%
    price_limit_st_pct: float | None    # ST stocks (CN only), None if N/A
    has_price_limit: bool = True

    # Settlement
    settlement_cycle: str = "T+1"       # T+0 or T+1
    can_sell_same_day: bool = False      # T+0 markets allow same-day sell

    # Short selling
    short_selling_available: bool = False
    short_selling_notes: str = ""

    # Lot size
    min_lot_size: int = 100             # Minimum trading unit (shares)

    # Trading hours (local time, for display/scheduling)
    trading_hours: list[str] = field(default_factory=list)

    # Currency
    currency: str = "CNY"

    # Special flags
    has_st_mechanism: bool = False      # ST/ST* risk warning mechanism
    has_registration_system: bool = False  # 注册制 (vs 核准制)


# ─────────────────────────────────────────────
# Market configurations
# ─────────────────────────────────────────────

_CN_MAIN_CONFIG = MarketConfig(
    market=MarketType.CN_MAIN,
    name="Shanghai Main Board (沪市主板)",
    price_limit_pct=0.10,
    price_limit_st_pct=0.05,
    has_price_limit=True,
    settlement_cycle="T+1",
    can_sell_same_day=False,
    short_selling_available=False,
    short_selling_notes="融券做空仅限融资融券标的，门槛高且券源稀缺",
    min_lot_size=100,
    trading_hours=["09:30-11:30", "13:00-15:00"],
    currency="CNY",
    has_st_mechanism=True,
    has_registration_system=False,
)

_CN_SZ_CONFIG = MarketConfig(
    market=MarketType.CN_SZ,
    name="Shenzhen Main Board (深市主板)",
    price_limit_pct=0.10,
    price_limit_st_pct=0.05,
    has_price_limit=True,
    settlement_cycle="T+1",
    can_sell_same_day=False,
    short_selling_available=False,
    short_selling_notes="融券做空仅限融资融券标的，门槛高且券源稀缺",
    min_lot_size=100,
    trading_hours=["09:30-11:30", "13:00-15:00"],
    currency="CNY",
    has_st_mechanism=True,
    has_registration_system=False,
)

_CN_CHINEXT_CONFIG = MarketConfig(
    market=MarketType.CN_CHINEXT,
    name="ChiNext (创业板)",
    price_limit_pct=0.20,
    price_limit_st_pct=0.20,
    has_price_limit=True,
    settlement_cycle="T+1",
    can_sell_same_day=False,
    short_selling_available=False,
    short_selling_notes="创业板注册制股票上市前5日不设涨跌幅限制",
    min_lot_size=100,
    trading_hours=["09:30-11:30", "13:00-15:00"],
    currency="CNY",
    has_st_mechanism=True,
    has_registration_system=True,
)

_CN_STAR_CONFIG = MarketConfig(
    market=MarketType.CN_STAR,
    name="STAR Market (科创板)",
    price_limit_pct=0.20,
    price_limit_st_pct=0.20,
    has_price_limit=True,
    settlement_cycle="T+1",
    can_sell_same_day=False,
    short_selling_available=False,
    short_selling_notes="科创板股票上市前5日不设涨跌幅限制；50万资产+2年经验门槛",
    min_lot_size=200,  # 科创板最低200股，超出部分以1股为单位递增
    trading_hours=["09:30-11:30", "13:00-15:00"],
    currency="CNY",
    has_st_mechanism=False,
    has_registration_system=True,
)

_HK_CONFIG = MarketConfig(
    market=MarketType.HK,
    name="HKEX (港股)",
    price_limit_pct=0.0,           # No daily price limit
    price_limit_st_pct=None,
    has_price_limit=False,
    settlement_cycle="T+2",
    can_sell_same_day=True,        # HK allows T+0
    short_selling_available=True,
    short_selling_notes="可沽空指定证券名单内的股票",
    min_lot_size=100,              # Varies by stock (board lot)
    trading_hours=["09:30-12:00", "13:00-16:00"],
    currency="HKD",
    has_st_mechanism=False,
    has_registration_system=False,
)

_US_CONFIG = MarketConfig(
    market=MarketType.US,
    name="US Equities",
    price_limit_pct=0.0,
    price_limit_st_pct=None,
    has_price_limit=False,
    settlement_cycle="T+1",
    can_sell_same_day=True,
    short_selling_available=True,
    short_selling_notes="Standard short selling with margin account",
    min_lot_size=1,
    trading_hours=["09:30-16:00 ET"],
    currency="USD",
    has_st_mechanism=False,
    has_registration_system=False,
)


MARKET_CONFIGS: dict[MarketType, MarketConfig] = {
    MarketType.CN_MAIN: _CN_MAIN_CONFIG,
    MarketType.CN_SZ: _CN_SZ_CONFIG,
    MarketType.CN_CHINEXT: _CN_CHINEXT_CONFIG,
    MarketType.CN_STAR: _CN_STAR_CONFIG,
    MarketType.HK: _HK_CONFIG,
    MarketType.US: _US_CONFIG,
}


def get_market_config(market: MarketType) -> MarketConfig:
    """Get the trading rules for a given market."""
    config = MARKET_CONFIGS.get(market)
    if config is None:
        raise ValueError(f"No configuration for market: {market}")
    return config


def get_risk_context(market: MarketType) -> str:
    """
    Generate a natural-language risk context string for LLM prompts.

    This is injected into agent system prompts so the LLM understands
    the specific constraints of the market it's analyzing.
    """
    cfg = get_market_config(market)
    lines = [f"Market: {cfg.name}"]

    if cfg.has_price_limit:
        lines.append(
            f"Daily price limit: ±{cfg.price_limit_pct:.0%}"
            + (f" (ST stocks: ±{cfg.price_limit_st_pct:.0%})" if cfg.price_limit_st_pct else "")
        )
    else:
        lines.append("No daily price limit.")

    lines.append(f"Settlement: {cfg.settlement_cycle}")

    if not cfg.can_sell_same_day:
        lines.append("Cannot sell same-day purchases (T+1 constraint).")

    if not cfg.short_selling_available:
        lines.append(f"Short selling: restricted. {cfg.short_selling_notes}")
    else:
        lines.append(f"Short selling: available. {cfg.short_selling_notes}")

    lines.append(f"Minimum lot size: {cfg.min_lot_size} shares")
    lines.append(f"Currency: {cfg.currency}")

    if cfg.has_st_mechanism:
        lines.append("ST mechanism: stocks with financial risk are marked ST/*ST with tighter price limits.")

    if cfg.has_registration_system:
        lines.append("Registration-based IPO system (注册制) — higher new-listing volatility.")

    return "\n".join(lines)
