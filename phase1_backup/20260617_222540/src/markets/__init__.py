"""
Market abstraction layer for multi-market support.

Provides ticker parsing, market detection, and market-specific
trading rule configurations for CN (A-share / ChiNext / STAR)
and HK (HKEX) markets.
"""

from src.markets.ticker import (
    MarketType,
    TickerInfo,
    parse_ticker,
    normalize_ticker,
    detect_market,
    is_china_ticker,
    is_hk_ticker,
)
from src.markets.config import (
    MarketConfig,
    get_market_config,
    MARKET_CONFIGS,
)

__all__ = [
    "MarketType",
    "TickerInfo",
    "parse_ticker",
    "normalize_ticker",
    "detect_market",
    "is_china_ticker",
    "is_hk_ticker",
    "MarketConfig",
    "get_market_config",
    "MARKET_CONFIGS",
]
