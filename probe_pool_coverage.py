# -*- coding: utf-8 -*-
"""
probe_pool_coverage.py — 只读探测:用你三分法池里的真实 A 股测 Baostock 覆盖。
不改任何文件。用法(项目根目录下):
    cd E:\\AI-tool\\Stock\\ai-hedge-fund
    poetry run python probe_pool_coverage.py
"""
import os, sys, traceback
ROOT = os.getcwd()
sys.path.insert(0, ROOT)

try:
    from src.tools import baostock_data as bsd
except Exception:
    print("import baostock_data 失败:"); print(traceback.format_exc()); sys.exit(1)

print("baostock available:", bsd.available())
if not bsd.available():
    print(">>> baostock 不可用,先确认已部署+已装包"); sys.exit(1)

# 三分法池 A 股(港股 09880/09660 Baostock 无,略)+ 板块代表
POOL = [
    ("002444.SZ", "V 巨星(主板)"),  ("600660.SH", "V 福耀(主板)"),
    ("000333.SZ", "V 美的(主板)"),  ("000921.SZ", "V 海信(主板)"),
    ("600887.SH", "V 伊利(主板)"),
    ("300308.SZ", "T 中际旭创(创业板)"), ("002008.SZ", "T 大族(主板)"),
    ("603228.SH", "T 景旺(主板)"), ("300502.SZ", "T 新易盛(创业板)"),
    ("688019.SH", "T 安集(科创板)"),
    ("601985.SH", "N 中国核电(主板)"), ("002050.SZ", "N 三花(主板)"),
    ("002594.SZ", "N 比亚迪(主板)"),
    ("430047.BJ", "BJ 诺思兰德(北交所)"), ("920019.BJ", "BJ 英特科技(北交所新920)"),
]

print("\n%-14s %-22s %6s %6s %10s %8s" % ("代码","名称(板块)","日线","季报","末收","PE"))
print("-" * 76)
miss = []
for norm, label in POOL:
    try:
        rows = bsd.get_prices_dicts(norm, "2026-06-01", "2026-06-20")
        qs = bsd.get_quarters(norm, "2026-06-22", limit=1)
        val = bsd.latest_valuation(norm, "2026-06-22")
        last = rows[-1]["close"] if rows else None
        pe = val.get("pe")
        flag = "" if (rows and qs) else "  ❌缺"
        if not (rows and qs):
            miss.append((norm, label))
        print("%-14s %-22s %6d %6d %10s %8s%s" % (
            norm, label, len(rows), len(qs),
            ("%.2f" % last) if last else "-",
            ("%.2f" % pe) if pe else "-", flag))
    except Exception as e:
        miss.append((norm, label))
        print("%-14s %-22s  异常: %s" % (norm, label, str(e)[:50]))

print("-" * 76)
if miss:
    print("覆盖缺口(需兜底):")
    for n, l in miss:
        print("  ❌", n, l)
else:
    print("✅ 全池 Baostock 覆盖完整(价格+季报)")
print("\n>>> 把这张表贴回 <<<")
