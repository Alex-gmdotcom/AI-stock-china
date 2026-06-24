"""
tools/line_items_china.py — A 股 + 港股 line items 实现

v1.0.0 (2026-06-19, Phase 3 Step 2)

api_bridge.py v3.4 把 src.tools.api.search_line_items 对 CN/HK ticker
路由到本模块的 search_line_items_china. warren_buffett / taleb 等 agent
通过路由透明拿到中国数据.

数据源 (基于 AKShare 1.18+):
  - A 股:
      * 利润表  → ak.stock_financial_benefit_ths(symbol, indicator="按报告期")
      * 资产负债表 → ak.stock_financial_debt_ths(...)
      * 现金流量表 → ak.stock_financial_cash_ths(...)
  - 港股 (东财):
      * 利润表  → ak.stock_financial_hk_report_em(stock, symbol="利润表", indicator="年度")
      * 资产负债表 → 同上 symbol="资产负债表"
      * 现金流量表 → 同上 symbol="现金流量表"

字段映射 (从 warren_buffett.py 反推核心字段):
  revenue, gross_profit, net_income, free_cash_flow,
  capital_expenditure, depreciation_and_amortization,
  outstanding_shares, total_assets, total_liabilities,
  shareholders_equity, dividends_and_other_cash_distributions,
  issuance_or_purchase_of_equity_shares

fail-soft: 任何 API / 字段失败 → 该字段 None / 该期略过, 不抛.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

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

# 软导入 ticker 规范化
try:
    from markets.ticker import parse_ticker as _parse_ticker  # type: ignore
except ImportError:
    try:
        from src.markets.ticker import parse_ticker as _parse_ticker  # type: ignore
    except ImportError:
        _parse_ticker = None  # type: ignore

# 软导入 LineItem Pydantic 模型
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
            free_cash_flow: Optional[float] = None
            capital_expenditure: Optional[float] = None
            depreciation_and_amortization: Optional[float] = None
            outstanding_shares: Optional[float] = None
            total_assets: Optional[float] = None
            total_liabilities: Optional[float] = None
            shareholders_equity: Optional[float] = None
            dividends_and_other_cash_distributions: Optional[float] = None
            issuance_or_purchase_of_equity_shares: Optional[float] = None
            def model_dump(self): return asdict(self)


# 软导入 ensure_no_proxy
try:
    from markets.proxy import ensure_no_proxy  # type: ignore
except ImportError:
    try:
        from src.markets.proxy import ensure_no_proxy  # type: ignore
    except ImportError:
        def ensure_no_proxy(): pass


# ── 工具 ───────────────────────────────────────────────────────────────

def _normalize(ticker: str) -> tuple[str, str]:
    """ → (full_ticker, market_suffix). 复刻 api_china 风格."""
    if not ticker:
        raise ValueError(f"empty ticker")
    if _parse_ticker is not None:
        try:
            info = _parse_ticker(ticker)
            if hasattr(info, "market_value"):
                return info.full_ticker, info.market_value
            return info.full_ticker, info.market.exchange_suffix
        except Exception as exc:
            raise ValueError(f"ticker parse failed: {exc}") from exc
    # stub fallback (sandbox 用)
    s = str(ticker).strip().upper()
    if "." in s:
        code, suf = s.split(".", 1)
        if suf == "HK":
            return f"{code.zfill(5)}.HK", "HK"
        return f"{code}.{suf}", suf
    # 纯数字
    if s.isdigit():
        if len(s) in (4, 5):
            return f"{s.zfill(5)}.HK", "HK"
        if len(s) == 6:
            # 简化推断
            if s[0] == "6" or s[:3] == "688":
                return f"{s}.SH", "SH"
            if s[:2] in ("43", "83", "87", "88"):
                return f"{s}.BJ", "BJ"
            return f"{s}.SZ", "SZ"
    raise ValueError(f"cannot parse ticker: {ticker!r}")


def _ak_a(norm: str) -> str:
    return norm.split(".")[0]


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


def _parse_ths_value(raw) -> Optional[float]:
    """同花顺值带 '亿' / '万' / '%' 后缀清洗."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("--", "-", "None", "nan", "NaN"):
        return None
    s = s.replace(",", "")
    mult = 1.0
    if s.endswith("%"):
        mult = 0.01
        s = s[:-1]
    elif s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


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


# ── A 股: 同花顺三表字段映射 ──────────────────────────────────────────
#
# 同花顺三表中文科目 → LineItem 字段
# (从 warren_buffett.py 反推 + 国内会计准则常见科目命名)

_THS_BENEFIT_MAP = {  # 利润表
    "营业总收入": "revenue",
    "营业收入": "revenue",
    "毛利": "gross_profit",
    "营业利润": None,         # 与 net_income 区分
    "净利润": "net_income",
    "归属于母公司股东的净利润": "net_income",
    "归属母公司股东的净利润": "net_income",
}

_THS_DEBT_MAP = {  # 资产负债表
    "资产总计": "total_assets",
    "负债合计": "total_liabilities",
    "归属于母公司股东权益合计": "shareholders_equity",
    "股东权益合计": "shareholders_equity",
    "所有者权益合计": "shareholders_equity",
    "实收资本(或股本)": "outstanding_shares",
    "股本": "outstanding_shares",
}

_THS_CASH_MAP = {  # 现金流量表
    "经营活动产生的现金流量净额": None,    # 不直接映射, 需减 capex 得 FCF
    "购建固定资产、无形资产和其他长期资产支付的现金": "capital_expenditure",
    "固定资产折旧、油气资产折耗、生产性生物资产折旧": "depreciation_and_amortization",
    "折旧与摊销": "depreciation_and_amortization",
    "分配股利、利润或偿付利息支付的现金": "dividends_and_other_cash_distributions",
    "吸收投资收到的现金": None,
    "取得借款收到的现金": None,
    # issuance_or_purchase_of_equity_shares: 回购 - 发行, 用单独逻辑
}


def _parse_ths_three_tables(norm: str) -> dict[str, dict[str, Optional[float]]]:
    """拉同花顺三表, 按报告期合并字段.

    Returns:
        {"2025-12-31": {"revenue": ..., "net_income": ..., ...}, ...}
        按报告期降序的字典.
    """
    if not _HAVE_AK:
        return {}

    symbol = _ak_a(norm)
    table_specs = [
        ("stock_financial_benefit_ths", _THS_BENEFIT_MAP, "利润表"),
        ("stock_financial_debt_ths", _THS_DEBT_MAP, "资产负债表"),
        ("stock_financial_cash_ths", _THS_CASH_MAP, "现金流量表"),
    ]

    by_period: dict[str, dict[str, Optional[float]]] = {}

    for func_name, field_map, table_name in table_specs:
        try:
            fn = getattr(_ak, func_name, None)
            if fn is None:
                continue
            df = fn(symbol=symbol, indicator="按报告期")
        except Exception as exc:
            logger.debug("%s(%s) 失败: %s", func_name, symbol, exc)
            continue

        if df is None or len(df) == 0:
            continue

        # 找日期列
        date_col = None
        for cand in ("报告期", "报告时间", "时间", "日期"):
            if cand in df.columns:
                date_col = cand
                break
        if date_col is None:
            continue

        for _, row in df.iterrows():
            rep = _from_iso_date(_row_get(row, date_col, default=""))
            if not rep:
                continue
            slot = by_period.setdefault(rep, {})
            for col in df.columns:
                mapped = field_map.get(col)
                if not mapped:
                    continue
                val = _parse_ths_value(row[col])
                if val is not None and slot.get(mapped) is None:
                    slot[mapped] = val

            # 利润表里有营业总收入和营业总成本,可以推 gross_profit
            if table_name == "利润表":
                rev = slot.get("revenue")
                if rev is not None and slot.get("gross_profit") is None:
                    op_cost = _parse_ths_value(_row_get(row, "营业总成本", "营业成本"))
                    if op_cost is not None:
                        slot["gross_profit"] = rev - op_cost

            # 现金流表里, 经营现金流净额 - capex = free_cash_flow
            if table_name == "现金流量表":
                op_cf = _parse_ths_value(_row_get(row, "经营活动产生的现金流量净额"))
                capex = slot.get("capital_expenditure")
                if op_cf is not None and capex is not None and slot.get("free_cash_flow") is None:
                    slot["free_cash_flow"] = op_cf - abs(capex)

                # issuance_or_purchase_of_equity_shares:
                # 正数=融资(吸收投资), 负数=回购
                buy_back = _parse_ths_value(_row_get(row, "支付的其他与筹资活动有关的现金"))
                raise_cash = _parse_ths_value(_row_get(row, "吸收投资收到的现金"))
                if (raise_cash is not None or buy_back is not None) and \
                   slot.get("issuance_or_purchase_of_equity_shares") is None:
                    net = (raise_cash or 0) - (buy_back or 0)
                    slot["issuance_or_purchase_of_equity_shares"] = net

    return by_period


# ── 港股: 东财三表 ─────────────────────────────────────────────────────

_HK_BENEFIT_FIELDS = {  # 利润表
    "revenue": ["营业额", "营业收入", "REVENUE", "TOTAL_REVENUE"],
    "gross_profit": ["毛利", "GROSS_PROFIT"],
    "net_income": ["股东应占溢利", "净利润", "NET_PROFIT", "PROFIT_FOR_PERIOD"],
}

_HK_DEBT_FIELDS = {  # 资产负债表
    "total_assets": ["资产总计", "总资产", "TOTAL_ASSETS"],
    "total_liabilities": ["负债总计", "总负债", "TOTAL_LIABILITIES"],
    "shareholders_equity": ["股东权益总额", "股东权益", "TOTAL_EQUITY"],
    "outstanding_shares": ["普通股股数", "股本", "SHARES_OUTSTANDING"],
}

_HK_CASH_FIELDS = {  # 现金流量表
    "capital_expenditure": ["资本开支", "资本性支出", "CAPEX"],
    "depreciation_and_amortization": ["折旧及摊销", "折旧", "DEPRECIATION"],
    "dividends_and_other_cash_distributions": ["已付股息", "派息", "DIVIDENDS_PAID"],
}


def _fetch_hk_table(norm: str, table_symbol: str, field_spec: dict) -> dict:
    """拉一张港股报表, 返回 {report_period: {field: value}}."""
    try:
        df = _ak.stock_financial_hk_report_em(
            stock=_ak_hk(norm), symbol=table_symbol, indicator="年度"
        )
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


def _parse_hk_three_tables(norm: str) -> dict[str, dict[str, Optional[float]]]:
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


# ── 主入口 ─────────────────────────────────────────────────────────────

def search_line_items_china(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: Optional[str] = None,
) -> list:
    """A 股 / 港股 line items. 接口与 src.tools.api.search_line_items 一致.

    Args:
        ticker: 任意常见形式.
        line_items: 请求的字段名列表 (英文, 见 warren_buffett.py).
        end_date: ISO 'YYYY-MM-DD', 按报告期截断.
        period: 仅作 cache key, 实际按 报告期 返回 (年度).
        limit: 最大返回报告期数 (倒序).

    Returns:
        list[LineItem], 按报告期降序.
    """
    if not _HAVE_AK:
        return []
    try:
        norm, market = _normalize(ticker)
    except ValueError:
        return []

    ensure_no_proxy()

    if market in ("SH", "SZ", "BJ"):
        by_period = _parse_ths_three_tables(norm)
    elif market == "HK":
        by_period = _parse_hk_three_tables(norm)
    else:
        return []

    if not by_period:
        return []

    # 按报告期降序 + end_date 截断 + limit
    sorted_periods = sorted(
        (p for p in by_period if not end_date or p <= end_date),
        reverse=True,
    )[:limit]

    requested = set(line_items or [])
    currency = "HKD" if market == "HK" else "CNY"

    out: list = []
    for rep in sorted_periods:
        slot = by_period[rep]
        kwargs = {
            "ticker": norm,
            "report_period": rep,
            "period": period,
            "currency": currency,
        }
        # 只填请求的字段 (与原版 search_line_items 行为一致)
        # 注: 实测中 agent 也会访问未请求的字段做兜底, 所以我们
        # 把已有的全部填进去, requested 只用于 line_items 校验
        for field_name, value in slot.items():
            kwargs[field_name] = value
        item = _safe_construct(_LineItem, **kwargs)
        if item:
            out.append(item)

    return out


# ── Self-test ──────────────────────────────────────────────────────────

def _selftest() -> None:
    print(f"[line_items_china v{__version__}] self-test (mock AKShare)")
    failures: list[str] = []
    global _ak, _HAVE_AK
    real_ak, real_have_ak = _ak, _HAVE_AK

    try:
        # mock AKShare 同花顺三表
        class _FakeAK:
            @staticmethod
            def stock_financial_benefit_ths(symbol, indicator):
                return _pd.DataFrame([
                    {"报告期": "2025-12-31",
                     "营业总收入": "1740亿",
                     "营业总成本": "143亿",
                     "净利润": "925亿"},
                    {"报告期": "2024-12-31",
                     "营业总收入": "1503亿",
                     "营业总成本": "129亿",
                     "净利润": "747亿"},
                ])
            @staticmethod
            def stock_financial_debt_ths(symbol, indicator):
                return _pd.DataFrame([
                    {"报告期": "2025-12-31",
                     "资产总计": "3100亿",
                     "负债合计": "560亿",
                     "归属于母公司股东权益合计": "2540亿",
                     "股本": "12.56亿"},
                    {"报告期": "2024-12-31",
                     "资产总计": "2860亿",
                     "负债合计": "510亿",
                     "归属于母公司股东权益合计": "2350亿",
                     "股本": "12.56亿"},
                ])
            @staticmethod
            def stock_financial_cash_ths(symbol, indicator):
                return _pd.DataFrame([
                    {"报告期": "2025-12-31",
                     "经营活动产生的现金流量净额": "893亿",
                     "购建固定资产、无形资产和其他长期资产支付的现金": "73亿",
                     "折旧与摊销": "20亿"},
                ])

        _ak = _FakeAK()
        _HAVE_AK = True

        # T1: 基础调用
        items = search_line_items_china(
            "600519",
            ["revenue", "net_income", "total_assets",
             "shareholders_equity", "outstanding_shares",
             "free_cash_flow", "capital_expenditure",
             "depreciation_and_amortization", "gross_profit"],
            end_date="2025-12-31",
            limit=10,
        )
        if len(items) != 2:
            failures.append(f"T1.count: {len(items)}")
        else:
            latest = items[0]
            checks = [
                ("revenue", 1740e8),
                ("net_income", 925e8),
                ("total_assets", 3100e8),
                ("shareholders_equity", 2540e8),
                ("outstanding_shares", 12.56e8),
                ("capital_expenditure", 73e8),
                ("depreciation_and_amortization", 20e8),
                ("gross_profit", 1740e8 - 143e8),  # rev - cost
                ("free_cash_flow", 893e8 - 73e8),   # op_cf - capex
            ]
            for fname, expected in checks:
                actual = getattr(latest, fname, None)
                if actual is None or abs(actual - expected) > 1e6:
                    failures.append(f"T1.{fname}: 期望 {expected:.0f} 得 {actual}")

        if not any(f.startswith("T1") for f in failures):
            print(f"[T1] A 股 search_line_items (9 字段全过): PASS "
                  f"({len(items)} periods, revenue={latest.revenue/1e8:.0f}亿, "
                  f"FCF={latest.free_cash_flow/1e8:.0f}亿)")

        # T2: 端日截断
        items = search_line_items_china(
            "600519", ["revenue"], end_date="2024-12-31", limit=10,
        )
        if len(items) != 1:
            failures.append(f"T2.end_date filter: {len(items)}")
        elif items[0].report_period != "2024-12-31":
            failures.append(f"T2.period: {items[0].report_period}")
        else:
            print(f"[T2] end_date 截断: PASS ({items[0].report_period})")

        # T3: 非法 ticker → []
        bad = search_line_items_china("totally_invalid", ["revenue"], end_date="2025-12-31")
        if bad != []:
            failures.append(f"T3.bad ticker: {bad}")
        else:
            print("[T3] bad ticker → [] no raise: PASS")

        # T4: AKShare 不可用 → fail-soft
        _HAVE_AK = False
        empty = search_line_items_china("600519", ["revenue"], end_date="2025-12-31")
        if empty != []:
            failures.append("T4: fail-soft 不工作")
        else:
            print("[T4] AKShare 不可用 → fail-soft: PASS")

    finally:
        _ak = real_ak
        _HAVE_AK = real_have_ak

    if failures:
        print(f"\n[line_items_china] FAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[line_items_china v{__version__}] self-test PASS (4 groups)")


if __name__ == "__main__":
    _selftest()
