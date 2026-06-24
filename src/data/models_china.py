"""
China and Hong Kong market specific data models.

Extends the base models in src/data/models.py with fields unique
to CN/HK markets (northbound capital flow, margin trading,
sector/concept classification, policy events, etc.).
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ─────────────────────────────────────────────
# Capital flow data (unique to CN markets)
# ─────────────────────────────────────────────

class NorthboundFlow(BaseModel):
    """
    沪深港通北向资金 daily snapshot.
    Northbound (HK→CN) capital flow is one of the strongest
    short-term sentiment indicators for A-shares.
    """
    date: str
    sh_net_buy: float | None = None    # 沪股通净买入 (亿元)
    sz_net_buy: float | None = None    # 深股通净买入 (亿元)
    total_net_buy: float | None = None # 北向合计净买入 (亿元)
    sh_buy_volume: float | None = None
    sz_buy_volume: float | None = None


class MarginTrading(BaseModel):
    """
    融资融券 daily data for a specific stock or market-wide.
    Margin balance trends indicate leveraged sentiment.
    """
    date: str
    ticker: str | None = None
    margin_balance: float | None = None        # 融资余额 (元)
    margin_buy: float | None = None            # 融资买入额 (元)
    margin_repay: float | None = None          # 融资偿还额 (元)
    short_balance: float | None = None         # 融券余额 (元)
    short_sell_volume: float | None = None     # 融券卖出量 (股)
    short_repay_volume: float | None = None    # 融券偿还量 (股)


class MainCapitalFlow(BaseModel):
    """
    主力资金流向 for a specific stock.
    Tracks large-order (主力/超大单/大单) net inflow.
    """
    date: str
    ticker: str
    main_net_inflow: float | None = None       # 主力净流入 (元)
    super_large_net: float | None = None       # 超大单净流入
    large_net: float | None = None             # 大单净流入
    medium_net: float | None = None            # 中单净流入
    small_net: float | None = None             # 小单净流入
    main_net_inflow_pct: float | None = None   # 主力净流入占比 (%)


# ─────────────────────────────────────────────
# Sector / concept classification
# ─────────────────────────────────────────────

class SectorInfo(BaseModel):
    """Stock's sector and concept tag membership."""
    ticker: str
    industry: str | None = None            # 申万行业分类
    concepts: list[str] = Field(default_factory=list)  # 概念板块列表
    region: str | None = None              # 地域板块


class SectorPerformance(BaseModel):
    """Daily sector performance for rotation analysis."""
    date: str
    sector_name: str
    change_pct: float | None = None        # 涨跌幅 (%)
    turnover_rate: float | None = None     # 换手率 (%)
    net_inflow: float | None = None        # 板块资金净流入 (元)
    leading_stock: str | None = None       # 领涨股
    stock_count_up: int | None = None      # 上涨家数
    stock_count_down: int | None = None    # 下跌家数


# ─────────────────────────────────────────────
# Policy / news / public opinion
# ─────────────────────────────────────────────

class PolicyEvent(BaseModel):
    """
    A policy event or regulatory announcement.
    Policy is the single most important factor in A-share markets.
    """
    date: str
    source: str                             # 来源 (国务院/央行/证监会/发改委/etc.)
    title: str
    summary: str | None = None
    url: str | None = None
    event_type: str | None = None           # monetary/fiscal/regulatory/industry
    affected_sectors: list[str] = Field(default_factory=list)
    sentiment: Literal["positive", "negative", "neutral"] | None = None


class PublicOpinionItem(BaseModel):
    """
    A single public opinion / news item from any source.
    Used by the China public opinion agent to assess
    pre-market sentiment and black-swan risk.
    """
    date: str
    source: str                             # 财联社/雪球/东方财富/微博/etc.
    title: str
    content: str | None = None
    url: str | None = None
    author: str | None = None
    heat_score: float | None = None         # Popularity/engagement metric
    sentiment: Literal["positive", "negative", "neutral"] | None = None
    is_black_swan: bool = False             # Flagged as potential black swan


class BlackSwanAlert(BaseModel):
    """Structured output from the black swan detection logic."""
    detected: bool = False
    event_description: str | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    affected_tickers: list[str] = Field(default_factory=list)
    affected_sectors: list[str] = Field(default_factory=list)
    historical_analog: str | None = None    # Reference to similar past events
    recommended_action: str | None = None


# ─────────────────────────────────────────────
# HK-specific models
# ─────────────────────────────────────────────

class SouthboundFlow(BaseModel):
    """
    南向资金 (CN→HK) flow for Hong Kong stocks.
    Mirror of NorthboundFlow for the other direction.
    """
    date: str
    total_net_buy: float | None = None     # 南向合计净买入 (亿港元)
    sh_net_buy: float | None = None        # 港股通(沪)净买入
    sz_net_buy: float | None = None        # 港股通(深)净买入


class AHPremium(BaseModel):
    """
    A/H premium data for dual-listed stocks.
    AH premium index > 100 means A-shares are more expensive.
    """
    date: str
    ticker_a: str
    ticker_h: str
    company_name: str
    price_a: float | None = None
    price_h: float | None = None       # Converted to CNY
    premium_pct: float | None = None   # (A-H)/H * 100


# ─────────────────────────────────────────────
# Aggregated analysis outputs
# ─────────────────────────────────────────────

class ChinaSentimentOutput(BaseModel):
    """Structured output of the China public opinion agent."""
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100)
    reasoning: dict
    black_swan: BlackSwanAlert | None = None
    market_temperature: Literal["extreme_fear", "fear", "neutral", "greed", "extreme_greed"] | None = None


class CapitalFlowOutput(BaseModel):
    """Structured output of the capital flow agent."""
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100)
    reasoning: dict
    northbound_trend: Literal["inflow", "outflow", "neutral"] | None = None
    margin_trend: Literal["increasing", "decreasing", "stable"] | None = None


class SectorRotationOutput(BaseModel):
    """Structured output of the sector rotation agent."""
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(ge=0, le=100)
    reasoning: dict
    hot_sectors: list[str] = Field(default_factory=list)
    cold_sectors: list[str] = Field(default_factory=list)
    rotation_phase: str | None = None  # e.g. "early_cycle", "mid_cycle", "late_cycle"
