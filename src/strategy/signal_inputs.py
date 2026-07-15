# -*- coding: utf-8 -*-
"""
signal_inputs.py — Step 18c 数据接线层 v1.4(S3-B 盈利性判定 net_profit_is_loss)
================================================================
职责: 为 migration_signals.evaluate_pool 采集每票输入。
纪律:
  - 按池类别只取所需字段(V:ret_5d / T:个股融资余额+板块排名 / N:净利YoY+ret_20d)
  - 一切静默失败路径必须打 warning 面包屑; 取不到 -> None -> 引擎灰灯(I1.1)
  - fetchers 可注入 -> 沙箱确定性单测零网络
  - S2 融资余额 = **个股** rzye(tushare margin_detail, 裁决⑦口径);
    市场合计 get_margin_trading 不用于 S2(无个股区分度)
  - 板块排名 sector_rank_change_5d = 申万 L1 排名位移(v1.1 接入):
    ticker 所属 L1 行业按 5 日收益在 31 个 L1 行业中的排名, 今 vs 5 个交易日前,
    正数 = 排名下滑。全宇宙收益每日只算一次(进程内 memo + 磁盘日缓存)。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

_logger = logging.getLogger(__name__)

MARGIN_LOOKBACK_CAL_DAYS = 60   # 取 ~25+ 个交易日融资余额
PRICE_LOOKBACK_CAL_DAYS = 60    # 取 ~21+ 根收盘价
SECTOR_LOOKBACK_CAL_DAYS = 35   # L1 指数取 ~11+ 根收盘价(r5_now + r5_prev)
MIN_UNIVERSE = 15               # 可排名行业少于此数 -> 排名不可信 -> None(灰灯)

# 申万2021 L1 兜底清单(index_classify 动态取失败时用)
SW2021_L1_FALLBACK = [
    "801010.SI","801030.SI","801040.SI","801050.SI","801080.SI","801110.SI",
    "801120.SI","801130.SI","801140.SI","801150.SI","801160.SI","801170.SI",
    "801180.SI","801200.SI","801210.SI","801230.SI","801710.SI","801720.SI",
    "801730.SI","801740.SI","801750.SI","801760.SI","801770.SI","801780.SI",
    "801790.SI","801880.SI","801890.SI","801950.SI","801960.SI","801970.SI",
    "801980.SI",
]


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


# ---- 申万 L1 排名位移(纯计算部分独立可测) ----
def _r5_pair(closes: list) -> tuple | None:
    """(r5_now, r5_prev); 需 >=11 根收盘价。"""
    if not closes or len(closes) < 11:
        return None
    c = closes
    try:
        return (c[-1] / c[-6] - 1.0, c[-6] / c[-11] - 1.0)
    except ZeroDivisionError:
        return None


def rank_shift(pairs: dict, my_code: str):
    """pairs: {l1_code: (r5_now, r5_prev)}; 返回 (位移, 宇宙大小) 或 None。
    排名按收益降序, 1 = 最强; 位移 = rank_now - rank_prev, 正数 = 下滑。纯函数。"""
    valid = {k: v for k, v in pairs.items() if v is not None}
    if my_code not in valid or len(valid) < MIN_UNIVERSE:
        return None
    order_now = sorted(valid, key=lambda k: valid[k][0], reverse=True)
    order_prev = sorted(valid, key=lambda k: valid[k][1], reverse=True)
    return (order_now.index(my_code) - order_prev.index(my_code), len(valid))


_RUN_MEMO: dict = {}     # 进程内: {"date": iso, "pairs": {...}, "l1_of": {ticker: code}}


def _cache_file():
    from pathlib import Path
    return Path.home() / ".ai-hedge-fund" / "sw_l1_rank_cache.json"


def _load_universe_pairs(asof_iso: str) -> dict | None:
    """31 个 L1 行业的 (r5_now, r5_prev)。memo -> 磁盘日缓存 -> 现取。"""
    import json
    if _RUN_MEMO.get("date") == asof_iso and "pairs" in _RUN_MEMO:
        return _RUN_MEMO["pairs"]
    cf = _cache_file()
    try:
        if cf.exists():
            blob = json.loads(cf.read_text(encoding="utf-8"))
            if blob.get("date") == asof_iso:
                pairs = {k: (tuple(v) if v else None) for k, v in blob["pairs"].items()}
                _RUN_MEMO.update(date=asof_iso, pairs=pairs)
                return pairs
    except Exception as exc:
        _logger.warning("18c sector: 日缓存读取失败(忽略重算): %s", exc)
    try:
        from src.analysis import peer_compare as pc
    except ImportError:
        try:
            import peer_compare as pc  # type: ignore
        except ImportError:
            _logger.warning("18c sector: peer_compare 不可用, 排名跳过")
            return None
    # L1 清单: 动态优先, 兜底硬清单
    codes = []
    try:
        df = pc._q("index_classify", level="L1", src="SW2021",
                   fields="index_code,industry_name")
        if df is not None and len(df):
            codes = [str(c) for c in df["index_code"].tolist() if c]
    except Exception as exc:
        _logger.warning("18c sector: index_classify 失败(%s), 用兜底清单", exc)
    if len(codes) < MIN_UNIVERSE:
        codes = list(SW2021_L1_FALLBACK)
    from datetime import date as _d, timedelta as _td
    end = asof_iso.replace("-", "")
    start = (_d.fromisoformat(asof_iso) - _td(days=SECTOR_LOOKBACK_CAL_DAYS)).strftime("%Y%m%d")
    pairs: dict = {}
    for code in codes:
        closes = []
        try:
            rows = pc._index_series("index_daily", code, start, end)
            if not rows:
                cut = (_d.fromisoformat(asof_iso) - _td(days=SECTOR_LOOKBACK_CAL_DAYS)).isoformat()
                rows = pc._sw_index_series_ak(code.split(".")[0], cut, asof_iso)
                rows = [r for r in rows if r[0] >= cut]   # 兜底接口给全历史, 截取窗口
            closes = [c for _dt, c in rows]
        except Exception as exc:
            _logger.warning("18c sector: L1 %s 序列失败: %s", code, str(exc)[:100])
        pairs[code] = _r5_pair(closes)
    n_ok = sum(1 for v in pairs.values() if v)
    _logger.warning("18c sector: L1 宇宙就绪 %d/%d 可排名", n_ok, len(pairs))
    _RUN_MEMO.update(date=asof_iso, pairs=pairs)
    try:
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"date": asof_iso,
                                  "pairs": {k: (list(v) if v else None) for k, v in pairs.items()},
                                  "l1_of": _RUN_MEMO.get("l1_of", {})},
                                 ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        _logger.warning("18c sector: 日缓存写入失败(不阻塞): %s", exc)
    return pairs


def _l1_of(ticker: str) -> str | None:
    memo = _RUN_MEMO.setdefault("l1_of", {})
    if ticker in memo:
        return memo[ticker]
    try:
        from src.analysis import peer_compare as pc
        df = pc._q("index_member_all", ts_code=ticker, is_new="Y",
                   fields="ts_code,l1_code,l1_name")
        code = str(df.iloc[0].get("l1_code") or "") if df is not None and len(df) else ""
        if code and not code.endswith(".SI"):
            code = code + ".SI"
        memo[ticker] = code or None
        if not code:
            _logger.warning("18c sector: %s 无 L1 归属(index_member_all 空)", ticker)
        return memo[ticker]
    except Exception as exc:
        _logger.warning("18c sector: %s L1 归属查询失败: %s", ticker, exc)
        return None


def _default_fetch_sector_rank_change(ticker: str, asof_iso: str):
    """申万 L1 排名位移; 任一环节缺 -> None -> S2 灰灯(缺失项已标)。"""
    if ticker.upper().endswith(".HK"):
        return None
    l1 = _l1_of(ticker)
    if not l1:
        return None
    pairs = _load_universe_pairs(asof_iso)
    if not pairs:
        return None
    rs = rank_shift(pairs, l1)
    if rs is None:
        _logger.warning("18c sector: %s(%s) 排名不可算(宇宙不足或自身缺数)", ticker, l1)
        return None
    shift, n = rs
    _logger.warning("18c sector: %s %s 排名位移 %+d (宇宙 %d)", ticker, l1, shift, n)
    return float(shift)


def _g(rec, key):
    return rec.get(key) if isinstance(rec, dict) else getattr(rec, key, None)


def _same_period_rev_yoy(recs) -> float | None:
    """同报告期营收YoY: 找上一年同期(同月日)记录, 避免中报对年报错配。"""
    seq = []
    for r in recs or []:
        p, v = str(_g(r, "report_period") or ""), _g(r, "revenue")
        if p and v:
            seq.append((p, float(v)))
    for i, (p, v) in enumerate(seq):
        if len(p) < 8:
            continue
        prev = str(int(p[:4]) - 1) + p[4:]
        for q, w in seq[i + 1:]:
            if q == prev and w:
                return v / w - 1.0
    return None


def _default_fetch_fin_growth(ticker: str, asof_iso: str):
    """marker: S3B_FORCE_REV_V1 — 返回 (net_profit_yoy, revenue_yoy, is_loss)。
    is_loss = 最新期净利为负(net_margin<0, 兜底 EPS<0); 不可判 -> None(引擎沿用净利口径)。
    单次取数三个口径都出。"""
    try:
        try:
            from src.tools import api_china
        except ImportError:
            import api_china  # type: ignore
        recs = api_china.get_financial_metrics(ticker, asof_iso, period="ttm", limit=6)
        net = None
        for rec in recs or []:
            g = _g(rec, "earnings_growth")
            if g is not None:
                net = float(g)
                break
        rev = None
        for rec in recs or []:                 # v1.3: 源已直供 YoY(HKFIN_INDICATOR_V1)则直取
            rg = _g(rec, "revenue_growth")
            if rg is not None:
                rev = float(rg)
                break
        if rev is None:
            rev = _same_period_rev_yoy(recs)   # 兜底: 同期revenue自算
        is_loss = None                          # S3-B: 仅以最新期判定盈利性(不用陈旧期)
        for rec in (recs or [])[:1]:
            nm = _g(rec, "net_margin")
            eps = _g(rec, "earnings_per_share")
            if nm is not None:
                is_loss = float(nm) < 0.0
            elif eps is not None:
                is_loss = float(eps) < 0.0
        if net is None and rev is None:
            _logger.warning("18c inputs: %s 净利/营收增速均缺失", ticker)
        elif net is None:
            _logger.warning("18c inputs: %s earnings_growth 缺失, 降级营收YoY=%.1f%%",
                            ticker, rev * 100)
        if is_loss and net is not None:
            _logger.warning("18c inputs: %s 未盈利(S3-B), 净利YoY=%+.1f%% 弃用, 强制营收口径(营收YoY=%s)",
                            ticker, net * 100,
                            f"{rev * 100:+.1f}%" if rev is not None else "缺失->灰灯")
        elif is_loss is None and net is not None and recs:
            _logger.warning("18c inputs: %s 盈利性不可判(net_margin/EPS 缺失), S3 沿用净利口径", ticker)
        return net, rev, is_loss
    except Exception as exc:
        _logger.warning("18c inputs: %s 财务采集失败: %s", ticker, exc)
        return None, None, None


DEFAULT_FETCHERS = {
    "closes": _default_fetch_closes,
    "stock_margin": _default_fetch_stock_margin,
    "sector_rank_change": _default_fetch_sector_rank_change,
    "fin_growth": _default_fetch_fin_growth,
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
            res = f["fin_growth"](ticker, asof_iso)
            net, rev, *rest = res               # 兼容旧 2 元组注入 fetcher(沙箱单测)
            d["net_profit_yoy"] = net
            d["revenue_yoy"] = rev              # S3-B: 未盈利强制口径 / 净利YoY缺失降级
            d["net_profit_is_loss"] = rest[0] if rest else None
            d["consensus_beat"] = None          # 无一致预期源, 走 YoY 代理(审批单 S3)
            d["ret_20d"] = _ret(f["closes"](ticker, asof_iso), 20)
        data[ticker] = d
    return data
