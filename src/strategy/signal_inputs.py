# -*- coding: utf-8 -*-
"""
signal_inputs.py — Step 18c 数据接线层(引擎输入采集)
================================================================
职责: 为 migration_signals.evaluate_pool 采集每票输入。
纪律:
  - 按池类别只取所需字段(V:ret_5d / T:个股融资余额+板块排名 / N:净利YoY+ret_20d)
  - 一切静默失败路径必须打 warning 面包屑; 取不到 -> None -> 引擎灰灯(I1.1)
  - fetchers 可注入 -> 沙箱确定性单测零网络
  - S2 融资余额 = **个股** rzye(tushare margin_detail, 裁决⑦口径);
    市场合计 get_margin_trading 不用于 S2(无个股区分度)
  - 板块排名 sector_rank_change_5d: 本版未接入(口径=申万L1排名位移, 下一补丁),
    显式返回 None -> T 池 S2 灰灯属设计内行为
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

_logger = logging.getLogger(__name__)

MARGIN_LOOKBACK_CAL_DAYS = 60   # 取 ~25+ 个交易日融资余额
PRICE_LOOKBACK_CAL_DAYS = 60    # 取 ~21+ 根收盘价


# ---------------------------------------------------------------
# 默认 fetchers(懒加载真实数据层; 单测注入 stub 替换)
# ---------------------------------------------------------------
def _default_fetch_closes(ticker: str, asof_iso: str) -> list | None:
    try:
        try:
            from src.tools import api_china
        except ImportError:
            import api_china  # type: ignore
        start = (date.fromisoformat(asof_iso)
                 - timedelta(days=PRICE_LOOKBACK_CAL_DAYS)).isoformat()
        prices = api_china.get_prices(ticker, start, asof_iso)
        closes = [p.close for p in (prices or [])
                  if getattr(p, "close", None) not in (None, 0, 0.0)]
        if not closes:
            _logger.warning("18c inputs: %s 收盘序列为空(%s..%s)", ticker, start, asof_iso)
            return None
        return closes
    except Exception as exc:
        _logger.warning("18c inputs: %s 价格采集失败: %s", ticker, exc)
        return None


def _default_fetch_stock_margin(ticker: str, asof_iso: str) -> list | None:
    """个股融资余额(rzye)升序序列; 港股/无数据 -> None."""
    if ticker.upper().endswith(".HK"):
        return None  # 融资余额口径不适用港股, 引擎会给灰灯
    try:
        try:
            from src.tools import tushare_data as tsd
        except ImportError:
            import tushare_data as tsd  # type: ignore
        if not tsd.available():
            _logger.warning("18c inputs: tushare 不可用, %s 融资余额缺失", ticker)
            return None
        asof = date.fromisoformat(asof_iso)
        start = (asof - timedelta(days=MARGIN_LOOKBACK_CAL_DAYS)).strftime("%Y%m%d")
        df = tsd._query("margin_detail", ts_code=ticker,
                        start_date=start, end_date=asof.strftime("%Y%m%d"))
        if df is None or len(df) == 0:
            _logger.warning("18c inputs: %s margin_detail 空返回", ticker)
            return None
        df = df.sort_values("trade_date")
        vals = [float(v) for v in df["rzye"].tolist() if v is not None]
        # 值层判空: 有行但全 None 也是缺口(有壳无肉, I1.1)
        if not vals:
            _logger.warning("18c inputs: %s margin_detail 有壳无肉(rzye 全空)", ticker)
            return None
        return vals[-25:]
    except Exception as exc:
        _logger.warning("18c inputs: %s 融资余额采集失败: %s", ticker, exc)
        return None


def _default_fetch_sector_rank_change(ticker: str, asof_iso: str):
    # 口径未接入(申万L1排名位移, 下一补丁) -> None -> S2 灰灯
    return None


def _default_fetch_earnings_yoy(ticker: str, asof_iso: str):
    try:
        try:
            from src.tools import api_china
        except ImportError:
            import api_china  # type: ignore
        recs = api_china.get_financial_metrics(ticker, asof_iso, period="ttm", limit=4)
        for rec in recs or []:
            g = rec.get("earnings_growth") if isinstance(rec, dict) \
                else getattr(rec, "earnings_growth", None)
            if g is not None:
                return float(g)
        _logger.warning("18c inputs: %s earnings_growth 缺失", ticker)
        return None
    except Exception as exc:
        _logger.warning("18c inputs: %s 财务采集失败: %s", ticker, exc)
        return None


DEFAULT_FETCHERS = {
    "closes": _default_fetch_closes,
    "stock_margin": _default_fetch_stock_margin,
    "sector_rank_change": _default_fetch_sector_rank_change,
    "earnings_yoy": _default_fetch_earnings_yoy,
}


# ---------------------------------------------------------------
def _ret(closes: list | None, n: int):
    """近 n 个交易日收益; 数据不足 -> None(不补0, R2)."""
    if not closes or len(closes) < n + 1:
        return None
    base = closes[-(n + 1)]
    if not base:
        return None
    return closes[-1] / base - 1.0


def collect_signal_inputs(pool: dict, asof_iso: str, fetchers: dict | None = None) -> dict:
    """
    pool: {ticker: "V"/"T"/"N"}
    返回 data: {ticker: {...}} 直接喂 evaluate_pool。
    """
    f = dict(DEFAULT_FETCHERS)
    if fetchers:
        f.update(fetchers)
    data: dict = {}
    for ticker, cls in pool.items():
        d: dict = {}
        if cls == "V":
            d["ret_5d"] = _ret(f["closes"](ticker, asof_iso), 5)
        elif cls == "T":
            d["margin_balance"] = f["stock_margin"](ticker, asof_iso)
            d["sector_rank_change_5d"] = f["sector_rank_change"](ticker, asof_iso)
        elif cls == "N":
            d["net_profit_yoy"] = f["earnings_yoy"](ticker, asof_iso)
            d["consensus_beat"] = None          # 无一致预期源, 走 YoY 代理(审批单 S3)
            d["ret_20d"] = _ret(f["closes"](ticker, asof_iso), 20)
        data[ticker] = d
    return data
