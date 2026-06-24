"""
Market abstraction layer for multi-market support.
Provides ticker parsing, market detection, and market-specific
trading rule configurations for CN (A-share / ChiNext / STAR)
and HK (HKEX) markets.

===========================================================================
Phase 1 过渡说明：
    ticker.py 和 config.py 是 Phase 2 才实施的子模块。在它们就位前，
    本 __init__.py 用 try/except 让 markets 包能正常加载（保证 proxy.py
    可用），同时只把成功加载的 symbol 暴露在 __all__ 里。

    Phase 2 实施 ticker.py / config.py 后，两个 try 自动成功，所有
    symbol 都会被暴露，本文件不需要改动。
===========================================================================
"""

__all__ = []

# === Phase 1 ===
try:
    from src.markets.ticker import (
        MarketType,
        TickerInfo,
        parse_ticker,
        normalize_ticker,
        detect_market,
        is_china_ticker,
        is_hk_ticker,
    )
    __all__ += [
        "MarketType",
        "TickerInfo",
        "parse_ticker",
        "normalize_ticker",
        "detect_market",
        "is_china_ticker",
        "is_hk_ticker",
    ]
except ImportError:
    # Phase 1: ticker.py 尚未实施，跳过暴露
    pass

try:
    from src.markets.config import (
        MarketConfig,
        get_market_config,
        MARKET_CONFIGS,
    )
    __all__ += [
        "MarketConfig",
        "get_market_config",
        "MARKET_CONFIGS",
    ]
except ImportError:
    # Phase 1: config.py 尚未实施，跳过暴露
    pass
