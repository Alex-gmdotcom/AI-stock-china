# -*- coding: utf-8 -*-
"""
analysis/unlock_radar.py — 限售解禁雷达 (Phase 3 Step 12, v1.0.0)
========================================================================
规格: TECH v1.1 §8.3 / PRODUCT F14 / I1.1(缺口显式标注) / I10.4(asof PIT)

数据链(全部已确权/已复用,零新依赖):
  · tushare share_float — 逐股东行,必须按 float_date groupby(2026-07-06 探针定谳)。
    ⚠ 单位坑: 文档称 float_share 万股,安集实测为**股**(4378股/5313万股=0.0082%
    与 float_ratio 精确吻合)。防御: float_ratio 为主口径,股数由
    ratio × total_share 反推,float_share 仅做自校准比对+偏差面包屑。
  · tushare daily_basic — total_share/float_share(万股),取 asof 最近交易日。
  · eval.data BaostockPriceSource — 后复权收盘 + 沪深300 基准
    (与 outcome harness 同真相源;基准=沪深300 为 Alex 2026-07-06 拍板)。

fail-soft: 港股/北交所价格链等任何环节失败 → data_gaps 显式标注,绝不编造。
每条退出路径必留面包屑(0704 §8 原则)。
"""
from __future__ import annotations

import bisect
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
__version__ = "1.0.1"

# 历史解禁表现: 只统计占总股本 ≥ 该阈值的事件(碎股东零星解禁无信息量)
_MIN_RATIO_PCT = float(os.environ.get("AIHF_UNLOCK_MIN_RATIO_PCT", "0.5"))
_PERF_N_DAYS = int(os.environ.get("AIHF_UNLOCK_PERF_N", "30"))     # 解禁后 N 个交易日
_WINDOWS = ((92, "3m"), (183, "6m"), (366, "12m"))                 # 自然日窗口


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------
class UnlockEvent(BaseModel):
    float_date: str                       # ISO
    ann_date: str = ""                    # 最近公告日 ISO
    ratio_total_pct: float                # 占总股本 %(逐股东求和)
    ratio_float_pct: Optional[float] = None   # 占流通股本 %(股本数据缺失则 None)
    shares_est: Optional[int] = None      # 解禁股数估计(ratio×total_share 反推)
    n_holders: int = 0
    share_types: list[str] = []


class HistoricalPerf(BaseModel):
    float_date: str
    ratio_total_pct: float
    stock_ret_pct: float                  # 解禁日起 N 交易日收益 %
    bench_ret_pct: float                  # 同窗沪深300 %
    rel_ret_pct: float                    # 超额 = stock − bench


class UnlockRadarResult(BaseModel):
    ticker: str
    asof: str
    upcoming: dict[str, list[UnlockEvent]] = {}    # {"3m":[...],"6m":[...],"12m":[...]}
    cum_ratio_pct: dict[str, float] = {}           # 各窗口累计占总股本 %
    next_event: Optional[UnlockEvent] = None
    historical: list[HistoricalPerf] = []
    hist_rel_median_pct: Optional[float] = None
    hist_rel_min_pct: Optional[float] = None
    hist_rel_max_pct: Optional[float] = None
    data_gaps: list[str] = []
    source_notes: list[str] = []


# ----------------------------------------------------------------------
# 纯计算核心(沙箱可确定性验证,零网络)
# ----------------------------------------------------------------------
def _iso(compact: str) -> str:
    c = (compact or "").strip()
    return f"{c[:4]}-{c[4:6]}-{c[6:8]}" if len(c) == 8 and c.isdigit() else c


def group_events(rows: list[dict],
                 total_share_shares: Optional[float]) -> tuple[list[UnlockEvent], list[str]]:
    """逐股东行 → 按 float_date 聚合;单位自校准面包屑。"""
    notes: list[str] = []
    # 2026-07-06 探针实锤: share_float 存在完全重复行(920222.BJ 同股东同日同量两行),
    # 直接求和会双倍计入 → 按全字段元组精确去重(不同 tranche 只要任一字段不同即保留)
    seen: set = set()
    deduped: list[dict] = []
    for r in rows:
        key = (str(r.get("float_date")), str(r.get("ann_date")), str(r.get("holder_name")),
               str(r.get("float_share")), str(r.get("float_ratio")), str(r.get("share_type")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    n_dup = len(rows) - len(deduped)
    if n_dup:
        notes.append(f"去重 {n_dup} 条完全重复行(share_float 接口 artifact)")
        logger.warning("unlock_radar: share_float 去除 %d 条完全重复行", n_dup)
    by_date: dict[str, dict] = {}
    for r in deduped:
        fd = _iso(str(r.get("float_date") or ""))
        if not fd:
            continue
        g = by_date.setdefault(fd, {"ratio": 0.0, "share_raw": 0.0, "n": 0,
                                    "types": set(), "ann": ""})
        try:
            g["ratio"] += float(r.get("float_ratio") or 0.0)
        except (TypeError, ValueError):
            pass
        try:
            g["share_raw"] += float(r.get("float_share") or 0.0)
        except (TypeError, ValueError):
            pass
        g["n"] += 1
        st = (r.get("share_type") or "").strip()
        if st:
            g["types"].add(st)
        ann = _iso(str(r.get("ann_date") or ""))
        if ann > g["ann"]:
            g["ann"] = ann

    events: list[UnlockEvent] = []
    calib_samples: list[float] = []
    for fd in sorted(by_date):
        g = by_date[fd]
        ratio = round(g["ratio"], 4)
        shares_est: Optional[int] = None
        if total_share_shares and ratio > 0:
            shares_est = int(ratio / 100.0 * total_share_shares)
            if g["share_raw"] > 0 and shares_est > 0:
                calib_samples.append(g["share_raw"] / shares_est)
        events.append(UnlockEvent(
            float_date=fd, ann_date=g["ann"], ratio_total_pct=ratio,
            shares_est=shares_est, n_holders=g["n"], share_types=sorted(g["types"])))

    if calib_samples:
        med = sorted(calib_samples)[len(calib_samples) // 2]
        # 比值 = 事件时股数 / (ratio×当前总股本): 偏离 1 系历史股本变动(转增/增发),非单位问题
        # (688019 中位 0.235 = 上市后股本约 4 倍化,2026-07-06 破案)
        if 0.05 <= med <= 3.0:
            notes.append(f"float_share 单位=股(比值中位 {med:.2f};偏离1系股本变动,非单位问题)")
        elif 500 <= med <= 3e4:
            notes.append(f"float_share 单位=万股(比值中位 {med:.0f}) — 与探针口径不同,注意")
        else:
            notes.append(f"float_share 单位自校准异常(比值中位 {med:.3g}),股数以 ratio 反推为准")
    return events, notes


def attach_float_ratio(events: list[UnlockEvent],
                       total_share_shares: Optional[float],
                       float_share_shares: Optional[float]) -> None:
    """占流通比 = ratio_total × total / float(F14 规格口径)。就地更新。"""
    if not (total_share_shares and float_share_shares and float_share_shares > 0):
        return
    k = total_share_shares / float_share_shares
    for e in events:
        e.ratio_float_pct = round(e.ratio_total_pct * k, 4)


def bucket_upcoming(events: list[UnlockEvent], asof: str
                    ) -> tuple[dict[str, list[UnlockEvent]], dict[str, float], Optional[UnlockEvent]]:
    """未来事件按 3/6/12 月分桶(互斥)+ 各窗口累计比例(含更近窗口)。"""
    d0 = datetime.strptime(asof, "%Y-%m-%d").date()
    buckets: dict[str, list[UnlockEvent]] = {name: [] for _, name in _WINDOWS}
    future = [e for e in events if e.float_date > asof]
    for e in future:
        ed = datetime.strptime(e.float_date, "%Y-%m-%d").date()
        delta = (ed - d0).days
        for days, name in _WINDOWS:
            if delta <= days:
                buckets[name].append(e)
                break
    cum: dict[str, float] = {}
    running = 0.0
    for _, name in _WINDOWS:
        running += sum(e.ratio_total_pct for e in buckets[name])
        cum[name] = round(running, 4)
    nxt = min(future, key=lambda e: e.float_date) if future else None
    return buckets, cum, nxt


def _lookup_le(dates: list[str], closes: list[float], target: str,
               max_back_days: int = 7) -> Optional[float]:
    """基准对齐: 取 ≤ target 的最近收盘(容忍停牌错位 ≤7 自然日)。"""
    i = bisect.bisect_right(dates, target) - 1
    if i < 0:
        return None
    d = datetime.strptime(dates[i], "%Y-%m-%d").date()
    t = datetime.strptime(target, "%Y-%m-%d").date()
    return closes[i] if (t - d).days <= max_back_days else None


def historical_perf(events: list[UnlockEvent], asof: str,
                    stock_series: list[tuple[str, float]],
                    bench_series: list[tuple[str, float]],
                    n_days: int = _PERF_N_DAYS,
                    min_ratio_pct: float = _MIN_RATIO_PCT) -> list[HistoricalPerf]:
    """历史解禁后 N 交易日超额收益(基准沪深300,后复权口径)。
    交易日以个股自身序列为准;窗口越过 asof 或数据不足 → 该事件跳过(不编造)。"""
    if not stock_series:
        return []
    s_dates = [d for d, _ in stock_series]
    s_close = [c for _, c in stock_series]
    b_dates = [d for d, _ in bench_series]
    b_close = [c for _, c in bench_series]
    out: list[HistoricalPerf] = []
    for e in events:
        if e.float_date > asof or e.ratio_total_pct < min_ratio_pct:
            continue
        i0 = bisect.bisect_left(s_dates, e.float_date)     # 首个 ≥ 解禁日的交易日
        i1 = i0 + n_days
        if i0 >= len(s_dates) or i1 >= len(s_dates) or s_dates[i1] > asof:
            continue                                        # PIT: 不越过 asof
        p0, p1 = s_close[i0], s_close[i1]
        if not (p0 and p1 and p0 > 0):
            continue
        b0 = _lookup_le(b_dates, b_close, s_dates[i0])
        b1 = _lookup_le(b_dates, b_close, s_dates[i1])
        if not (b0 and b1 and b0 > 0):
            continue
        sr = (p1 / p0 - 1.0) * 100.0
        br = (b1 / b0 - 1.0) * 100.0
        out.append(HistoricalPerf(
            float_date=e.float_date, ratio_total_pct=e.ratio_total_pct,
            stock_ret_pct=round(sr, 2), bench_ret_pct=round(br, 2),
            rel_ret_pct=round(sr - br, 2)))
    return out


def summarize_rel(perfs: list[HistoricalPerf]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not perfs:
        return None, None, None
    vals = sorted(p.rel_ret_pct for p in perfs)
    return vals[len(vals) // 2], vals[0], vals[-1]


# ----------------------------------------------------------------------
# 数据适配层(真机路径,fail-soft + 面包屑)
# ----------------------------------------------------------------------
def _tsd():
    try:
        from src.tools import tushare_data as t
    except ImportError:
        from tools import tushare_data as t   # type: ignore
    return t


def _q(method: str, **kw):
    t = _tsd()
    if not t.available():
        raise RuntimeError("tushare 不可用(缺 token 或未装包)")
    return t._query(method, **kw)


def _latest_share_counts(norm: str, asof: str) -> tuple[Optional[float], Optional[float]]:
    """daily_basic → (total_share_股, float_share_股)。文档口径万股 ×1e4。"""
    start = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y%m%d")
    df = _q("daily_basic", ts_code=norm, start_date=start,
            end_date=asof.replace("-", ""),
            fields="ts_code,trade_date,total_share,float_share")
    if df is None or getattr(df, "empty", True):
        logger.warning("unlock_radar %s: daily_basic 空返回,股数不可反推", norm)
        return None, None
    row = df.sort_values("trade_date").iloc[-1]
    try:
        ts = float(row["total_share"]) * 1e4
        fs = float(row["float_share"]) * 1e4
        return (ts if ts > 0 else None), (fs if fs > 0 else None)
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("unlock_radar %s: daily_basic 字段异常: %s", norm, exc)
        return None, None


def fetch(norm: str, asof: Optional[str] = None) -> UnlockRadarResult:
    """限售解禁雷达主入口(F14)。任何环节失败 → data_gaps,绝不抛出中断整页。"""
    asof = asof or date.today().isoformat()
    res = UnlockRadarResult(ticker=norm, asof=asof)

    if norm.upper().endswith(".HK"):
        res.data_gaps.append("港股解禁数据源 v1 不覆盖(规格明示,v2 议 HKEX)")
        logger.info("unlock_radar %s: 港股短路,标注缺口", norm)
        return res

    # 1) share_float 全量(逐股东行)
    try:
        df = _q("share_float", ts_code=norm)
    except Exception as exc:
        res.data_gaps.append(f"share_float 获取失败: {str(exc)[:80]}")
        logger.warning("unlock_radar %s: share_float 失败: %s", norm, exc)
        return res
    rows = df.to_dict("records") if df is not None and not getattr(df, "empty", True) else []
    if not rows:
        res.source_notes.append("share_float 零记录(无解禁历史或接口不覆盖该票)")
        logger.info("unlock_radar %s: share_float 零记录", norm)
        return res

    # 2) 股本(反推股数 + 占流通比);失败不阻塞,比例口径仍完整
    total_s, float_s = None, None
    try:
        total_s, float_s = _latest_share_counts(norm, asof)
    except Exception as exc:
        res.data_gaps.append(f"daily_basic 股本获取失败(股数/占流通比缺): {str(exc)[:60]}")

    events, notes = group_events(rows, total_s)
    attach_float_ratio(events, total_s, float_s)
    res.source_notes.extend(notes)

    max_fd = max(e.float_date for e in events)
    if max_fd <= asof:
        # 接口含未来预告已定谳(2026-07-06 全市场窗口探针 6000 行)→ 空 = 真无已公告解禁
        res.source_notes.append(f"未来 12 月无已公告解禁(历史最晚 {max_fd})")
    res.upcoming, res.cum_ratio_pct, res.next_event = bucket_upcoming(events, asof)

    # 3) 历史解禁后表现(基准沪深300,与 harness 同真相源)
    hist_candidates = [e for e in events
                       if e.float_date <= asof and e.ratio_total_pct >= _MIN_RATIO_PCT]
    if hist_candidates:
        try:
            try:
                from src.eval.data import baostock_session, BaostockPriceSource
            except ImportError:
                from eval.data import baostock_session, BaostockPriceSource  # type: ignore
            start = min(e.float_date for e in hist_candidates)
            end = asof
            with baostock_session() as bs:
                src = BaostockPriceSource(bs)
                s = src.get_closes(norm, start, end)
                b = src.get_benchmark_closes(start, end)
            stock_series = list(zip(s.index.tolist(), s.tolist()))
            bench_series = list(zip(b.index.tolist(), b.tolist()))
            if not stock_series:
                res.data_gaps.append("历史解禁表现不可算: baostock 个股价格空(北交所/次新?)")
            else:
                res.historical = historical_perf(hist_candidates, asof,
                                                 stock_series, bench_series)
                m, lo, hi = summarize_rel(res.historical)
                res.hist_rel_median_pct, res.hist_rel_min_pct, res.hist_rel_max_pct = m, lo, hi
                if not res.historical:
                    res.source_notes.append(
                        f"历史事件 {len(hist_candidates)} 起均不满足 {_PERF_N_DAYS} 交易日完整窗口")
        except Exception as exc:
            res.data_gaps.append(f"历史解禁表现不可算(价格链失败): {str(exc)[:80]}")
            logger.warning("unlock_radar %s: 价格链失败: %s", norm, exc)
    logger.info("unlock_radar %s: 事件 %d 起,未来12月累计 %.2f%%,历史样本 %d,缺口 %d",
                norm, len(events), res.cum_ratio_pct.get("12m", 0.0),
                len(res.historical), len(res.data_gaps))
    return res
