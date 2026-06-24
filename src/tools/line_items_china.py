# -*- coding: utf-8 -*-
"""
tools/line_items_china.py — A 股 + 港股 line items (v2.0.0, 2026-06-22)
=====================================================================

v2.0.0 换地基:A 股从同花顺三表(py_mini_racer/V8 多线程硬崩)迁到 Baostock。
  - A 股:baostock_data 6 表 → 比率代数反推绝对值(revenue/net_income/
          total_assets/equity/liabilities/current_assets/current_liab/
          shares/gross_profit/operating_income)。
  - v3.0: 叠加 Tushare 真值(若配置 token)→ 补上 Baostock 缺的现金流绝对科目
    (capital_expenditure / depreciation / free_cash_flow / dividends),
    并用真值覆盖反推值。无 token 时退回纯 baostock(下方所述)。
  - 无 Tushare 时 Baostock 无现金流量表绝对科目 → 上述四项 None(诚实留空,
    优于 V8 进程级崩溃。受影响:buffett 的 owner-earnings/FCF 内在价值
    退化为"数据不足",但盈利一致性/账面价值/定价权子分析照常)。
  - 港股:保留东财三表(stock_financial_hk_report_em,不走 py_mini_racer)。

api_bridge.py v3.4 把 src.tools.api.search_line_items 对 CN/HK 路由到
本模块的 search_line_items_china。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)
__version__ = "3.0.0"

# ── 软导入 ──────────────────────────────────────────────────────────────
try:
    import akshare as _ak  # type: ignore
    _HAVE_AK = True
except ImportError:
    _ak = None  # type: ignore
    _HAVE_AK = False

try:
    import pandas as _pd  # type: ignore
    _HAVE_PD = True
except ImportError:
    _pd = None  # type: ignore
    _HAVE_PD = False

# Baostock 数据底座(A 股)
try:
    from tools import baostock_data as _bsd  # type: ignore
except ImportError:
    try:
        from src.tools import baostock_data as _bsd  # type: ignore
    except ImportError:
        _bsd = None  # type: ignore

# Tushare 真值底座(A 股现金流绝对科目;无 token 则 available()=False)
try:
    from tools import tushare_data as _tsd  # type: ignore
except ImportError:
    try:
        from src.tools import tushare_data as _tsd  # type: ignore
    except ImportError:
        _tsd = None  # type: ignore

# ticker 规范化
try:
    from markets.ticker import parse_ticker as _parse_ticker  # type: ignore
except ImportError:
    try:
        from src.markets.ticker import parse_ticker as _parse_ticker  # type: ignore
    except ImportError:
        _parse_ticker = None  # type: ignore

# LineItem 模型
try:
    from data.models import LineItem as _LineItem  # type: ignore
    _HAVE_REAL_MODELS = True
except ImportError:
    try:
        from src.data.models import LineItem as _LineItem  # type: ignore
        _HAVE_REAL_MODELS = True
    except ImportError:
        from dataclasses import dataclass, asdict
        _HAVE_REAL_MODELS = False

        @dataclass
        class _LineItem:  # type: ignore
            ticker: str = ""
            report_period: str = ""
            period: str = "ttm"
            currency: str = "CNY"
            revenue: Optional[float] = None
            gross_profit: Optional[float] = None
            net_income: Optional[float] = None
            operating_income: Optional[float] = None
            free_cash_flow: Optional[float] = None
            capital_expenditure: Optional[float] = None
            depreciation_and_amortization: Optional[float] = None
            outstanding_shares: Optional[float] = None
            total_assets: Optional[float] = None
            total_liabilities: Optional[float] = None
            current_assets: Optional[float] = None
            current_liabilities: Optional[float] = None
            cash_and_equivalents: Optional[float] = None
            shareholders_equity: Optional[float] = None
            dividends_and_other_cash_distributions: Optional[float] = None
            def model_dump(self): return asdict(self)

# ensure_no_proxy
try:
    from markets.proxy import ensure_no_proxy  # type: ignore
except ImportError:
    try:
        from src.markets.proxy import ensure_no_proxy  # type: ignore
    except ImportError:
        def ensure_no_proxy(): pass


# ── 工具 ───────────────────────────────────────────────────────────────

def _normalize(ticker: str) -> tuple[str, str]:
    if not ticker:
        raise ValueError("empty ticker")
    if _parse_ticker is not None:
        try:
            info = _parse_ticker(ticker)
            if hasattr(info, "market_value"):
                return info.full_ticker, info.market_value
            return info.full_ticker, info.market.exchange_suffix
        except Exception as exc:
            raise ValueError(f"ticker parse failed: {exc}") from exc
    s = str(ticker).strip().upper()
    if "." in s:
        code, suf = s.split(".", 1)
        if suf == "HK":
            return f"{code.zfill(5)}.HK", "HK"
        return f"{code}.{suf}", suf
    if s.isdigit():
        if len(s) in (4, 5):
            return f"{s.zfill(5)}.HK", "HK"
        if len(s) == 6:
            if s[0] == "6" or s[:3] == "688":
                return f"{s}.SH", "SH"
            if s[:2] in ("43", "83", "87", "88"):
                return f"{s}.BJ", "BJ"
            return f"{s}.SZ", "SZ"
    raise ValueError(f"cannot parse ticker: {ticker!r}")


def _ak_hk(norm: str) -> str:
    return norm.split(".")[0].zfill(5)


def _to_float(v, default=None):
    if v is None or v == "":
        return default
    try:
        f = float(v)
        if _HAVE_PD and _pd.isna(f):  # type: ignore
            return default
        return f
    except (TypeError, ValueError):
        return default


def _row_get(row, *keys, default=None):
    for k in keys:
        try:
            v = row[k]
        except (KeyError, IndexError, TypeError):
            continue
        if v is None:
            continue
        try:
            if _HAVE_PD and _pd.isna(v):  # type: ignore
                continue
        except Exception:
            pass
        return v
    return default


def _safe_construct(cls, **kwargs):
    try:
        return cls(**kwargs)
    except Exception:
        try:
            import inspect
            sig = inspect.signature(cls)
            accepted = set(sig.parameters)
            filtered = {k: v for k, v in kwargs.items() if k in accepted}
            return cls(**filtered)
        except Exception as exc:
            logger.debug("_safe_construct(%s) 失败: %s", cls.__name__, exc)
            return None


def _from_iso_date(s) -> str:
    if s is None:
        return ""
    s = str(s)
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    c = s.replace("/", "").replace("-", "")[:8]
    if len(c) == 8 and c.isdigit():
        return f"{c[:4]}-{c[4:6]}-{c[6:8]}"
    return s


# ── 港股:东财三表(保留,不走 py_mini_racer)─────────────────────────────

_HK_BENEFIT_FIELDS = {
    "revenue": ["营业额", "营业收入", "REVENUE", "TOTAL_REVENUE"],
    "gross_profit": ["毛利", "GROSS_PROFIT"],
    "net_income": ["股东应占溢利", "净利润", "NET_PROFIT", "PROFIT_FOR_PERIOD"],
}
_HK_DEBT_FIELDS = {
    "total_assets": ["资产总计", "总资产", "TOTAL_ASSETS"],
    "total_liabilities": ["负债总计", "总负债", "TOTAL_LIABILITIES"],
    "shareholders_equity": ["股东权益总额", "股东权益", "TOTAL_EQUITY"],
    "outstanding_shares": ["普通股股数", "股本", "SHARES_OUTSTANDING"],
}
_HK_CASH_FIELDS = {
    "capital_expenditure": ["资本开支", "资本性支出", "CAPEX"],
    "depreciation_and_amortization": ["折旧及摊销", "折旧", "DEPRECIATION"],
    "dividends_and_other_cash_distributions": ["已付股息", "派息", "DIVIDENDS_PAID"],
}


def _fetch_hk_table(norm: str, table_symbol: str, field_spec: dict) -> dict:
    try:
        df = _ak.stock_financial_hk_report_em(
            stock=_ak_hk(norm), symbol=table_symbol, indicator="年度")
    except Exception as exc:
        logger.debug("stock_financial_hk_report_em(%s,%s) 失败: %s", norm, table_symbol, exc)
        return {}
    if df is None or len(df) == 0:
        return {}
    date_col = None
    for cand in ("REPORT_DATE", "报告期", "报告日期"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        return {}
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        rep = _from_iso_date(_row_get(row, date_col, default=""))
        if not rep:
            continue
        slot = out.setdefault(rep, {})
        for field_name, candidates in field_spec.items():
            if slot.get(field_name) is not None:
                continue
            v = _to_float(_row_get(row, *candidates))
            if v is not None:
                slot[field_name] = v
    return out


def _parse_hk_three_tables(norm: str) -> dict:
    if not _HAVE_AK:
        return {}
    by_period: dict[str, dict] = {}
    for table_symbol, spec in [
        ("利润表", _HK_BENEFIT_FIELDS),
        ("资产负债表", _HK_DEBT_FIELDS),
        ("现金流量表", _HK_CASH_FIELDS),
    ]:
        partial = _fetch_hk_table(norm, table_symbol, spec)
        for rep, fields in partial.items():
            slot = by_period.setdefault(rep, {})
            for k, v in fields.items():
                if slot.get(k) is None and v is not None:
                    slot[k] = v
    return by_period


# ── A 股:Baostock 比率反推 ────────────────────────────────────────────

def _a_share_by_period(norm: str, end_date: str, limit: int) -> dict:
    """A 股 → {report_period: {line_item_field: value}}。

    底:Baostock 比率反推(revenue/net_income/assets/equity/liab/current_*/shares)。
    叠加:Tushare 真值(若配 token)——覆盖反推值并补上现金流绝对科目
          (capital_expenditure / depreciation / free_cash_flow / dividends)。
    """
    asof = end_date or "9999-12-31"
    by_period: dict[str, dict] = {}

    # 底:baostock 反推
    if _bsd is not None and _bsd.available():
        for blk in _bsd.get_quarters(norm, asof, limit=limit):
            rep = blk.get("statDate", "")
            if rep:
                by_period[rep] = _bsd.line_items_from_block(blk)

    # 叠加:tushare 真值(真值优先,补全 FCF/capex/折旧/分红)
    if _tsd is not None and _tsd.available():
        try:
            ts_by = _tsd.get_abs_periods(norm, asof, limit=limit)
        except Exception as exc:
            logger.warning("tushare 叠加失败,退回 baostock: %s", str(exc)[:120])
            ts_by = {}
        for rep, fields in ts_by.items():
            slot = by_period.setdefault(rep, {})
            for k, v in fields.items():
                if v is not None:
                    slot[k] = v   # tushare 真值覆盖/补全

    return by_period


# ── 主入口 ─────────────────────────────────────────────────────────────

def search_line_items_china(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: Optional[str] = None,
) -> list:
    """A 股 / 港股 line items。接口与 src.tools.api.search_line_items 一致。

    A 股走 Baostock 比率反推;港股走东财三表。任何失败 fail-soft → []。
    """
    try:
        norm, market = _normalize(ticker)
    except ValueError:
        return []

    ensure_no_proxy()

    if market in ("SH", "SZ", "BJ"):
        by_period = _a_share_by_period(norm, end_date, limit)
    elif market == "HK":
        if not _HAVE_AK:
            return []
        by_period = _parse_hk_three_tables(norm)
    else:
        return []

    if not by_period:
        return []

    sorted_periods = sorted(
        (p for p in by_period if not end_date or p <= end_date),
        reverse=True,
    )[:limit]

    currency = "HKD" if market == "HK" else "CNY"
    out: list = []
    for rep in sorted_periods:
        slot = by_period[rep]
        kwargs = {"ticker": norm, "report_period": rep,
                  "period": period, "currency": currency}
        # 预置所有被请求字段为 None,复刻上游 financialdatasets 契约:
        # 请求的字段必然存在(数据源没有则 None),避免 agent 直接裸访问未派生
        # 字段时 AttributeError 把整轮 run 炸掉。  [patch:preseed_requested]
        for _li in line_items:
            if _li not in kwargs:
                kwargs[_li] = None
        for field_name, value in slot.items():
            kwargs[field_name] = value
        item = _safe_construct(_LineItem, **kwargs)
        if item:
            out.append(item)
    return out


# ── Self-test ──────────────────────────────────────────────────────────

def _selftest() -> None:
    print(f"[line_items_china v{__version__}] self-test")
    failures: list[str] = []
    global _bsd

    # 用 fake baostock_data(注入 600519 真实季度块)
    import types as _types
    real_bsd = _bsd

    class _FakeBSD:
        @staticmethod
        def available(): return True
        @staticmethod
        def get_quarters(norm, end_date, limit=8):
            if norm != "600519.SH":
                return []
            return [{
                "statDate": "2026-03-31", "pubDate": "2026-04-25",
                "profit": {"netProfit": "28153831489.89", "npMargin": "0.522245",
                           "gpMargin": "0.897592", "totalShare": "1252270215.00",
                           "epsTTM": "66.052123"},
                "balance": {"assetToEquity": "1.137951", "liabilityToAsset": "0.121227",
                            "currentRatio": "7.060729", "cashRatio": "1.268650"},
                "cash": {"CAToAsset": "0.848729", "CFOToNP": "0.955816"},
                "growth": {}, "operation": {},
                "dupont": {"dupontEbittogr": "0.684145", "dupontAssetTurn": "0.175399"},
            }]
        @staticmethod
        def line_items_from_block(blk):
            # 复用真模块的反推(若可用),否则简化
            if real_bsd is not None:
                return real_bsd.line_items_from_block(blk)
            return {}

    _bsd = _FakeBSD()
    try:
        items = search_line_items_china(
            "600519",
            ["revenue", "net_income", "total_assets", "shareholders_equity",
             "outstanding_shares", "gross_profit", "operating_income"],
            end_date="2026-06-22", limit=10)
        if len(items) != 1:
            failures.append(f"T1.count: {len(items)}")
        else:
            latest = items[0]
            B = 1e9
            checks = [("revenue", 53.9e9), ("net_income", 28.15e9),
                      ("total_assets", 307.4e9), ("shareholders_equity", 270.1e9),
                      ("outstanding_shares", 1252270215.0), ("gross_profit", 48.4e9)]
            for fname, exp in checks:
                act = getattr(latest, fname, None)
                if act is None or abs(act - exp) / exp > 0.015:
                    failures.append(f"T1.{fname}: 期望 {exp/B:.1f}B 得 {act}")
            # FCF/capex 应 None
            for fname in ("free_cash_flow", "capital_expenditure"):
                if getattr(latest, fname, None) is not None:
                    failures.append(f"T1.{fname} 应 None")
        if not any(f.startswith("T1") for f in failures):
            print(f"[T1] A 股 Baostock 反推: PASS (revenue={items[0].revenue/1e8:.0f}亿, "
                  f"FCF={items[0].free_cash_flow})")

        # T2: end_date 截断
        items2 = search_line_items_china("600519", ["revenue"], end_date="2025-12-31")
        if items2 != []:
            failures.append(f"T2: end_date<statDate 应空 得 {len(items2)}")
        else:
            print("[T2] end_date 截断: PASS")

        # T3: 非法 ticker
        if search_line_items_china("totally_invalid", ["revenue"], end_date="2026-06-22") != []:
            failures.append("T3: bad ticker 应 []")
        else:
            print("[T3] bad ticker → []: PASS")
    finally:
        _bsd = real_bsd

    if failures:
        print(f"\n[line_items_china] FAILED ({len(failures)}):")
        for f in failures:
            print("  ✗", f)
        raise SystemExit(1)
    print(f"\n[line_items_china v{__version__}] self-test PASS")


if __name__ == "__main__":
    _selftest()
