# -*- coding: utf-8 -*-
"""test_mainflow_chain.py — 主力资金双链确定性单测(零网络)
marker: MAINFLOW_TUSHARE_V1
运行: poetry run python test_mainflow_chain.py  (仓库根目录)
"""
import sys

sys.path.insert(0, ".")
import pandas as pd

from src.tools import api_china
from src.agents.china_capital_flow import _analyze_stock_flow

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def mf_df(rows):
    """rows: [(trade_date, b_lg, s_lg, b_elg, s_elg)] 单位万元"""
    return pd.DataFrame([{"ts_code": "600519.SH", "trade_date": d,
                          "buy_lg_amount": bl, "sell_lg_amount": sl,
                          "buy_elg_amount": be, "sell_elg_amount": se}
                         for d, bl, sl, be, se in rows])


# ---- M1: 口径与单位映射(用探针真实数值 600519 20260715) ----
df = mf_df([("20260715", 302730.99, 259879.16, 142331.53, 178969.82)])
recs = api_china._moneyflow_df_to_records(df, "600519.SH", 20)
r = recs[0]
exp_lg = (302730.99 - 259879.16) * 1e4          # +4.285 亿元
exp_elg = (142331.53 - 178969.82) * 1e4         # -3.664 亿元
check("M1a 日期 YYYYMMDD -> ISO", r.date == "2026-07-15")
check("M1b 大单净额 万元->元", abs(r.large_net_inflow - exp_lg) < 1e-6, str(r.large_net_inflow))
check("M1c 特大单净额 万元->元", abs(r.super_large_net_inflow - exp_elg) < 1e-6)
check("M1d 主力=大单+特大单", abs(r.main_net_inflow - (exp_lg + exp_elg)) < 1e-6,
      f"{r.main_net_inflow:.0f} 元")
check("M1e close/change_pct 显式缺口(不臆造)", r.close is None and r.change_pct is None)
check("M1f source 标注口径", "tushare" in (r.source or ""))

# ---- M2: 排序升序 + limit 截尾(下游 recent_3d 取 [-3:] 依赖升序) ----
df = mf_df([(f"2026070{i}", 100.0 + i, 50.0, 10.0, 5.0) for i in (3, 1, 2)])
recs = api_china._moneyflow_df_to_records(df, "600519.SH", 2)
check("M2a 升序排列", [x.date for x in recs] == ["2026-07-02", "2026-07-03"], str([x.date for x in recs]))
check("M2b limit 保留最近 N 条", len(recs) == 2)

# ---- M3: 缺字段 -> 该行 main=None(不臆造 0) ----
df = pd.DataFrame([{"ts_code": "600519.SH", "trade_date": "20260715",
                    "buy_lg_amount": 100.0, "sell_lg_amount": None,
                    "buy_elg_amount": 10.0, "sell_elg_amount": 5.0}])
recs = api_china._moneyflow_df_to_records(df, "600519.SH", 20)
check("M3 缺卖出额 -> main=None(非 0)", recs[0].main_net_inflow is None)

# ---- M4: 链路由(注入桩, 零网络) ----
calls = {"ts": 0, "ak": 0}


def fake_ts_ok(norm, limit):
    calls["ts"] += 1
    return api_china._moneyflow_df_to_records(
        mf_df([("2026071%d" % i, 200.0, 100.0, 20.0, 10.0) for i in (3, 4, 5)]), norm, limit)


def fake_ts_empty(norm, limit):
    calls["ts"] += 1
    return []


def fake_ak(ticker, norm, market_arg, limit):
    calls["ak"] += 1
    return [api_china.MainCapitalFlow(date="2026-07-15", ticker=norm,
                                      main_net_inflow=123.0,
                                      source="akshare.eastmoney(主力净流入-净额)")]


_ts_real, _ak_real = api_china._mainflow_via_tushare, api_china._mainflow_via_akshare
api_china._mainflow_via_tushare, api_china._mainflow_via_akshare = fake_ts_ok, fake_ak
out = api_china.get_main_capital_flow("600519.SH", limit=20)
check("M4a tushare 通 -> 用 tushare 且不调东财",
      len(out) == 3 and calls["ak"] == 0 and "tushare" in out[0].source, str(calls))

calls.update({"ts": 0, "ak": 0})
api_china._mainflow_via_tushare = fake_ts_empty
out = api_china.get_main_capital_flow("600519.SH", limit=20)
check("M4b tushare 空 -> 回落东财", calls["ak"] == 1 and "eastmoney" in out[0].source)

calls.update({"ts": 0, "ak": 0})
api_china._mainflow_via_akshare = lambda *a, **k: (calls.__setitem__("ak", 1), [])[1]
out = api_china.get_main_capital_flow("600519.SH", limit=20)
check("M4c 双链皆空 -> [](不臆造)", out == [])

out = api_china.get_main_capital_flow("09660.HK", limit=20)
check("M4d 港股不适用 -> [](既有行为不变)", out == [])
api_china._mainflow_via_tushare, api_china._mainflow_via_akshare = _ts_real, _ak_real

# ---- M5: 换链不改判据 —— 下游只用符号/连续性, 幅度尺度无关 ----
def flows(vals, src):
    return [api_china.MainCapitalFlow(date=f"2026-07-{i+1:02d}", ticker="X",
                                      main_net_inflow=v, source=src)
            for i, v in enumerate(vals)]


pattern = [1, 2, 3, 4, 5, 6]          # 连续 6 日净流入
sig_a, conf_a, why_a = _analyze_stock_flow(flows([v * 1e4 for v in pattern], "东财"))
sig_b, conf_b, why_b = _analyze_stock_flow(flows([v * 1e8 for v in pattern], "tushare"))
check("M5a 同符号形态 -> 同信号同置信(幅度尺度无关)",
      (sig_a, conf_a) == (sig_b, conf_b) == ("bullish", 75), f"{sig_a}@{conf_a} vs {sig_b}@{conf_b}")
check("M5b reasoning 透出 source", why_b.get("source") == "tushare")
sig_c, conf_c, _ = _analyze_stock_flow(flows([-1, -2, -3, -4, -5, -6], "tushare"))
check("M5c 连续净流出 -> bearish75", (sig_c, conf_c) == ("bearish", 75))
check("M5d 数据不足(<3) -> 中性20+error(coverage 降级触发)",
      _analyze_stock_flow(flows([1, 2], "tushare"))[2].get("error") is not None)

n_pass = sum(1 for _, ok in PASS if ok)
print(f"\n==== {n_pass}/{len(PASS)} PASS ====")
sys.exit(0 if n_pass == len(PASS) else 1)
