"""
src/eval/metrics.py — outcome 评估纯数学层
======================================================================
职责：只做纯计算，不碰任何外部数据源（baostock/DeepSeek/网络）。
      → 这一层可以在沙箱里对着合成数据确定性验证，是整个 harness 的
        "可信真值"基石。如果这里的数学错了，下游全部污染。

不依赖 scipy（国内机器可能没装）。RankIC 用 pandas 秩相关自实现。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 1. 信号方向 → 数值分（供截面相关性用）
# ----------------------------------------------------------------------
_DIRECTION = {
    "bullish": 1.0, "buy": 1.0, "多": 1.0, "偏多": 1.0,
    "bearish": -1.0, "sell": -1.0, "short": -1.0, "空": -1.0, "偏空": -1.0,
    "neutral": 0.0, "hold": 0.0, "中性": 0.0,
}


def signal_to_score(direction: str, confidence: float) -> float:
    """把 (方向, 置信度0-100) 映射成 [-1, 1] 的连续分。

    score = dir_sign * (confidence / 100)
    - bullish  ->  +conf
    - bearish  ->  -conf
    - neutral  ->   0
    未知方向 -> 0（安全兜底，绝不臆造方向）。
    """
    sign = _DIRECTION.get(str(direction).strip().lower(), None)
    if sign is None:
        # 不认识的方向标签：记 0，不猜。上层应在 signals.py 归一化。
        sign = 0.0
    conf = 0.0 if confidence is None else float(confidence)
    if conf > 1.5:          # 传进来的是 0-100，归一到 0-1
        conf = conf / 100.0
    conf = min(max(conf, 0.0), 1.0)
    return sign * conf


# ----------------------------------------------------------------------
# 2. 前瞻收益
# ----------------------------------------------------------------------
def forward_return(entry_close: float, exit_close: float) -> float:
    """单只：h 个交易日后的前瞻收益。exit/entry - 1。用后复权收盘价。"""
    if entry_close is None or exit_close is None or entry_close == 0:
        return np.nan
    return exit_close / entry_close - 1.0


def excess_return(stock_ret: float, bench_ret: float) -> float:
    """相对基准（沪深300）的超额收益。"""
    if np.isnan(stock_ret) or np.isnan(bench_ret):
        return np.nan
    return stock_ret - bench_ret


# ----------------------------------------------------------------------
# 3. 截面 IC / RankIC（单个 date 截面内，score vs 前瞻收益）
# ----------------------------------------------------------------------
def _clean_pairs(scores: pd.Series, rets: pd.Series) -> tuple[pd.Series, pd.Series]:
    df = pd.DataFrame({"s": scores, "r": rets}).dropna()
    return df["s"], df["r"]


def cross_sectional_ic(scores: pd.Series, rets: pd.Series, min_n: int = 3) -> float:
    """Pearson IC：一个日期截面里，信号分与前瞻收益的皮尔逊相关。

    min_n: 少于这么多有效样本就返回 nan（15 只池子单日 n 很小，
           诚实地不给伪相关）。
    """
    s, r = _clean_pairs(scores, rets)
    if len(s) < min_n or s.std(ddof=0) == 0 or r.std(ddof=0) == 0:
        return np.nan
    return float(np.corrcoef(s.values, r.values)[0, 1])


def cross_sectional_rank_ic(scores: pd.Series, rets: pd.Series, min_n: int = 3) -> float:
    """RankIC = Spearman：先转秩再算 Pearson，避免 scipy 依赖。"""
    s, r = _clean_pairs(scores, rets)
    if len(s) < min_n:
        return np.nan
    sr, rr = s.rank(), r.rank()
    if sr.std(ddof=0) == 0 or rr.std(ddof=0) == 0:
        return np.nan
    return float(np.corrcoef(sr.values, rr.values)[0, 1])


# ----------------------------------------------------------------------
# 4. 跨日期聚合：IC 序列 → ICIR
# ----------------------------------------------------------------------
def ic_series(panel: pd.DataFrame, kind: str = "rank", min_n: int = 3) -> pd.Series:
    """对每个 date 截面算 IC，返回按日期索引的 IC 序列。

    panel 需含列：date, ticker, score, fwd_ret
    kind: "rank"(RankIC) | "pearson"(IC)
    """
    fn = cross_sectional_rank_ic if kind == "rank" else cross_sectional_ic
    out = {}
    for date, g in panel.groupby("date"):
        out[date] = fn(g["score"], g["fwd_ret"], min_n=min_n)
    return pd.Series(out, name=f"{kind}_ic").sort_index()


def icir(ic_ser: pd.Series) -> float:
    """ICIR = mean(IC) / std(IC)。需要 >= 2 个有效日期截面，否则 nan。

    ⚠️ 诚实约束：现在只有单日信号 → ICIR 无意义，必须向前累积
       多个截面日期（2-4 周多次批跑）才可信。
    """
    ic = ic_ser.dropna()
    if len(ic) < 2 or ic.std(ddof=1) == 0:
        return np.nan
    return float(ic.mean() / ic.std(ddof=1))


# ----------------------------------------------------------------------
# 5. 命中率（方向对不对，只看有方向的信号）
# ----------------------------------------------------------------------
def hit_rate(scores: pd.Series, rets: pd.Series, neutral_eps: float = 1e-9) -> dict:
    """方向命中率。

    只统计有方向的信号（|score| > eps；neutral 不计）。
    返回：命中数 / 有方向总数 / 命中率。
    """
    s, r = _clean_pairs(scores, rets)
    directional = s.abs() > neutral_eps
    s, r = s[directional], r[directional]
    n = len(s)
    if n == 0:
        return {"hits": 0, "directional": 0, "hit_rate": np.nan}
    hits = int((np.sign(s.values) == np.sign(r.values)).sum())
    return {"hits": hits, "directional": n, "hit_rate": hits / n}


def summary(panel: pd.DataFrame, min_n: int = 3) -> dict:
    """一次性汇总所有指标。panel 列：date, ticker, score, fwd_ret, excess_ret(可选)。"""
    rank = ic_series(panel, kind="rank", min_n=min_n)
    pear = ic_series(panel, kind="pearson", min_n=min_n)
    hr = hit_rate(panel["score"], panel["fwd_ret"])
    res = {
        "n_dates": int(panel["date"].nunique()),
        "n_obs": int(len(panel.dropna(subset=["score", "fwd_ret"]))),
        "rank_ic_mean": float(rank.mean()) if rank.notna().any() else np.nan,
        "ic_mean": float(pear.mean()) if pear.notna().any() else np.nan,
        "rank_icir": icir(rank),
        "icir": icir(pear),
        "hit_rate": hr["hit_rate"],
        "directional_n": hr["directional"],
    }
    if "excess_ret" in panel.columns:
        ex = hit_rate(panel["score"], panel["excess_ret"])
        res["excess_hit_rate"] = ex["hit_rate"]
    return res
