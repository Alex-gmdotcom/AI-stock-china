# -*- coding: utf-8 -*-
"""
tools/tushare_data.py — A 股现金流量表绝对值底座 (Phase 2, v1.0.0)
================================================================
补 Baostock 唯一的真实数据缺口:现金流量表绝对科目
(capital_expenditure / depreciation / free_cash_flow / dividends),
让 buffett 的 owner-earnings / 真 FCF 内在价值回归。

FCF = 经营活动现金流量净额(n_cashflow_act) - 购建固定资产支付的现金(c_pay_acq_const_fiolta)

门控:需 tushare 包 + token(env AIHF_TUSHARE_TOKEN 或 TUSHARE_TOKEN)。
      cashflow 接口需 ≥2000 积分(见 tushare.pro 积分说明)。
      未配 token / 包未装 → available()=False,上层不调用,行为退回 baostock。

并发:tushare 走 HTTP(requests),受积分限频。本模块加全局锁 + 简单限速,
      配合上层 _cache_v2,避免 9 路并发撞限频。
ts_code 即我们的 norm 格式("600519.SH");北交所 tushare 覆盖有限,空则上层兜底。
point-in-time:按 f_ann_date(实际公告日)≤ asof 过滤,回测不前视。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)
__version__ = "1.0.0"

try:
    import tushare as _ts  # type: ignore
    _HAVE_TS = True
except ImportError:
    _ts = None  # type: ignore
    _HAVE_TS = False

_LOCK = threading.RLock()
_pro_api = None
_last_call_ts = 0.0
_MIN_INTERVAL = 0.35   # 秒,粗限速(~170 次/分,低于多数积分档上限)


def _token() -> Optional[str]:
    return os.environ.get("AIHF_TUSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN")


def available() -> bool:
    return _HAVE_TS and bool(_token())


def _pro():
    global _pro_api
    if not available():
        return None
    if _pro_api is None:
        try:
            _ts.set_token(_token())
            _pro_api = _ts.pro_api()
        except Exception as exc:
            logger.warning("tushare pro_api 初始化失败: %s", exc)
            _pro_api = None
    return _pro_api


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _compact(d: str) -> str:
    return (d or "").replace("-", "")[:8]


def _iso(d: str) -> str:
    d = _compact(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d


def _query(method: str, **kwargs):
    """限速 + 串行执行一个 tushare pro 接口,返回 DataFrame 或 None。"""
    pro = _pro()
    if pro is None:
        return None
    fn = getattr(pro, method, None)
    if fn is None:
        return None
    global _last_call_ts
    with _LOCK:
        dt = time.time() - _last_call_ts
        if dt < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - dt)
        try:
            df = fn(**kwargs)
            _last_call_ts = time.time()
            return df
        except Exception as exc:
            _last_call_ts = time.time()
            logger.warning("tushare %s 失败: %s", method, str(exc)[:140])
            return None


# ── 字段映射 ─────────────────────────────────────────────────────────
_CASHFLOW_FIELDS = ("ts_code,end_date,f_ann_date,n_cashflow_act,"
                    "c_pay_acq_const_fiolta,depr_fa_coga_dpba,amort_intang_assets,"
                    "lt_amort_deferred_exp,c_pay_dist_dpcp_int_exp,free_cashflow")
_BALANCE_FIELDS = ("ts_code,end_date,f_ann_date,total_assets,total_liab,"
                   "total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab,money_cap")
_INCOME_FIELDS = ("ts_code,end_date,f_ann_date,total_revenue,revenue,n_income,"
                  "n_income_attr_p,operate_profit,oper_cost")


def _rows_by_period(df, asof_compact: str) -> dict:
    """DataFrame → {end_date(iso): {col: val}},按 f_ann_date≤asof 过滤防前视。"""
    out = {}
    if df is None or len(df) == 0:
        return out
    for _, row in df.iterrows():
        fann = _compact(str(row.get("f_ann_date") or row.get("ann_date") or ""))
        if fann and asof_compact and fann > asof_compact:
            continue
        rep = _iso(str(row.get("end_date") or ""))
        if not rep:
            continue
        # 同一报告期保留最早公告的一条(去重)
        if rep in out:
            continue
        out[rep] = {c: row.get(c) for c in row.index}
    return out


def get_abs_periods(norm: str, asof: str, limit: int = 8) -> dict:
    """→ {report_period: {LineItem 绝对值字段}}(现金流+资产负债+利润)。

    供 line_items_china 叠加到 baostock 反推之上(tushare 真值优先)。
    """
    if not available():
        return {}
    ts_code = norm  # norm 即 ts_code 格式
    try:
        end = datetime.strptime(asof[:10], "%Y-%m-%d")
    except Exception:
        end = datetime.now()
    start = (end - timedelta(days=365 * 4)).strftime("%Y%m%d")
    end_c = end.strftime("%Y%m%d")

    cf = _rows_by_period(_query("cashflow", ts_code=ts_code, start_date=start,
                                end_date=end_c, fields=_CASHFLOW_FIELDS), end_c)
    if not cf:
        return {}  # 无现金流 = tushare 对此票无用 → 让 baostock 兜底
    bs = _rows_by_period(_query("balancesheet", ts_code=ts_code, start_date=start,
                                end_date=end_c, fields=_BALANCE_FIELDS), end_c)
    inc = _rows_by_period(_query("income", ts_code=ts_code, start_date=start,
                                 end_date=end_c, fields=_INCOME_FIELDS), end_c)

    periods = sorted(cf.keys(), reverse=True)[:limit]
    out = {}
    for rep in periods:
        c = cf.get(rep, {})
        b = bs.get(rep, {})
        n = inc.get(rep, {})

        cfo = _f(c.get("n_cashflow_act"))
        capex = _f(c.get("c_pay_acq_const_fiolta"))
        fcf = (cfo - capex) if (cfo is not None and capex is not None) else _f(c.get("free_cashflow"))
        depr = sum(x for x in (_f(c.get("depr_fa_coga_dpba")),
                               _f(c.get("amort_intang_assets")),
                               _f(c.get("lt_amort_deferred_exp"))) if x is not None) or None
        revenue = _f(n.get("revenue")) or _f(n.get("total_revenue"))
        oper_cost = _f(n.get("oper_cost"))

        rec = {
            "free_cash_flow": fcf,
            "capital_expenditure": (-abs(capex)) if capex is not None else None,  # 支出记负
            "depreciation_and_amortization": depr,
            "dividends_and_other_cash_distributions":
                (lambda d: -abs(d) if d is not None else None)(_f(c.get("c_pay_dist_dpcp_int_exp"))),
            # 资产负债真值(可覆盖 baostock 反推)
            "total_assets": _f(b.get("total_assets")),
            "total_liabilities": _f(b.get("total_liab")),
            "shareholders_equity": _f(b.get("total_hldr_eqy_exc_min_int")),
            "current_assets": _f(b.get("total_cur_assets")),
            "current_liabilities": _f(b.get("total_cur_liab")),
            "cash_and_equivalents": _f(b.get("money_cap")),
            # 利润真值
            "revenue": revenue,
            "net_income": _f(n.get("n_income_attr_p")) or _f(n.get("n_income")),
            "operating_income": _f(n.get("operate_profit")),
            "gross_profit": (revenue - oper_cost) if (revenue is not None and oper_cost is not None) else None,
        }
        out[rep] = {k: v for k, v in rec.items() if v is not None}
    return out


def get_fcf_latest(norm: str, asof: str) -> Optional[float]:
    """最新一期 FCF(供 free_cash_flow_yield 等)。"""
    periods = get_abs_periods(norm, asof, limit=1)
    if not periods:
        return None
    rep = sorted(periods.keys(), reverse=True)[0]
    return periods[rep].get("free_cash_flow")

# ── marker: TUSHARE_HK_DAILY_V1 (HK PRICE 链首选,权限探针 2026-07-03 通过) ──
def get_hk_prices_df(norm: str, start_yyyymmdd: str, end_yyyymmdd: str):
    """港股日线 via pro.hk_daily(不复权) → DataFrame[date,open,close,high,low,volume] 升序。
    口径注记:hk_daily 为不复权价;与东财 qfq 的口径差属已接受兜底取舍
    (同 pytdx 先例,ADR-06);tushare 居链首时同源自洽。
    失败/空/无权限 → None(上层链继续东财/新浪)。"""
    if not norm or not norm.endswith(".HK"):
        return None
    df = _query("hk_daily", ts_code=norm,
                start_date=_compact(start_yyyymmdd), end_date=_compact(end_yyyymmdd))
    if df is None or len(df) == 0:
        return None
    try:
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": _iso(str(r.get("trade_date", ""))),
                "open": _f(r.get("open")),
                "close": _f(r.get("close")),
                "high": _f(r.get("high")),
                "low": _f(r.get("low")),
                "volume": _f(r.get("vol")) or 0,
            })
        rows = [x for x in rows if x["date"] and x["close"] is not None]
        if not rows:
            return None
        rows.sort(key=lambda x: x["date"])  # tushare 返回降序 → 升序
        import pandas as _pd_local
        return _pd_local.DataFrame(rows)
    except Exception as exc:
        logger.warning("tushare hk_daily(%s) 解析失败: %s", norm, str(exc)[:140])
        return None
