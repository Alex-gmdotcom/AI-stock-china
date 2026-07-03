# -*- coding: utf-8 -*-
"""沙箱验证:用 Alex 国内机器贴回的真实 tushare YTD 原值,确定性验证
_ttm_from_ytd 的 TTM 换算 + _a_share_by_period 的防覆盖逻辑。不连任何实时数据源。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
sys.path.insert(0, os.path.dirname(__file__))

import importlib
li = importlib.import_module("src.tools.line_items_china")

# ── 真实数据(688019 安集,来自探针②,YTD 累计)─────────────────────────
ANJI_YTD = {
    "2026-03-31": {"free_cash_flow": 30918509.41,  "net_income": 207668028.8,  "total_assets": 5454801096.53},
    "2025-12-31": {"free_cash_flow": 84394209.18,  "net_income": 783648337.86, "total_assets": 5038433221.1},
    "2025-09-30": {"free_cash_flow": 58494997.31,  "net_income": 608321608.16, "total_assets": 4776210275.41},
    "2025-06-30": {"free_cash_flow": 121167253.92, "net_income": 375634437.13, "total_assets": 4436653913.34},
    "2025-03-31": {"free_cash_flow": -8592885.30,  "net_income": 168825875.37},
    "2024-12-31": {"free_cash_flow": 225845095.01, "net_income": 533643641.69},
    "2024-09-30": {"free_cash_flow": 121730923.01, "net_income": 392568199.29},
    "2024-06-30": {"free_cash_flow": 52536954.75,  "net_income": 233995840.71},
}
# 中际旭创 300308.SZ(探针①,YTD 累计,部分科目)
ZJXC_YTD = {
    "2026-03-31": {"free_cash_flow": 1438374393.74, "net_income": 5734501526.83},
    "2025-12-31": {"free_cash_flow": 8136131464.12, "net_income": 10797254300.45},
    "2025-09-30": {"free_cash_flow": 3840282971.37, "net_income": 7131932436.7},
    "2025-06-30": {"free_cash_flow": 2264741243.25, "net_income": 3995115384.7},
    "2025-03-31": {"free_cash_flow": 1762896482.87, "net_income": 1582876128.67},
}

EPS = 0.5  # 浮点容差(元)
fails = []
def check(name, got, exp):
    if got is None or abs(got - exp) > EPS:
        fails.append(f"{name}: 期望 {exp:,.2f} 得 {got}")
    else:
        print(f"  ✓ {name} = {got:,.2f}")

# ── T1:_ttm_from_ytd 纯函数,安集 FCF ───────────────────────────────────
print("[T1] 安集 FCF: YTD → TTM")
ttm = li._ttm_from_ytd(ANJI_YTD, li._FLOW_FIELDS_TS)
check("FCF TTM @2026-03-31", ttm["2026-03-31"].get("free_cash_flow"), 30918509.41 + 84394209.18 - (-8592885.30))
check("FCF TTM @2025-12-31 (年末原样)", ttm["2025-12-31"].get("free_cash_flow"), 84394209.18)
check("FCF TTM @2025-09-30", ttm["2025-09-30"].get("free_cash_flow"), 58494997.31 + 225845095.01 - 121730923.01)
check("FCF TTM @2025-06-30", ttm["2025-06-30"].get("free_cash_flow"), 121167253.92 + 225845095.01 - 52536954.75)
check("净利 TTM @2026-03-31", ttm["2026-03-31"].get("net_income"), 207668028.8 + 783648337.86 - 168825875.37)

# ── T2:中际旭创 FCF TTM(主因是混口径 bug,看修复后 base 量级)──────────
print("[T2] 中际旭创 FCF: YTD → TTM")
ttm2 = li._ttm_from_ytd(ZJXC_YTD, li._FLOW_FIELDS_TS)
check("FCF TTM @2026-03-31", ttm2["2026-03-31"].get("free_cash_flow"), 1438374393.74 + 8136131464.12 - 1762896482.87)

# ── T3:fail-soft,缺回溯期保留原 YTD(2024-09-30 缺 2023 数据)────────────
print("[T3] fail-soft:缺回溯期保留原 YTD")
check("FCF @2024-09-30 (无 2023,保留原值)", ttm["2024-09-30"].get("free_cash_flow"), 121730923.01)

# ── T4:_a_share_by_period 集成 — 防覆盖 + 点时覆盖 ──────────────────────
print("[T4] 集成:baostock TTM 不被覆盖 + FCF 用 TTM 补 + 平衡表原样覆盖")
SENTINEL_NI = 888888888.88   # 模拟 baostock 已算好的 TTM 净利(哨兵值)

class _FakeBSD:
    @staticmethod
    def available(): return True
    @staticmethod
    def get_quarters(norm, asof, limit=8):
        # baostock 只在最近 limit 期提供:含 TTM 净利(哨兵),不含 FCF
        reps = sorted([r for r in ANJI_YTD if r <= asof], reverse=True)[:limit]
        return [{"statDate": r} for r in reps]
    @staticmethod
    def line_items_from_block(blk):
        return {"net_income": SENTINEL_NI}   # baostock TTM 净利,无 FCF

class _FakeTSD:
    @staticmethod
    def available(): return True
    @staticmethod
    def get_abs_periods(norm, asof, limit=8):
        return {r: dict(v) for r, v in ANJI_YTD.items() if r <= asof}

li._bsd = _FakeBSD()
li._tsd = _FakeTSD()
bp = li._a_share_by_period("688019.SH", "2026-06-25", 8)

latest = bp["2026-03-31"]
# 净利:baostock 哨兵保留,未被 tushare YTD(207M) 或 tushare TTM 覆盖
check("净利 @2026-03-31 = baostock 哨兵(防覆盖)", latest.get("net_income"), SENTINEL_NI)
if abs(latest.get("net_income", 0) - 207668028.8) < EPS:
    fails.append("净利被 tushare YTD 覆盖了 → 回退病未修!")
# FCF:baostock 没有 → 用 tushare TTM 补
check("FCF @2026-03-31 = tushare TTM(补缺)", latest.get("free_cash_flow"), 123905603.89)
# 平衡表点时:tushare 原样覆盖
check("total_assets @2026-03-31 = tushare 点时(原样)", latest.get("total_assets"), 5454801096.53)

# ── T5:模拟 valuation 的 fcf_history / base_fcf,修前 vs 修后 ──────────
print("[T5] DCF base_fcf:修前(混口径) vs 修后(TTM)")
def base_fcf(series):
    cur = series[0]; avg3 = sum(series[:3]) / min(3, len(series))
    return max(cur, avg3 * 0.85)
reps_desc = sorted(ANJI_YTD, reverse=True)
buggy = [ANJI_YTD[r]["free_cash_flow"] for r in reps_desc]              # 修前:混口径
fixed = [bp[r]["free_cash_flow"] for r in reps_desc if r in bp]         # 修后:TTM
print(f"  修前 base_fcf = {base_fcf(buggy):,.0f}  (混口径,单季Q1当全年)")
print(f"  修后 base_fcf = {base_fcf(fixed):,.0f}  (TTM)")
print(f"  → 抬升 {base_fcf(fixed)/base_fcf(buggy):.2f}x")

print()
if fails:
    print("❌ FAIL:")
    for f in fails: print("   -", f)
    sys.exit(1)
print("✅ 全部通过 — TTM 换算与防覆盖逻辑正确(基于真实数据,确定性验证)")
