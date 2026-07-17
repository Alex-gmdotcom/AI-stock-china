# -*- coding: utf-8 -*-
"""
validate_specs.py — SDD 第④步 /validate-changes-match-specs 机械化验证器 v1
================================================================
对照面: PRODUCT_v1.1.md / TECH_v1.1.md(含 2026-07-16 规格同步: 裁决⑥-⑨, 坑23-28,
       §10.2 as-built, §8.6 修③, §13.1 研究回路)  vs  实现代码。
四层校验(全部零网络, 真机 poetry 环境直接跑):
  A. 规格文件完整性 —— 裁决/坑/章节 marker 必须在档
  B. 代码 marker 存在性 —— 规格声明的每个幂等哨兵在对应文件里
  C. 常量对账 —— 规格写死的阈值/参数 与 代码常量逐一相等(import 真模块)
  D. 行为回归 —— 四套确定性单测(24+18+17+11=70 组)全绿
不通过项逐条列出; 任一 FAIL → 退出码非 0。
运行: cd E:\\AI-tool\\Stock\\ai-hedge-fund && poetry run python validate_specs.py
Step 25(41 条不变量人工签字)仍为 Alex 人工步骤, 本器不代签。
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv
    load_dotenv()                      # 入口纪律(坑27)
except Exception:
    pass

RESULTS = []


def check(section, name, ok, detail=""):
    RESULTS.append((section, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {section} | {name}" + ("" if ok else f"  <- {detail}"))


def file_has(path, needles, section):
    try:
        txt = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        check(section, f"{path} 存在", False, "文件缺失")
        return
    for n in needles:
        check(section, f"{path} :: {n}", n in txt)


# ════ A. 规格文件完整性 ════
print("== A. 规格文件完整性 ==")
file_has("TECH_v1.1.md",
         ["裁决⑥", "裁决⑦", "裁决⑧", "裁决⑨",
          "| **23** |", "| **24** |", "| **25** |", "| **26** |", "| **27** |", "| **28** |",
          "### 10.2 迁移信号检测（as-built", "### 8.6 technical agent 修③",
          "### 13.1 研究回路评估层 as-built",
          "eval 分段点：2026-07-16", "18c-core-v1.2"], "A")
file_has("PRODUCT_v1.1.md", ["裁决⑥", "裁决⑦", "裁决⑧", "裁决⑨", "维持 41 条"], "A")

# ════ B. 代码 marker 存在性(规格 ↔ 代码 双向锚) ════
print("== B. 代码 marker ==")
MARKER_MAP = {
    "src/strategy/migration_signals.py": ["S3B_FORCE_REV_V1", '18c-core-v1.2'],
    "src/strategy/signal_inputs.py": ["S3B_FORCE_REV_V1", "v1.4"],
    "src/agents/technicals.py": ["TECH_FIX3_V1"],
    "src/eval/harness.py": ["ENTRYPOINT_DOTENV_V1", "EVAL_HK_PRICE_V1"],
    "src/eval/data.py": ["EVAL_HK_PRICE_V1", "OHLC_SANITY_V1"],
    "src/eval/metrics.py": ["EVAL_RANDOM_CONTROL_V1"],
    "src/tools/api.py": ["OHLC_SANITY_V1"],
    "src/tools/api_china.py": ["MAINFLOW_TUSHARE_V1", "SAFE_CONSTRUCT_DEDUP_V1",
                               "HKFIN_INDICATOR_V1"],
    "src/tools/tushare_data.py": ["MAINFLOW_TUSHARE_V1", "TUSHARE_HK_DAILY_V3"],
    "src/agents/china_capital_flow.py": ["MAINFLOW_TUSHARE_V1", "CAPFLOW_DECISION7_V1"],
    "run_signal_decay_scan.py": ["SIGNAL_DECAY_SCAN_V1"],
    "src/web_app.py": ["STEP18_POOL_PAGE_V3", "STEP18C_LAMP_V2", "POOL_FULL_GUARD_V1"],
}
for path, mks in MARKER_MAP.items():
    file_has(path, mks, "B")
# web_app 满员护栏须 4 处齐(HTML 注释 + wire 逻辑 + render CNT + CSS)
try:
    wa = Path("src/web_app.py").read_text(encoding="utf-8", errors="replace")
    check("B", "web_app POOL_FULL_GUARD_V1 x4", wa.count("POOL_FULL_GUARD_V1") == 4,
          f"实际 {wa.count('POOL_FULL_GUARD_V1')} 处")
    check("B", "web_app 陈旧文案已清除(信号引擎待建)", "信号引擎待建" not in wa)
except FileNotFoundError:
    check("B", "src/web_app.py 存在", False)

# ════ C. 常量对账(规格值 ↔ import 真模块) ════
print("== C. 常量对账 ==")
try:
    from src.strategy import migration_signals as ms
    SPEC_MS = {"S1_YELLOW": 0.15, "S1_RED": 0.20, "S1_OFF": 0.12,
               "S3_YOY_PROXY": 0.30, "S3_REV_PROXY": 0.30,
               "S3_RET20_YELLOW": 0.10, "S3_RET20_RED": 0.20,
               "COOLDOWN_DAYS": 5, "MAX_RED_HIGHLIGHT": 3}
    for k, v in SPEC_MS.items():
        actual = getattr(ms, k, None)
        check("C", f"18c {k} == {v}", actual is not None and abs(float(actual) - v) < 1e-9,
              f"实际 {actual}")
    check("C", "引擎版本 18c-core-v1.2", ms.__version__.startswith("18c-core-v1.2"),
          ms.__version__)
except Exception as exc:
    check("C", "migration_signals 常量组", False, str(exc)[:80])

try:
    from src.agents import technicals as te
    SPEC_TE = {"TECH_LOOKBACK_CAL_DAYS": 460, "MR_ADX_GATE": 25.0,
               "MR_TREND_RELIABILITY": 0.3, "TREND_CONF_ADX_SCALE": 25.0,
               "MOM_VOL_SMOOTH_DAYS": 5}
    for k, v in SPEC_TE.items():
        actual = getattr(te, k, None)
        check("C", f"修③ {k} == {v}", actual is not None and abs(float(actual) - v) < 1e-9,
              f"实际 {actual}")
    check("C", "修③ 基准 = 000300.SH", te.TECH_BENCHMARK == "000300.SH", te.TECH_BENCHMARK)
except Exception as exc:
    check("C", "technicals 常量组", False, str(exc)[:80])

try:
    import run_signal_decay_scan as dc
    SPEC_DC = {"OUTCOME_H": 20, "ROLLING_N": 12, "MIN_N": 5,
               "HIT_ACTIVE": 0.55, "HIT_DECAYED": 0.40, "MIN_N_DECAYED": 8}
    for k, v in SPEC_DC.items():
        actual = getattr(dc, k, None)
        check("C", f"衰减 {k} == {v}", actual is not None and abs(float(actual) - v) < 1e-9,
              f"实际 {actual}")
    check("C", "衰减预期方向 S1+/S3+/S2-",
          dc.EXPECTED_DIR == {"S1": 1.0, "S3": 1.0, "S2": -1.0}, str(dc.EXPECTED_DIR))
except Exception as exc:
    check("C", "decay_scan 常量组", False, str(exc)[:80])

try:
    import dataclasses
    from src.tools.api_china import MainCapitalFlow
    check("C", "MainCapitalFlow.source 字段(裁决⑨口径标注)",
          "source" in [f.name for f in dataclasses.fields(MainCapitalFlow)])
    from src.eval.data import hk_closes_via_api_china, nth_trading_day_in_series  # noqa
    check("C", "eval 港股符号可导入(EVAL_HK_PRICE_V1)", True)
    from src.eval.metrics import random_ic_control  # noqa
    check("C", "random_ic_control 可导入(EVAL_RANDOM_CONTROL_V1)", True)
except Exception as exc:
    check("C", "符号导入组", False, str(exc)[:80])

# ════ D. 行为回归(四套确定性单测) ════
print("== D. 行为回归 ==")
SUITES = {"test_migration_signals.py": 24, "test_technicals_fix3.py": 18,
          "test_mainflow_chain.py": 17, "test_decay_scan.py": 11}
for suite, expected in SUITES.items():
    if not Path(suite).exists():
        check("D", suite, False, "缺文件")
        continue
    r = subprocess.run([sys.executable, suite], capture_output=True, text=True, timeout=600)
    tail = (r.stdout or "").strip().splitlines()
    verdict = tail[-1] if tail else ""
    ok = r.returncode == 0 and f"{expected}/{expected} PASS" in verdict
    check("D", f"{suite} ({expected} 组)", ok, verdict[:60] or (r.stderr or "")[:60])

# ════ 汇总 ════
n_fail = sum(1 for *_, ok, _ in [(a, b, c, d) for a, b, c, d in RESULTS] if not ok)
n_all = len(RESULTS)
print("=" * 56)
print(f"SDD ④ 机械化验证: {n_all - n_fail}/{n_all} PASS" + ("" if not n_fail else f", {n_fail} FAIL:"))
for sec, name, ok, detail in RESULTS:
    if not ok:
        print(f"  ✗ [{sec}] {name}  {detail}")
if n_fail == 0:
    print("规格↔实现 全项对齐。Step 25(41 条不变量人工签字)仍需 Alex 人工完成。")
sys.exit(1 if n_fail else 0)
