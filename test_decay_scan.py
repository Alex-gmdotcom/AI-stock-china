# -*- coding: utf-8 -*-
"""test_decay_scan.py — 衰减扫描器确定性单测(零网络; 合成 JSONL + 注入价格)
运行: poetry run python test_decay_scan.py  (仓库根目录)
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")
import numpy as np
import pandas as pd

from run_signal_decay_scan import (scan, load_log_entries, OUTCOME_H,
                                   MIN_N, HIT_DECAYED)

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# ---- 合成市场: 60个交易日, 基准平, 票A恒涨(超额+), 票B恒跌(超额-) ----
DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-04-01", periods=60)]
BENCH = pd.Series(4000.0, index=DATES)
PX_UP = pd.Series(100.0 * (1.01 ** np.arange(60)), index=DATES)
PX_DN = pd.Series(100.0 * (0.99 ** np.arange(60)), index=DATES)


def fetch_stub(entries):
    return {"AAA": PX_UP, "BBB": PX_DN, "09XX.HK": PX_UP}, BENCH, DATES


def make_log(rows):
    f = Path(tempfile.mkdtemp()) / "log.jsonl"
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return f


def rows_for(sig, ticker, dates, rid_prefix):
    return [{"run_date": d, "ticker": ticker, "signal": sig, "strength": "yellow",
             "record_id": f"{rid_prefix}{i}"} for i, d in enumerate(dates)]


# D1: 空日志 → 优雅结论
st, oc = scan(Path(tempfile.mkdtemp()) / "none.jsonl", fetch_stub)
check("D1 空日志 -> note+全active", "note" in st
      and all(b["state"] == "active" for b in st["by_signal"].values()))

# D2: record_id 去重(重复触发合并只算首日)
f = make_log([{"run_date": DATES[0], "ticker": "AAA", "signal": "S1",
               "strength": "red", "record_id": "R1"},
              {"run_date": DATES[1], "ticker": "AAA", "signal": "S1",
               "strength": "red", "record_id": "R1"}])
check("D2 去重后1条且取首日", len(load_log_entries(f)) == 1
      and load_log_entries(f)[0]["run_date"] == DATES[0])

# D3: S1 恒命中(涨票, 预期+) 6条成熟 → active
f = make_log(rows_for("S1", "AAA", DATES[:6], "a"))
st, oc = scan(f, fetch_stub)
b = st["by_signal"]["S1"]
check("D3a S1 6成熟全命中 -> active", b["state"] == "active" and b["rolling_n"] == 6
      and b["rolling_hit_rate"] == 1.0, str(b))
check("D3b outcome 数值正确(20日超额>0)",
      all(o["hit"] and o["excess_20d"] > 0 for o in oc if o["status"] == "mature"))

# D4: S1 全未中(跌票, 预期+) 8条成熟 → decayed
f = make_log(rows_for("S1", "BBB", DATES[:8], "b"))
st, _ = scan(f, fetch_stub)
check("D4 8成熟命中0% -> decayed", st["by_signal"]["S1"]["state"] == "decayed")

# D5: S2 预期为负: 跌票=命中
f = make_log(rows_for("S2", "BBB", DATES[:6], "c"))
st, _ = scan(f, fetch_stub)
check("D5 S2 跌票6成熟全命中 -> active",
      st["by_signal"]["S2"]["state"] == "active"
      and st["by_signal"]["S2"]["rolling_hit_rate"] == 1.0)

# D6: 混合命中率落入 40-55% → monitoring (6中/13 ≈ 46%... 用 12窗: 6/12=50%)
rows = rows_for("S3", "AAA", DATES[:6], "d") + rows_for("S3", "BBB", DATES[6:12], "e")
st, _ = scan(make_log(rows), fetch_stub)
check("D6 命中50%(n=12) -> monitoring", st["by_signal"]["S3"]["state"] == "monitoring",
      str(st["by_signal"]["S3"]))

# D7: 未成熟(信号日太近, 20交易日没走满) → pending 不计入
f = make_log(rows_for("S1", "AAA", DATES[-3:], "f"))
st, oc = scan(f, fetch_stub)
check("D7 未成熟 -> pending+样本不足active",
      st["by_signal"]["S1"]["pending"] == 3 and st["by_signal"]["S1"]["rolling_n"] == 0)

# D8: 缺价票 → no_price 不崩
f = make_log(rows_for("S1", "ZZZ", DATES[:2], "g"))
st, _ = scan(f, fetch_stub)
check("D8 缺价 -> no_price 计数", st["by_signal"]["S1"]["no_price"] == 2)

# D9: 人工 disabled 位保留
prev = {"by_signal": {"S1": {"disabled": True}}}
f = make_log(rows_for("S1", "AAA", DATES[:6], "h"))
st, _ = scan(f, fetch_stub, prev_state=prev)
check("D9 disabled 位扫描不改写", st["by_signal"]["S1"]["disabled"] is True)

# D10: 港股走自身日历路径不崩
f = make_log(rows_for("S1", "09XX.HK", DATES[:3], "i"))
st, oc = scan(f, fetch_stub)
check("D10 港股成熟样本可算", all(o["status"] == "mature" for o in oc), str(oc))

n_pass = sum(1 for _, ok in PASS if ok)
print(f"\n==== {n_pass}/{len(PASS)} PASS ====")
sys.exit(0 if n_pass == len(PASS) else 1)
