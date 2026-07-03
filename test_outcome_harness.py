"""
test_outcome_harness.py — 沙箱确定性验证
======================================================================
对**合成数据**（已知真值）校验纯数学层。不碰 baostock/网络。
在国内机器可直接 `poetry run python test_outcome_harness.py` 复跑。

覆盖：signal_to_score / forward_return / IC / RankIC / ICIR(单日→nan) /
      hit_rate / 费用模型 / 交易日历 t+h / build_panel 端到端。
"""
import sys, math
sys.path.insert(0, ".")          # 让 `from src.eval...` 可用
sys.path.insert(0, "/tmp/aihf")  # 沙箱路径（国内机器上这行无害）

import numpy as np
import pandas as pd

from src.eval import metrics, fees
from src.eval.data import nth_trading_day_on_or_after
from src.eval.harness import build_panel
from src.eval.signals import Signal

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name)

print("== 1. signal_to_score ==")
check("bullish@72 -> 0.72", abs(metrics.signal_to_score("bullish", 72) - 0.72) < 1e-9)
check("bearish@50 -> -0.5", abs(metrics.signal_to_score("bearish", 50) + 0.5) < 1e-9)
check("neutral -> 0", metrics.signal_to_score("neutral", 90) == 0.0)
check("中文'偏空'@80 -> -0.8", abs(metrics.signal_to_score("偏空", 80) + 0.8) < 1e-9)
check("未知方向 -> 0（不臆造）", metrics.signal_to_score("???", 90) == 0.0)
check("置信度已是0-1也能处理", abs(metrics.signal_to_score("bullish", 0.72) - 0.72) < 1e-9)

print("== 2. forward_return ==")
check("10->11 = +10%", abs(metrics.forward_return(10, 11) - 0.1) < 1e-12)
check("entry=0 -> nan", math.isnan(metrics.forward_return(0, 11)))
check("excess: 8% - 3% = 5%", abs(metrics.excess_return(0.08, 0.03) - 0.05) < 1e-12)

print("== 3. 截面 IC / RankIC ==")
s = pd.Series([0.9, 0.5, 0.1, -0.3, -0.8])   # 信号分
r_lin = s * 0.1                               # 线性关系 -> Pearson=1
r_mono = pd.Series([0.20, 0.06, 0.02, -0.01, -0.05])  # 单调非线性 -> Pearson<1 但 RankIC=1
check("线性关系 Pearson IC≈1", abs(metrics.cross_sectional_ic(s, r_lin) - 1.0) < 1e-9)
check("单调非线性 RankIC≈1（Pearson 会<1）", abs(metrics.cross_sectional_rank_ic(s, r_mono) - 1.0) < 1e-9)
check("单调非线性 Pearson 确实<1（RankIC 的意义）", metrics.cross_sectional_ic(s, r_mono) < 0.999)
check("反向 RankIC≈-1", abs(metrics.cross_sectional_rank_ic(s, -r_mono) + 1.0) < 1e-9)
check("样本不足 min_n -> nan", math.isnan(metrics.cross_sectional_ic(s.head(2), r_lin.head(2))))
# 与 scipy 交叉核对（仅沙箱）
try:
    from scipy.stats import spearmanr
    sp = spearmanr(s, r_mono).statistic
    check("RankIC 与 scipy.spearmanr 一致", abs(metrics.cross_sectional_rank_ic(s, r_mono) - sp) < 1e-9)
except ImportError:
    print("  (跳过 scipy 交叉核对)")

print("== 4. ICIR（诚实：单日→nan）==")
single = pd.Series([0.3], index=["2026-07-01"])
check("单日截面 ICIR = nan", math.isnan(metrics.icir(single)))
multi = pd.Series([0.2, 0.3, 0.1, 0.25])
check("多日 ICIR = mean/std", abs(metrics.icir(multi) - multi.mean()/multi.std(ddof=1)) < 1e-9)

print("== 5. 命中率 ==")
sc = pd.Series([0.8, -0.5, 0.3, 0.0, -0.9])
rr = pd.Series([0.05, -0.02, -0.01, 0.99, -0.03])  # 第3个方向错，第4个neutral不计
hr = metrics.hit_rate(sc, rr)
check("方向样本=4（neutral剔除）", hr["directional"] == 4)
check("命中=3", hr["hits"] == 3)
check("命中率=0.75", abs(hr["hit_rate"] - 0.75) < 1e-9)

print("== 6. 费用模型 ==")
cn = fees.AShareFeeModel()
# 10万元买入：佣金 max(100000*0.00025,5)=25 ；过户 100000*0.00001=1 ；买入=26
check("A股买入10万成本=26元", abs(cn.buy_cost(100000) - 26.0) < 1e-9)
# 卖出：佣金25 + 印花 100000*0.0005=50 + 过户1 = 76
check("A股卖出10万成本=76元", abs(cn.sell_cost(100000) - 76.0) < 1e-9)
check("A股往返10万=102元", abs(cn.round_trip_cost(100000) - 102.0) < 1e-9)
check("小额佣金触发最低5元", abs(cn.buy_cost(1000) - (5 + 1000*0.00001)) < 1e-9)
check("路由: .HK -> 港股模型", isinstance(fees.fee_model_for("09880.HK"), fees.HKFeeModel))
check("路由: .SZ -> A股模型", isinstance(fees.fee_model_for("300308.SZ"), fees.AShareFeeModel))

print("== 7. 交易日历 t+h（跳周末）==")
cal = ["2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03",
       "2026-07-06", "2026-07-07"]  # 07-04/05 周末不在列
check("07-01 + 0 交易日 = 07-01", nth_trading_day_on_or_after("2026-07-01", 0, cal) == "2026-07-01")
check("07-01 + 3 交易日 = 07-06（跨周末）", nth_trading_day_on_or_after("2026-07-01", 3, cal) == "2026-07-06")
check("日历不够长 -> None", nth_trading_day_on_or_after("2026-07-01", 99, cal) is None)

print("== 8. build_panel 端到端（mock 价格）==")
# 构造：300308 信号日 07-01 收 10，5日后 07-08 收 11（+10%）；基准同期 +2%
cal2 = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07", "2026-07-08"]
px = {"300308.SZ": pd.Series({"2026-07-01": 10.0, "2026-07-08": 11.0})}
bench = pd.Series({"2026-07-01": 100.0, "2026-07-08": 102.0})
sigs = [Signal(date="2026-07-01", ticker="300308.SZ", direction="bullish",
               confidence=70, source="portfolio_manager", stock_class="T")]
panel = build_panel(sigs, px, bench, cal2, horizons=[5])
row = panel.iloc[0]
check("前瞻收益 = +10%", abs(row["fwd_ret"] - 0.10) < 1e-9)
check("超额 = 10% - 2% = 8%", abs(row["excess_ret"] - 0.08) < 1e-9)
check("score = 0.7", abs(row["score"] - 0.7) < 1e-9)
# 缺价的港股应被跳过
sigs2 = sigs + [Signal(date="2026-07-01", ticker="09880.HK", direction="bullish", confidence=50)]
panel2 = build_panel(sigs2, px, bench, cal2, horizons=[5])
check("缺价 ticker 被跳过（不臆造）", len(panel2) == 1)

print("\n" + "=" * 50)
print(f"结果: {len(PASS)} 通过, {len(FAIL)} 失败")
if FAIL:
    print("失败项:", FAIL); sys.exit(1)
print("✅ 纯数学层全部通过。baostock 数据层需国内机器实跑验证。")
