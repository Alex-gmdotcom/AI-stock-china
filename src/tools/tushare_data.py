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


# ── marker: MAINFLOW_TUSHARE_V1 — 个股主力资金(东财 fund_flow 硬掐后的同源级冗余) ──
# 2026-07-16 实锤: akshare stock_individual_fund_flow 13/13 全灭(RemoteDisconnected),
# 4 秒间隔 3/3 仍全灭 → 非连发反爬, 是端点级硬封。该腿占裁决⑦后 capflow 55%,
# 全灭 = capflow 盲眼, 满足"评估驱动修复"门槛。
# 口径: tushare moneyflow 按固定金额档分单(小<5万/中5-20万/大20-100万/特大>100万),
#       与东财动态档不完全同口径 → 记录标 source, 幅度值跨源不可比(符号/连续性可比)。
# 单位: tushare amount 字段为万元, 上层 MainCapitalFlow 约定为元 → ×10000 在 api_china 侧做。
_MONEYFLOW_FIELDS = ("ts_code,trade_date,buy_lg_amount,sell_lg_amount,"
                     "buy_elg_amount,sell_elg_amount,net_mf_amount")


def get_moneyflow(norm: str, start_yyyymmdd: str, end_yyyymmdd: str):
    """个股资金流向(日度). 返回 DataFrame 或 None。
    需 ≥2000 积分; 无 token/包/权限 → None(上层退回 akshare 链)。
    每条退出路径留面包屑(I1.1)。"""
    if not available():
        logger.warning("tushare moneyflow 跳过: available()=False (token/tushare包缺失)")
        return None
    df = _query("moneyflow", ts_code=norm, start_date=start_yyyymmdd,
                end_date=end_yyyymmdd, fields=_MONEYFLOW_FIELDS)
    if df is None:
        logger.warning("tushare moneyflow %s 返回 None(接口异常/积分不足)", norm)
        return None
    if len(df) == 0:
        logger.warning("tushare moneyflow %s 返回空表 window=%s..%s",
                       norm, start_yyyymmdd, end_yyyymmdd)
        return None
    return df


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

# ── marker: TUSHARE_HK_DAILY_V2 (1次/分钟 限频适配 + 诊断面包屑) ──
# 背景: 2026-07-04 探针实测本档位 hk_daily 限频 1次/分钟(A 股行情 80次/分不受影响)。
# 策略: 每 run 每票只调 1 次(超集窗口 + 进程内 memo 切片),调用间隔 ≥61s。
# 面包屑: 请求/结果/空返回一律 warning 级,让"无声失败"在 run log 里现形。
_HK_MEMO: dict = {}
_HK_MIN_INTERVAL = 61.0
_hk_last_call_ts = 0.0
_get_hk_prices_df_v1 = get_hk_prices_df  # 保留 v1 作为底层取数


def get_hk_prices_df(norm: str, start_yyyymmdd: str, end_yyyymmdd: str):
    """v2: memo + 限频包装。切片返回请求窗口;memo 空结果也缓存(同 run 不复烧配额)。"""
    if not norm or not norm.endswith(".HK"):
        return None
    s_iso, e_iso = _iso(start_yyyymmdd), _iso(end_yyyymmdd)

    if norm in _HK_MEMO:
        base = _HK_MEMO[norm]
        if base is None:
            logger.warning("tushare_hk memo命中(空) %s — 本 run 不再重试", norm)
            return None
        out = base[(base["date"] >= s_iso) & (base["date"] <= e_iso)]
        return out.reset_index(drop=True) if len(out) else None

    # 超集窗口: 请求起点再前推 ~200 自然日,覆盖本 run 内其他 agent 的更长窗口
    try:
        _start_dt = datetime.strptime(_compact(start_yyyymmdd), "%Y%m%d") - timedelta(days=200)
        _sup_start = _start_dt.strftime("%Y%m%d")
    except Exception:
        _sup_start = _compact(start_yyyymmdd)
    _sup_end = _compact(end_yyyymmdd)

    global _hk_last_call_ts
    with _LOCK:
        _wait = _HK_MIN_INTERVAL - (time.time() - _hk_last_call_ts)
        if _hk_last_call_ts > 0 and _wait > 0:
            logger.warning("tushare_hk 限频等待 %.0fs (hk_daily 1次/分钟档)", _wait)
            time.sleep(_wait)
        logger.warning("tushare_hk 请求 %s window=%s..%s", norm, _sup_start, _sup_end)
        df = _get_hk_prices_df_v1(norm, _sup_start, _sup_end)
        _hk_last_call_ts = time.time()

    if df is None or len(df) == 0:
        logger.warning("tushare_hk 空返回 %s (无声路径现形: _query 返回空/None)", norm)
        _HK_MEMO[norm] = None
        return None
    logger.warning("tushare_hk 命中 %s rows=%d (%s..%s)", norm, len(df),
                   df.iloc[0]["date"], df.iloc[-1]["date"])
    _HK_MEMO[norm] = df
    out = df[(df["date"] >= s_iso) & (df["date"] <= e_iso)]
    return out.reset_index(drop=True) if len(out) else None

# ── marker: TUSHARE_HK_DAILY_V3 (1次/小时配额适配: 双检锁+磁盘缓存+预算+固定超集) ──
# 0705b 实锤: ① 配额实为 1次/小时(分级限频降档,61s 节流无效);
# ② v2 memo 检查在锁外 → 并发竞态重复请求白烧配额;
# ③ v2 空返回覆盖 memo 好数据(09880 首调 193 行被第二调的空覆盖销毁)。
# v3: memo 判定进锁内(双检);失败永不覆盖好数据;固定超集窗口 end−450天
#     (一次调用喂饱 3月/1年 两类请求);磁盘缓存跨 run 收敛(TTL 20h);
#     每 run API 预算默认 1(AIHF_HK_DAILY_BUDGET 可调),过期无预算供旧+标注。
import json as _json
from pathlib import Path as _Path

_HK_DISK_DIR = _Path.home() / ".ai-hedge-fund" / "hk_prices"
_HK_TTL_SECONDS = 20 * 3600
_HK_SUPERSET_DAYS = 450
_hk_api_attempts = 0


def _hk_budget() -> int:
    try:
        return int(os.environ.get("AIHF_HK_DAILY_BUDGET", "1"))
    except ValueError:
        return 1


def _hk_disk_load(norm: str):
    """→ (rows_df | None, age_seconds | None);损坏/缺失 → (None, None)。"""
    try:
        p = _HK_DISK_DIR / f"{norm}.json"
        if not p.exists():
            return None, None
        data = _json.loads(p.read_text(encoding="utf-8"))
        rows = data.get("rows") or []
        if not rows:
            return None, None
        import pandas as _pd_local
        df = _pd_local.DataFrame(rows)
        age = time.time() - float(data.get("fetched_at_ts", 0))
        return df, age
    except Exception as exc:
        logger.warning("tushare_hk 磁盘缓存读取失败 %s: %s", norm, str(exc)[:100])
        return None, None


def _hk_disk_save(norm: str, df) -> None:
    try:
        _HK_DISK_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at_ts": time.time(),
                   "rows": df.to_dict(orient="records")}
        p = _HK_DISK_DIR / f"{norm}.json"
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)  # 原子写(I4.4 同款纪律)
    except Exception as exc:
        logger.warning("tushare_hk 磁盘缓存写入失败 %s: %s", norm, str(exc)[:100])


def _hk_slice(df, s_iso: str, e_iso: str):
    out = df[(df["date"] >= s_iso) & (df["date"] <= e_iso)]
    return out.reset_index(drop=True) if len(out) else None


def get_hk_prices_df(norm: str, start_yyyymmdd: str, end_yyyymmdd: str):
    """v3: 双检锁 + 磁盘缓存 + 每 run API 预算(1次/小时配额适配)。"""
    global _hk_api_attempts
    if not norm or not norm.endswith(".HK"):
        return None
    s_iso, e_iso = _iso(start_yyyymmdd), _iso(end_yyyymmdd)

    with _LOCK:  # 双检: memo 判定必须在锁内(v2 竞态教训)
        if norm in _HK_MEMO:
            base = _HK_MEMO[norm]
            if base is None:
                return None  # 本 run 已确认无数据,不再烧配额
            return _hk_slice(base, s_iso, e_iso)

        # 磁盘缓存(跨 run 收敛)
        disk_df, age = _hk_disk_load(norm)
        if disk_df is not None and age is not None and age < _HK_TTL_SECONDS:
            _HK_MEMO[norm] = disk_df
            logger.warning("tushare_hk 磁盘缓存命中 %s rows=%d age=%.1fh",
                           norm, len(disk_df), age / 3600)
            return _hk_slice(disk_df, s_iso, e_iso)

        # 预算门控(1次/小时配额,白烧一次全 run 皆输)
        if _hk_api_attempts >= _hk_budget():
            if disk_df is not None:
                _HK_MEMO[norm] = disk_df
                logger.warning("tushare_hk 预算耗尽,供给过期缓存 %s age=%.1fh(【数据缺口】非最新)",
                               norm, (age or 0) / 3600)
                return _hk_slice(disk_df, s_iso, e_iso)
            logger.warning("tushare_hk 预算耗尽(本 run %d/%d)且无缓存 %s → 缺口",
                           _hk_api_attempts, _hk_budget(), norm)
            _HK_MEMO[norm] = None
            return None

        # 固定超集窗口: end−450天,一次调用喂饱本 run 全部窗口需求
        try:
            _end_dt = datetime.strptime(_compact(end_yyyymmdd), "%Y%m%d")
        except Exception:
            _end_dt = datetime.now()
        _sup_start = (_end_dt - timedelta(days=_HK_SUPERSET_DAYS)).strftime("%Y%m%d")
        _sup_end = _end_dt.strftime("%Y%m%d")

        _hk_api_attempts += 1
        logger.warning("tushare_hk 请求 %s window=%s..%s (预算 %d/%d)",
                       norm, _sup_start, _sup_end, _hk_api_attempts, _hk_budget())
        df = _get_hk_prices_df_v1(norm, _sup_start, _sup_end)

        if df is None or len(df) == 0:
            logger.warning("tushare_hk 空返回 %s(限频/无数据)", norm)
            if disk_df is not None:
                _HK_MEMO[norm] = disk_df  # 失败永不覆盖好数据: 退回旧缓存
                logger.warning("tushare_hk 退回过期缓存 %s age=%.1fh", norm, (age or 0) / 3600)
                return _hk_slice(disk_df, s_iso, e_iso)
            _HK_MEMO[norm] = None
            return None

        logger.warning("tushare_hk 命中 %s rows=%d (%s..%s)",
                       norm, len(df), df.iloc[0]["date"], df.iloc[-1]["date"])
        _HK_MEMO[norm] = df
        _hk_disk_save(norm, df)
        return _hk_slice(df, s_iso, e_iso)
