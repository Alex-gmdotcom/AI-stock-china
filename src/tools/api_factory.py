"""
Data provider factory.

Automatically routes API calls to the correct data provider
based on the ticker's detected market:
  - US tickers  → financialdatasets.ai (original api.py)
  - CN tickers  → AKShare (api_china.py)
  - HK tickers  → AKShare (api_china.py)

This allows all existing agents to work transparently with
any market — they call the factory functions instead of
importing api.py or api_china.py directly.

Usage:
    from src.tools.api_factory import get_data_provider
    provider = get_data_provider("600519.SH")
    prices = provider.get_prices("600519.SH", "2024-01-01", "2024-12-31")
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Any

from src.markets.ticker import parse_ticker, TickerInfo


@dataclass
class DataProvider:
    """
    Unified data provider interface.

    Wraps market-specific API modules behind a common interface
    so agents don't need to know which data source they're using.
    """
    name: str
    get_prices: Callable
    get_financial_metrics: Callable
    get_company_news: Callable
    get_market_cap: Callable
    prices_to_df: Callable
    get_price_data: Callable

    # Optional China-specific (None for US provider)
    get_northbound_flow: Callable | None = None
    get_margin_trading: Callable | None = None
    get_main_capital_flow: Callable | None = None
    get_sector_performance: Callable | None = None
    get_public_opinion: Callable | None = None

    # Original API-specific (None for China provider)
    get_insider_trades: Callable | None = None
    search_line_items: Callable | None = None


def _load_us_provider() -> DataProvider:
    """Load the original US market data provider."""
    from src.tools import api
    return DataProvider(
        name="financialdatasets.ai (US)",
        get_prices=api.get_prices,
        get_financial_metrics=api.get_financial_metrics,
        get_company_news=api.get_company_news,
        get_market_cap=api.get_market_cap,
        prices_to_df=api.prices_to_df,
        get_price_data=api.get_price_data,
        get_insider_trades=api.get_insider_trades,
        search_line_items=api.search_line_items,
    )


def _load_china_provider() -> DataProvider:
    """Load the China/HK market data provider."""
    from src.tools import api_china
    return DataProvider(
        name="AKShare (CN/HK)",
        get_prices=api_china.get_prices,
        get_financial_metrics=api_china.get_financial_metrics,
        get_company_news=api_china.get_company_news,
        get_market_cap=api_china.get_market_cap,
        prices_to_df=api_china.prices_to_df,
        get_price_data=api_china.get_price_data,
        get_northbound_flow=api_china.get_northbound_flow,
        get_margin_trading=api_china.get_margin_trading,
        get_main_capital_flow=api_china.get_main_capital_flow,
        get_sector_performance=api_china.get_sector_performance,
        get_public_opinion=api_china.get_public_opinion,
    )


# Singleton cache
_providers: dict[str, DataProvider] = {}


def get_data_provider(ticker: str) -> DataProvider:
    """
    Get the appropriate data provider for a ticker.

    Args:
        ticker: Stock ticker (e.g. "AAPL", "600519.SH", "00700.HK")

    Returns:
        DataProvider instance with the correct API bindings.
    """
    info = parse_ticker(ticker)

    if info.market.is_china or info.market.is_hk:
        key = "china"
        if key not in _providers:
            _providers[key] = _load_china_provider()
        return _providers[key]
    else:
        key = "us"
        if key not in _providers:
            _providers[key] = _load_us_provider()
        return _providers[key]


def get_all_providers_for_tickers(tickers: list[str]) -> dict[str, DataProvider]:
    """
    Given a list of tickers, return a {ticker: provider} mapping.
    Useful for the main loop where we iterate over tickers.
    """
    return {t: get_data_provider(t) for t in tickers}


def detect_market_mode(tickers: list[str]) -> str:
    """
    Detect whether we're operating in US, CN, HK, or mixed mode.
    Returns: "us", "cn", "hk", or "mixed"
    """
    markets = set()
    for t in tickers:
        info = parse_ticker(t)
        if info.market.is_china:
            markets.add("cn")
        elif info.market.is_hk:
            markets.add("hk")
        else:
            markets.add("us")

    if len(markets) == 1:
        return markets.pop()
    return "mixed"
