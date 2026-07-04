# -*- coding: utf-8 -*-
"""
analysis/peer_compare.py — 同业对比 + 行业指数叠加 (Phase 3 Step 9, v1.0.1)
============================================================================
规格: TECH v1.1 §8.4 / PRODUCT F11+F12 / D1(申万二级) / D2(行业指数+沪深300+中证500)

架构分层(为可测性与权限韧性):
  · 纯计算核心: assemble_peer_table / normalize_overlay — 零 IO,沙箱确定性测试
  · 数据适配层: 每个 tushare 接口一个独立函数,统一经 tushare_data._query
    (复用全局锁/限速/token 门控,单一真相源);任一接口无权限/失败 →
    该维度进 data_gaps,绝不崩(I1.1 / I6.2 fail-soft)
  · 港股: v1 无申万成分映射 → 显式【数据缺口】(I1.1,不编造)

数据接口依赖(2026-07-04 权限探针待回传;全部 fail-soft 兜底):
  index_member_all(ts_code=)   目标股所属申万 L1/L2/L3 + 行业代码
  index_member_all(l2_code=)   L2 全成分
  daily_basic(trade_date=)     全市场 pe_ttm/pb/total_mv 一次取(1 次调用)
  daily(trade_date=)           全市场当日涨跌 pct_chg(1 次调用)
  fina_indicator(ts_code=)     ROE(按市值 top-N 限量取,控制调用数)
  sw_daily(ts_code=L2指数)     申万行业指数日线(2026-07-04 探针: 本档无权限 →
                               v1.0.1 两级兜底: index_daily → akshare 申万接口)
  index_daily(000300/000905)   沪深300 / 中证500 对照线
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
__version__ = "1.0.1"

# ROE 限量取的成员数上限(控制 fina_indicator 调用次数,80次/分档下 ~15s)
ROE_TOP_N = 20
# 同业表最多展示行数(按市值降序截断,行业中位数仍按全成分计算)
TABLE_MAX_ROWS = 30

_CSI300 = "000300.SH"
_CSI500 = "000905.SH"


# ══════════════════════ 数据模型 ══════════════════════

class PeerRow(BaseModel):
    ts_code: str
    name: str = ""
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    roe: Optional[float] = None            # %,fina_indicator 口径
    total_mv: Optional[float] = None       # 万元(tushare daily_basic 口径)
    pct_chg: Optional[float] = None        # 当日涨跌 %
    is_target: bool = False


class PeerCompareResult(BaseModel):
    target: str
    industry_l2_code: str = ""
    industry_l2_name: str = ""
    member_count: int = 0
    rows: list[PeerRow] = []               # 目标置顶,其余按市值降序,截断 TABLE_MAX_ROWS
    industry_median: dict = {}             # {pe_ttm, pb, roe, pct_chg} 全成分中位
    industry_mean: dict = {}
    asof: str = ""                         # 估值快照交易日
    data_gaps: list[str] = []
    source_chain: dict = {}                # footer 用(I8.2/I8.3)


class OverlayResult(BaseModel):
    target: str
    industry_l2_code: str = ""
    industry_l2_name: str = ""
    dates: list[str] = []                  # 公共交易日(升序)
    series: dict[str, list[Optional[float]]] = {}   # 名称 → 归一化序列(起点=100)
    data_gaps: list[str] = []
    source_chain: dict = {}


# ══════════════════════ 纯计算核心(零 IO) ══════════════════════

def _median(vals: list[float]) -> Optional[float]:
    v = sorted(x for x in vals if x is not None)
    n = len(v)
    if n == 0:
        return None
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _mean(vals: list[float]) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return (sum(v) / len(v)) if v else None


def assemble_peer_table(
    target_norm: str,
    l2_code: str,
    l2_name: str,
    members: list[dict],          # [{ts_code, name}]
    valuation: dict[str, dict],   # ts_code → {pe_ttm, pb, total_mv}
    change: dict[str, float],     # ts_code → pct_chg
    roe: dict[str, float],        # ts_code → roe(可为部分成员)
    asof: str = "",
) -> PeerCompareResult:
    """纯计算:成分 + 三张映射 → 同业表。中位/均值按全成分算,展示截断按市值。"""
    gaps: list[str] = []
    rows: list[PeerRow] = []
    for m in members:
        code = m.get("ts_code", "")
        if not code:
            continue
        v = valuation.get(code, {})
        rows.append(PeerRow(
            ts_code=code,
            name=m.get("name", "") or "",
            pe_ttm=v.get("pe_ttm"),
            pb=v.get("pb"),
            total_mv=v.get("total_mv"),
            pct_chg=change.get(code),
            roe=roe.get(code),
            is_target=(code == target_norm),
        ))

    if not rows:
        return PeerCompareResult(
            target=target_norm, industry_l2_code=l2_code, industry_l2_name=l2_name,
            data_gaps=["同业成分为空【数据缺口】"], asof=asof)

    med = {k: _median([getattr(r, k) for r in rows])
           for k in ("pe_ttm", "pb", "roe", "pct_chg")}
    avg = {k: _mean([getattr(r, k) for r in rows])
           for k in ("pe_ttm", "pb", "roe", "pct_chg")}

    if not valuation:
        gaps.append("估值快照缺失(daily_basic)【数据缺口】")
    if not change:
        gaps.append("当日涨跌缺失(daily)【数据缺口】")
    if not roe:
        gaps.append("ROE 缺失(fina_indicator)【数据缺口】")
    if not any(r.is_target for r in rows):
        gaps.append(f"目标 {target_norm} 不在成分列表(次新/调样期)【数据缺口】")

    # 排序:目标置顶,其余市值降序;展示截断,统计不截断
    target_rows = [r for r in rows if r.is_target]
    others = sorted((r for r in rows if not r.is_target),
                    key=lambda r: (r.total_mv is None, -(r.total_mv or 0.0)))
    shown = (target_rows + others)[:TABLE_MAX_ROWS]

    return PeerCompareResult(
        target=target_norm, industry_l2_code=l2_code, industry_l2_name=l2_name,
        member_count=len(rows), rows=shown,
        industry_median=med, industry_mean=avg,
        asof=asof, data_gaps=gaps)


def normalize_overlay(
    series_map: dict[str, list[tuple[str, float]]],   # 名称 → [(date_iso, close)]
    base: float = 100.0,
) -> tuple[list[str], dict[str, list[Optional[float]]], list[str]]:
    """纯计算:多序列按公共交易日对齐,各自归一化到首个公共日=base。
    返回 (公共日期升序, 名称→归一化序列, gaps)。空/无公共日 → fail-soft。"""
    gaps: list[str] = []
    valid = {k: dict(v) for k, v in series_map.items() if v}
    for k in series_map:
        if not series_map[k]:
            gaps.append(f"{k} 序列为空【数据缺口】")
    if not valid:
        return [], {}, gaps or ["全部序列为空【数据缺口】"]

    common = None
    for v in valid.values():
        ds = set(v.keys())
        common = ds if common is None else (common & ds)
    if not common:
        return [], {}, gaps + ["各序列无公共交易日【数据缺口】"]
    dates = sorted(common)

    out: dict[str, list[Optional[float]]] = {}
    for name, v in valid.items():
        first = v[dates[0]]
        if not first:
            gaps.append(f"{name} 起点价无效【数据缺口】")
            continue
        out[name] = [round(v[d] / first * base, 4) for d in dates]
    return dates, out, gaps


# ══════════════════════ 数据适配层(tushare,逐接口隔离 fail-soft) ══════════════════════

def _tsd():
    try:
        from src.tools import tushare_data as m
    except ImportError:
        import tushare_data as m  # type: ignore
    return m


def _q(method: str, **kw):
    """统一经 tushare_data._query(锁/限速/token 单一真相源);失败 → None。"""
    t = _tsd()
    if not t.available():
        return None
    return t._query(method, **kw)


def resolve_sw_industry(norm: str) -> tuple[str, str, list[dict]]:
    """目标股 → (L2 代码, L2 名称, 全成分[{ts_code,name}]);失败 → ("","",[])。"""
    if norm.endswith(".HK"):
        return "", "", []          # 港股 v1 无映射,上层标注
    df = _q("index_member_all", ts_code=norm, is_new="Y",
            fields="ts_code,name,l2_code,l2_name")
    if df is None or len(df) == 0:
        return "", "", []
    l2_code = str(df.iloc[0].get("l2_code") or "")
    l2_name = str(df.iloc[0].get("l2_name") or "")
    if not l2_code:
        return "", "", []
    mem = _q("index_member_all", l2_code=l2_code, is_new="Y",
             fields="ts_code,name")
    if mem is None or len(mem) == 0:
        return l2_code, l2_name, []
    members = [{"ts_code": str(r["ts_code"]), "name": str(r.get("name") or "")}
               for _, r in mem.iterrows()]
    return l2_code, l2_name, members


def _latest_trade_date() -> str:
    """近 10 日内最近有 daily_basic 数据的交易日(YYYYMMDD);失败 → ''。"""
    d = datetime.now()
    for _ in range(10):
        td = d.strftime("%Y%m%d")
        df = _q("daily_basic", trade_date=td, fields="ts_code", limit=1)
        if df is not None and len(df):
            return td
        d -= timedelta(days=1)
    return ""


def fetch_valuation_snapshot(trade_date: str) -> dict[str, dict]:
    """全市场估值快照一次取:ts_code → {pe_ttm, pb, total_mv}。"""
    df = _q("daily_basic", trade_date=trade_date,
            fields="ts_code,pe_ttm,pb,total_mv")
    if df is None or len(df) == 0:
        return {}
    t = _tsd()
    return {str(r["ts_code"]): {"pe_ttm": t._f(r.get("pe_ttm")),
                                "pb": t._f(r.get("pb")),
                                "total_mv": t._f(r.get("total_mv"))}
            for _, r in df.iterrows()}


def fetch_change_snapshot(trade_date: str) -> dict[str, float]:
    """全市场当日涨跌一次取:ts_code → pct_chg。"""
    df = _q("daily", trade_date=trade_date, fields="ts_code,pct_chg")
    if df is None or len(df) == 0:
        return {}
    t = _tsd()
    out = {}
    for _, r in df.iterrows():
        v = t._f(r.get("pct_chg"))
        if v is not None:
            out[str(r["ts_code"])] = v
    return out


def fetch_roe(codes: list[str]) -> dict[str, float]:
    """按 ts_code 逐个取最新一期 ROE(限量 ROE_TOP_N,受 _query 0.35s 限速保护)。"""
    t = _tsd()
    out: dict[str, float] = {}
    for code in codes[:ROE_TOP_N]:
        df = _q("fina_indicator", ts_code=code, fields="ts_code,end_date,roe", limit=1)
        if df is None or len(df) == 0:
            continue
        v = t._f(df.iloc[0].get("roe"))
        if v is not None:
            out[code] = v
    return out


def _index_series(method: str, ts_code: str, start: str, end: str) -> list[tuple[str, float]]:
    """指数日线 → [(date_iso, close)] 升序;失败 → []。"""
    df = _q(method, ts_code=ts_code, start_date=start, end_date=end,
            fields="ts_code,trade_date,close")
    if df is None or len(df) == 0:
        return []
    t = _tsd()
    rows = [(t._iso(str(r["trade_date"])), t._f(r.get("close")))
            for _, r in df.iterrows()]
    rows = [(d, c) for d, c in rows if d and c]
    rows.sort(key=lambda x: x[0])
    return rows


def _sw_index_series_ak(sw_code_plain: str, start_iso: str, end_iso: str) -> list[tuple[str, float]]:
    """akshare 申万指数兜底(index_hist_sw);接口漂移/代理拦截 → [](fail-soft)。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    fn = getattr(ak, "index_hist_sw", None)
    if fn is None:
        return []
    try:
        df = fn(symbol=sw_code_plain, period="day")
    except Exception as exc:
        logger.warning("akshare index_hist_sw(%s) 失败: %s", sw_code_plain, str(exc)[:120])
        return []
    if df is None or len(df) == 0:
        return []
    cols = list(df.columns)
    dcol = next((c for c in ("日期", "date") if c in cols), None)
    ccol = next((c for c in ("收盘", "close", "收盘指数") if c in cols), None)
    if not dcol or not ccol:
        logger.warning("akshare index_hist_sw 列不识别: %s", cols[:8])
        return []
    rows: list[tuple[str, float]] = []
    for _, r in df.iterrows():
        d = str(r[dcol])[:10].replace("/", "-")
        try:
            c = float(r[ccol])
        except (TypeError, ValueError):
            continue
        if len(d) == 10 and c:
            rows.append((d, c))
    rows = [(d, c) for d, c in rows if start_iso <= d <= end_iso]
    rows.sort(key=lambda x: x[0])
    return rows


def _sw_index_series(l2_code: str, start: str, end: str) -> tuple[list[tuple[str, float]], str]:
    """申万 L2 指数序列,两级兜底(sw_daily 无权限档,2026-07-04 探针实锤):
    ① tushare index_daily(部分积分档可取申万代码) ② akshare index_hist_sw。
    返回 (序列, 实际来源);全败 → ([], "")。"""
    sw_code = l2_code if l2_code.endswith(".SI") else f"{l2_code}.SI"
    s = _index_series("index_daily", sw_code, start, end)
    if s:
        return s, "tushare.index_daily"
    t = _tsd()
    s = _sw_index_series_ak(sw_code.split(".")[0], t._iso(start), t._iso(end))
    if s:
        return s, "akshare.index_hist_sw"
    return [], ""


def _target_series(norm: str, start: str, end: str) -> list[tuple[str, float]]:
    """目标股收盘序列,复用现有价格链(baostock/router,零新依赖);失败 → []。"""
    try:
        try:
            from src.tools.api_china import get_prices as _gp
        except ImportError:
            from tools.api_china import get_prices as _gp  # type: ignore
        t = _tsd()
        prices = _gp(norm, t._iso(start), t._iso(end)) or []
        return [(p.time[:10], float(p.close)) for p in prices if p.close]
    except Exception as exc:
        logger.warning("peer_compare 目标价格序列失败 %s: %s", norm, str(exc)[:120])
        return []


# ══════════════════════ 公开 API(TECH §8.4 命名) ══════════════════════

def fetch_peers(norm: str) -> PeerCompareResult:
    """同业对比表(F11)。任一数据维度失败 → data_gaps 标注,不崩。"""
    if norm.endswith(".HK"):
        return PeerCompareResult(
            target=norm, data_gaps=["港股无申万成分映射,v1 不支持同业对比【数据缺口】"])
    l2_code, l2_name, members = resolve_sw_industry(norm)
    if not members:
        return PeerCompareResult(
            target=norm, industry_l2_code=l2_code, industry_l2_name=l2_name,
            data_gaps=["申万成分获取失败(index_member_all 无权限或无数据)【数据缺口】"])
    asof = _latest_trade_date()
    valuation = fetch_valuation_snapshot(asof) if asof else {}
    change = fetch_change_snapshot(asof) if asof else {}
    codes = [m["ts_code"] for m in members]
    codes_by_mv = sorted(codes, key=lambda c: -(valuation.get(c, {}).get("total_mv") or 0))
    roe_codes = ([norm] + [c for c in codes_by_mv if c != norm])  # 目标必取
    roe = fetch_roe(roe_codes)
    res = assemble_peer_table(norm, l2_code, l2_name, members,
                              valuation, change, roe, asof=asof)
    res.source_chain = {"members": "tushare.index_member_all",
                        "valuation": "tushare.daily_basic",
                        "change": "tushare.daily",
                        "roe": f"tushare.fina_indicator(top{ROE_TOP_N})"}
    return res


def industry_index(norm: str, days: int = 120) -> OverlayResult:
    """行业指数叠加(F12,D2 三条对照线):申万L2 vs 沪深300 vs 中证500 vs 目标。"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days * 1.6))).strftime("%Y%m%d")
    l2_code, l2_name = "", ""
    sw_source = ""
    series_map: dict[str, list[tuple[str, float]]] = {}
    if not norm.endswith(".HK"):
        l2_code, l2_name, _ = resolve_sw_industry(norm)
        if l2_code:
            sw_series, sw_source = _sw_index_series(l2_code, start, end)
            series_map[f"申万{l2_name or 'L2'}"] = sw_series
    series_map["沪深300"] = _index_series("index_daily", _CSI300, start, end)
    series_map["中证500"] = _index_series("index_daily", _CSI500, start, end)
    series_map[norm] = _target_series(norm, start, end)

    dates, series, gaps = normalize_overlay(series_map)
    if norm.endswith(".HK"):
        gaps.append("港股无申万行业指数,叠加仅含对照线【数据缺口】")
    return OverlayResult(
        target=norm, industry_l2_code=l2_code, industry_l2_name=l2_name,
        dates=dates, series=series, data_gaps=gaps,
        source_chain={"industry_index": sw_source or "无可用源【数据缺口】",
                      "benchmark": "tushare.index_daily",
                      "target": "api_china.get_prices(现有价格链)"})
