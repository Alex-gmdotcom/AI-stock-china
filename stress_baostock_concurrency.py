# -*- coding: utf-8 -*-
"""
stress_baostock_concurrency.py — 只读:9 线程并发压 Baostock,验证单 socket 不串话。
当年 akshare 就是 9 路并发把 V8 干崩;Baostock 单 socket 多线程并发会串话,
本测试验证 baostock_data 的全局 RLock 是否真的把它们串行化、结果不错乱。
用法(项目根目录):
    cd E:\\AI-tool\\Stock\\ai-hedge-fund
    poetry run python stress_baostock_concurrency.py
"""
import os, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.getcwd(); sys.path.insert(0, ROOT)

try:
    from src.tools import baostock_data as bsd
except Exception:
    print("import 失败:"); print(traceback.format_exc()); sys.exit(1)

if not bsd.available():
    print(">>> baostock 不可用"); sys.exit(1)

ASOF = "2026-06-22"; START = "2026-06-01"; END = "2026-06-20"
# 9 只 A 股(模拟 9 agent 并行)
TICKERS = ["600519.SH","000333.SZ","300308.SZ","688019.SH","600660.SH",
           "000921.SZ","002594.SZ","601985.SH","002050.SZ"]

def fetch(norm):
    """一次完整取数:日线 + 季报 + 反推 + 估值。返回可比对的指纹。"""
    rows = bsd.get_prices_dicts(norm, START, END)
    qs = bsd.get_quarters(norm, ASOF, limit=1)
    li = bsd.line_items_from_block(qs[0]) if qs else {}
    m  = bsd.metrics_from_block(qs[0]) if qs else {}
    val = bsd.latest_valuation(norm, ASOF)
    return {
        "ticker": norm,                       # 自证:返回的票必须等于请求的票
        "n_daily": len(rows),
        "close": rows[-1]["close"] if rows else None,
        "revenue": round(li.get("revenue"), 2) if li.get("revenue") else None,
        "net_income": round(li.get("net_income"), 2) if li.get("net_income") else None,
        "net_margin": m.get("net_margin"),
        "pe": val.get("pe"),
    }

print("baostock available:", bsd.available())
print("\n[1] 单线程基线")
t0 = time.time()
baseline = {}
for t in TICKERS:
    baseline[t] = fetch(t)
    b = baseline[t]
    print("  %-12s 日线=%2d 末收=%-9s rev=%-10s npm=%s" % (
        t, b["n_daily"], b["close"], b["revenue"], b["net_margin"]))
print("  单线程耗时: %.1fs" % (time.time()-t0))

# ---- [2] 9 线程并发,不同票(最大 socket 交错压力)----
print("\n[2] 9 线程并发 × 不同票 × 3 轮 (验证不串话)")
mismatches = []
errors = []
t0 = time.time()
for rnd in range(3):
    with ThreadPoolExecutor(max_workers=9) as ex:
        futs = {ex.submit(fetch, t): t for t in TICKERS}
        for fu in as_completed(futs):
            req = futs[fu]
            try:
                r = fu.result()
            except Exception as e:
                errors.append((rnd, req, str(e)[:100])); continue
            # 串话检测①: 返回的 ticker 字段必须等于请求
            if r["ticker"] != req:
                mismatches.append((rnd, req, "ticker错位→"+str(r["ticker"])))
            # 串话检测②: 关键数值必须与单线程基线完全一致
            for k in ("n_daily","close","revenue","net_income","net_margin","pe"):
                if r[k] != baseline[req][k]:
                    mismatches.append((rnd, req, "%s: 基线%s≠并发%s" % (k, baseline[req][k], r[k])))
print("  并发耗时: %.1fs (3轮×9票)" % (time.time()-t0))

# ---- [3] 9 线程同时打同一只票(真实 agent 模式:一票被 9 agent 并行分析)----
print("\n[3] 9 线程并发 × 同一票 600519 (cache stampede 模式)")
with ThreadPoolExecutor(max_workers=9) as ex:
    same = list(ex.map(lambda _: fetch("600519.SH"), range(9)))
base = baseline["600519.SH"]
same_bad = [s for s in same if any(s[k]!=base[k] for k in ("n_daily","close","revenue","net_margin","pe"))]
if same_bad:
    mismatches.append(("same", "600519.SH", "9路同票结果不一致: %d/9 错" % len(same_bad)))
else:
    print("  9/9 结果一致 ✅")

print("\n" + "="*56)
if errors:
    print("并发异常 (%d):" % len(errors))
    for e in errors[:10]: print("  ✗", e)
if mismatches:
    print("串话/不一致 (%d):" % len(mismatches))
    for m in mismatches[:20]: print("  ✗", m)
if not errors and not mismatches:
    print("✅✅ 9 路并发无异常、无串话、结果与单线程完全一致")
    print("    → Baostock 单 socket 全局锁串行化成立,换地基彻底落地")
else:
    print("❌ 并发有问题,需排查(贴回完整输出)")
print("="*56)
print("\n>>> 把以上输出贴回 <<<")
