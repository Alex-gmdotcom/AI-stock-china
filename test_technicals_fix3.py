# -*- coding: utf-8 -*-
"""test_technicals_fix3.py — 修③ 确定性单测(零网络零 LLM)
运行: poetry run python test_technicals_fix3.py  (仓库根目录)
靶子来自修②草案 §5: 主升浪票 technical 必须能读出可用的偏多。
"""
import sys
sys.path.insert(0, ".")
import numpy as np
import pandas as pd

from src.agents.technicals import (
    calculate_trend_signals, calculate_mean_reversion_signals,
    calculate_momentum_signals, calculate_volatility_signals,
    calculate_stat_arb_signals, weighted_signal_combination,
    apply_regime_gate, _bench_momentum,
    MR_ADX_GATE, MR_TREND_RELIABILITY,
)

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def make_df(closes, vol_scale=None):
    """确定性 OHLCV(无随机): high/low 由 close 派生, 量可注入形状。"""
    n = len(closes)
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995
    vol = np.asarray(vol_scale, dtype=float) if vol_scale is not None else np.full(n, 1e6)
    idx = pd.bdate_range("2025-06-02", periods=n)
    return pd.DataFrame({"open": opens, "close": closes, "high": highs,
                         "low": lows, "volume": vol}, index=idx)


WEIGHTS = {"trend": 0.25, "mean_reversion": 0.20, "momentum": 0.25,
           "volatility": 0.15, "stat_arb": 0.15}


def combine_like_agent(df, bench_mom=None):
    t = calculate_trend_signals(df.copy())
    m = calculate_mean_reversion_signals(df)
    mo = calculate_momentum_signals(df, bench_mom=bench_mom)
    v = calculate_volatility_signals(df)
    st = calculate_stat_arb_signals(df)
    w = apply_regime_gate(WEIGHTS, t["metrics"].get("adx"))
    return weighted_signal_combination(
        {"trend": t, "mean_reversion": m, "momentum": mo,
         "volatility": v, "stat_arb": st}, w), t, mo, w


# ---- F1: 主升浪(中际旭创型): 300根, 日+0.9%稳步上行, 放量 ----
n = 300
up = 100.0 * (1.009 ** np.arange(n))
vol = 1e6 * (1.0 + np.arange(n) / n)          # 温和持续放量
df_up = make_df(up, vol)
c, t, mo, w = combine_like_agent(df_up)
check("F1a 主升浪 -> bullish", c["signal"] == "bullish", str(c))
check("F1b 置信度可用(>=25%)", c["confidence"] >= 0.25, f"conf={c['confidence']:.2f}")
check("F1c momentum 参与方向(非中性)", mo["signal"] == "bullish", str(mo["metrics"]))
check("F1d mom_6m 可算(FixA 扩窗后不再结构性NaN)",
      mo["metrics"]["momentum_6m"] is not None)

# ---- F2: regime 路由(FixC): 强趋势下 MR 权重被掐 ----
check("F2a ADX>25 判强趋势", t["metrics"]["adx"] > MR_ADX_GATE, f"adx={t['metrics']['adx']:.1f}")
check("F2b MR 权重 x0.3", abs(w["mean_reversion"] - 0.20 * MR_TREND_RELIABILITY) < 1e-9)
w_flat = apply_regime_gate(WEIGHTS, 10.0)
check("F2c 弱趋势不掐 MR", abs(w_flat["mean_reversion"] - 0.20) < 1e-9)

# ---- F3: 组合器 v2(C3): 中性不入分母 + breadth 防单策略满置信 ----
one_dir = {"trend": {"signal": "bullish", "confidence": 0.8},
           "mean_reversion": {"signal": "neutral", "confidence": 0.5},
           "momentum": {"signal": "neutral", "confidence": 0.5},
           "volatility": {"signal": "neutral", "confidence": 0.5},
           "stat_arb": {"signal": "neutral", "confidence": 0.5}}
r = weighted_signal_combination(one_dir, WEIGHTS)
check("F3a 单方向策略 raw=1.0", abs(r["raw_score"] - 1.0) < 1e-9)
check("F3b breadth=0.25 -> effective=0.25(非满置信)",
      abs(r["confidence"] - 0.25) < 1e-9, str(r))
all_neutral = {k: {"signal": "neutral", "confidence": 0.5} for k in WEIGHTS}
r0 = weighted_signal_combination(all_neutral, WEIGHTS)
check("F3c 全中性 -> 中性@0(诚实无方向)", r0["signal"] == "neutral" and r0["confidence"] == 0.0)

# ---- F4: 相对强度(FixB): 个股与基准同涨 -> 超额~0 -> 不 bullish ----
bench = pd.Series(up, index=pd.bdate_range("2025-06-02", periods=n).strftime("%Y-%m-%d"))
bm = _bench_momentum(bench)
check("F4a 基准动量三窗口可算", bm is not None and set(bm) == {21, 63, 126})
mo_rel = calculate_momentum_signals(df_up, bench_mom=bm)
check("F4b 同涨(超额~0) -> momentum 非 bullish", mo_rel["signal"] != "bullish",
      str(mo_rel["metrics"]))
check("F4c basis 标 excess", str(mo_rel["metrics"]["momentum_basis"]).startswith("excess"))
# 个股+0.9%/日 vs 基准+0.2%/日 -> 显著超额 -> bullish
bench_slow = pd.Series(100.0 * (1.002 ** np.arange(n)),
                       index=pd.bdate_range("2025-06-02", periods=n).strftime("%Y-%m-%d"))
mo_ex = calculate_momentum_signals(df_up, bench_mom=_bench_momentum(bench_slow))
check("F4d 显著超额 -> bullish", mo_ex["signal"] == "bullish", str(mo_ex["metrics"]))

# ---- F5: I1.1 守卫保留: 60根短史 -> momentum data_gaps ----
df_short = make_df(100.0 * (1.005 ** np.arange(60)))
mo_s = calculate_momentum_signals(df_short)
check("F5a 短史 -> 低置信中性+gaps", mo_s["signal"] == "neutral"
      and mo_s["confidence"] == 0.2 and "momentum_6m" in mo_s["metrics"]["data_gaps"])
bm_short = _bench_momentum(bench[:60])
check("F5b 基准过短 -> None(绝对兜底)", bm_short is None)

# ---- F6: 横盘市不误报多头 ----
flat = 100.0 + 3.0 * np.sin(np.arange(n) / 6.0)
df_flat = make_df(flat)
c_flat, _, _, _ = combine_like_agent(df_flat)
check("F6 横盘 -> 非 bullish", c_flat["signal"] != "bullish", str(c_flat))

# ---- F7: 下行趋势 -> bearish 方向仍可读(对称性) ----
down = 100.0 * (0.992 ** np.arange(n))
df_down = make_df(down, vol)
c_down, _, _, _ = combine_like_agent(df_down)
check("F7 阴跌趋势 -> 非 bullish(bearish/neutral)", c_down["signal"] != "bullish", str(c_down))

n_pass = sum(1 for _, ok in PASS if ok)
print(f"\n==== {n_pass}/{len(PASS)} PASS ====")
sys.exit(0 if n_pass == len(PASS) else 1)
