# -*- coding: utf-8 -*-
"""test_migration_signals.py — 18c 核心引擎确定性单测(零网络零 LLM; 含 S3-B 组)
运行: poetry run python test_migration_signals.py  (仓库根目录)
"""
import sys
sys.path.insert(0, "src/strategy")
sys.path.insert(0, ".")
try:
    from src.strategy.migration_signals import evaluate_pool, COOLDOWN_DAYS
except Exception:
    from migration_signals import evaluate_pool, COOLDOWN_DAYS

PASS = []


def check(name, cond, detail=""):
    PASS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def sig_of(signals, ticker):
    for s in signals:
        if s.ticker == ticker:
            return s
    return None


MB_FLAT = [1000.0] * 25                                   # 融资余额平稳
_osc = [1000.0]
for _i in range(21):                                       # ±10 日常波动历史
    _osc.append(_osc[-1] + (10.0 if _i % 2 == 0 else -10.0))
MB_DOWN3 = _osc + [_osc[-1] - 5, _osc[-1] - 10, _osc[-1] - 15]  # 连降3日(-5/日, 在±10波动的2σ内)
MB_CRASH = [1000.0 + (i % 2) for i in range(22)] + [960.0, 920.0, 880.0]  # 3日暴降(远超2σ)

# ---- T1/T2: S1 黄/红分档 ----
pool = {"AAA": "V", "BBB": "V"}
data = {"AAA": {"ret_5d": 0.16}, "BBB": {"ret_5d": 0.21}}
sigs, st = evaluate_pool(pool, data, None, "2026-07-13")
check("T1 S1 16%->黄", sig_of(sigs, "AAA") and sig_of(sigs, "AAA").strength == "yellow")
check("T2 S1 21%->红", sig_of(sigs, "BBB") and sig_of(sigs, "BBB").strength == "red")

# ---- T3: S1 迟滞(13% 驻留 / 11% 熄灯) ----
sigs2, st2 = evaluate_pool({"AAA": "V"}, {"AAA": {"ret_5d": 0.13}}, st, "2026-07-14")
check("T3a 迟滞: 回落13% 仍亮黄", sig_of(sigs2, "AAA") and sig_of(sigs2, "AAA").strength == "yellow")
sigs3, st3 = evaluate_pool({"AAA": "V"}, {"AAA": {"ret_5d": 0.11}}, st2, "2026-07-15")
check("T3b 迟滞: <12% 熄灯", sig_of(sigs3, "AAA") is None)

# ---- T4: 冷却期压制 + 期满重触发 ----
d = "2026-07-15"
stx = st3
suppressed = True
for i in range(COOLDOWN_DAYS):                              # 冷却 5 个运行日内再冲 16%
    d = f"2026-07-{16 + i:02d}"
    s, stx = evaluate_pool({"AAA": "V"}, {"AAA": {"ret_5d": 0.16}}, stx, d)
    if i < COOLDOWN_DAYS - 1 and sig_of(s, "AAA") is not None:
        suppressed = False
check("T4a 冷却期内重触发被压制", suppressed)
s, stx = evaluate_pool({"AAA": "V"}, {"AAA": {"ret_5d": 0.16}}, stx, "2026-07-22")
check("T4b 冷却期满重新亮灯", sig_of(s, "AAA") and sig_of(s, "AAA").strength == "yellow")

# ---- T5: S2 黄灯(连降3日 AND 排名下滑>5) ----
pool2 = {"TTT": "T"}
s, st5 = evaluate_pool(pool2, {"TTT": {"margin_balance": MB_DOWN3, "sector_rank_change_5d": 6}}, None, "2026-07-13")
check("T5 S2 连降3日+排名降6 ->黄", sig_of(s, "TTT") and sig_of(s, "TTT").strength == "yellow")

# ---- T6: S2 红灯(3日降幅>2σ) + 转增熄灯 ----
s, st6 = evaluate_pool(pool2, {"TTT": {"margin_balance": MB_CRASH, "sector_rank_change_5d": 8}}, None, "2026-07-13")
check("T6a S2 暴降>2σ ->红", sig_of(s, "TTT") and sig_of(s, "TTT").strength == "red",
      detail=str(sig_of(s, "TTT").evidence if sig_of(s, "TTT") else None))
s, st6b = evaluate_pool(pool2, {"TTT": {"margin_balance": MB_CRASH + [900.0], "sector_rank_change_5d": 8}}, st6, "2026-07-14")
check("T6b S2 余额转增1日即熄", sig_of(s, "TTT") is None)

# ---- T7: S2 灰灯(margin 缺失, 不给假绿) ----
s, _ = evaluate_pool(pool2, {"TTT": {"margin_balance": None, "sector_rank_change_5d": 6}}, None, "2026-07-13")
g = sig_of(s, "TTT")
check("T7 S2 数据缺失 ->灰灯+missing", g and g.strength == "gray" and g.note.startswith("信号不可算"))

# ---- T8: S3 黄/红 + 事件型冷却(次日不重复) ----
pool3 = {"NNN": "N", "MMM": "N"}
d8 = {"NNN": {"net_profit_yoy": 0.35, "ret_20d": 0.12, "consensus_beat": None},
      "MMM": {"net_profit_yoy": 0.35, "ret_20d": 0.22, "consensus_beat": None}}
s, st8 = evaluate_pool(pool3, d8, None, "2026-07-13")
check("T8a S3 YoY35%+ret20=12% ->黄", sig_of(s, "NNN") and sig_of(s, "NNN").strength == "yellow")
check("T8b S3 ret20=22% ->红", sig_of(s, "MMM") and sig_of(s, "MMM").strength == "red")
s, _ = evaluate_pool(pool3, d8, st8, "2026-07-14")
check("T8c S3 事件型: 次日同条件不重复亮", sig_of(s, "NNN") is None and sig_of(s, "MMM") is None)

# ---- T9: 重复触发合并(record_id 不变) + 红灯高亮上限3 ----
pool9 = {f"R{i}": "V" for i in range(5)}
d9 = {f"R{i}": {"ret_5d": 0.20 + i * 0.01} for i in range(5)}
s, st9 = evaluate_pool(pool9, d9, None, "2026-07-13")
rid0 = sig_of(s, "R0").record_id
hl = [x.ticker for x in s if x.highlight]
check("T9a 5红灯仅高亮3个", sum(1 for x in s if x.highlight) == 3)
check("T9b 高亮取涨幅top3", set(hl) == {"R4", "R3", "R2"}, detail=str(hl))
s2, _ = evaluate_pool(pool9, d9, st9, "2026-07-14")
check("T9c 重复触发合并: record_id 不变", sig_of(s2, "R0").record_id == rid0)
check("T9d 幂等: 同日重跑结果一致",
      [x.to_dict() for x in evaluate_pool(pool9, d9, st9, "2026-07-14")[0]] == [x.to_dict() for x in s2])

# ---- T10: S3 未盈利降级(营收YoY代理, 2026-07-14 批) ----
pool10 = {"RVA": "N", "RVB": "N", "RVC": "N"}
d10 = {"RVA": {"net_profit_yoy": None, "revenue_yoy": 0.35, "ret_20d": 0.12, "consensus_beat": None},
       "RVB": {"net_profit_yoy": None, "revenue_yoy": 0.20, "ret_20d": 0.15, "consensus_beat": None},
       "RVC": {"net_profit_yoy": 0.10, "revenue_yoy": 0.50, "ret_20d": 0.15, "consensus_beat": None}}
s, _ = evaluate_pool(pool10, d10, None, "2026-07-14")
_a = sig_of(s, "RVA")
check("T10a 未盈利+营收YoY35% ->黄(标proxy)", _a and _a.strength == "yellow"
      and _a.evidence.get("proxy", "").startswith("revenue_yoy"))
check("T10b 未盈利+营收YoY20% ->不触发", sig_of(s, "RVB") is None)
check("T10c 有净利YoY(10%)不降级营收 ->不触发", sig_of(s, "RVC") is None)

# ---- T11: S3-B 未盈利强制营收口径(2026-07-15 批) ----
pool11 = {"LSA": "N", "LSB": "N", "LSC": "N", "LSD": "N"}
d11 = {"LSA": {"net_profit_yoy": 0.40, "revenue_yoy": 0.55, "ret_20d": 0.15,
               "consensus_beat": None, "net_profit_is_loss": True},
       "LSB": {"net_profit_yoy": 0.40, "revenue_yoy": 0.10, "ret_20d": 0.15,
               "consensus_beat": None, "net_profit_is_loss": True},
       "LSC": {"net_profit_yoy": 0.40, "revenue_yoy": None, "ret_20d": 0.15,
               "consensus_beat": None, "net_profit_is_loss": True},
       "LSD": {"net_profit_yoy": 0.35, "revenue_yoy": 0.10, "ret_20d": 0.15,
               "consensus_beat": None, "net_profit_is_loss": None}}
s, _ = evaluate_pool(pool11, d11, None, "2026-07-15")
_a = sig_of(s, "LSA")
check("T11a 未盈利+净利YoY40%(收窄)+营收55% ->触发且标强制", _a and _a.strength == "yellow"
      and _a.evidence.get("proxy") == "revenue_yoy(未盈利强制S3-B)")
check("T11b 未盈利+净利YoY40%(收窄)+营收10% ->不触发(漏洞关闭)", sig_of(s, "LSB") is None)
_c = sig_of(s, "LSC")
check("T11c 未盈利+营收缺失 ->灰灯", _c and _c.strength == "gray"
      and any("revenue_yoy" in m for m in _c.evidence.get("missing", [])))
_d = sig_of(s, "LSD")
check("T11d 盈利性不可判+净利YoY35% ->触发(原口径保留)", _d and _d.strength == "yellow"
      and "proxy" not in _d.evidence)

n_pass = sum(1 for _, ok in PASS if ok)
print(f"\n==== {n_pass}/{len(PASS)} PASS ====")
sys.exit(0 if n_pass == len(PASS) else 1)
