# -*- coding: utf-8 -*-
"""沙箱验证:同一真实输入下,US 利率 vs CN 利率,看 valuation 内在值抬升 + 信号变化。
stub 掉 langchain 等重依赖,导入 valuation 的真纯函数(非复制),确保测的是上线代码。"""
import sys, types
from unittest.mock import MagicMock
for m in ["langchain_core","langchain_core.messages","src.tools.api","src.utils.progress",
          "src.graph.state","src.utils.llm","src.utils.api_key","pydantic"]:
    sys.modules.setdefault(m, MagicMock())
import os
_root = os.getcwd()  # 默认:从仓库根目录运行
if not os.path.isdir(os.path.join(_root, "src")):
    _root = os.path.dirname(os.path.abspath(__file__))  # 回退:脚本所在目录
sys.path.insert(0, _root)

from src.agents.valuation import (calculate_owner_earnings_value, calculate_wacc,
    calculate_residual_income_value, calculate_dcf_scenarios)
from src.markets.market_config import _US, _CN

B = 1e9  # 亿→十亿换算辅助(数值用元)
# 真实输入(安集688019 / 中际旭创300308,TTM 口径,来自探针;value 股为近似演示)
STOCKS = {
 "安集688019 (T,真实TTM)": dict(
    net_income=822.5e6, dep=150e6, capex=-400e6, wc=0, growth=0.15,
    fcf_history=[123.9e6,84.4e6,162.6e6,294.5e6], rev_g=0.30, fcf_g=0.10, earn_g=0.30,
    market_cap=48e9, total_debt=0.3e9, cash=1.68e9, int_cov=25, d2e=0.10,
    pb=10.4, bvg=0.15),
 "中际旭创300308 (T,真实TTM)": dict(
    net_income=14949e6, dep=910e6, capex=-2200e6, wc=0, growth=0.20,
    fcf_history=[7811e6,8136e6,3840e6,2264e6], rev_g=0.80, fcf_g=0.30, earn_g=0.60,
    market_cap=1393e9, total_debt=2e9, cash=12.2e9, int_cov=40, d2e=0.52,
    pb=6.7, bvg=0.30),
 "美的000333 (V,近似演示)": dict(
    net_income=39e9, dep=8e9, capex=-10e9, wc=0, growth=0.08,
    fcf_history=[30e9,28e9,26e9,24e9], rev_g=0.08, fcf_g=0.06, earn_g=0.10,
    market_cap=597e9, total_debt=30e9, cash=80e9, int_cov=30, d2e=0.40,
    pb=2.5, bvg=0.10),
}

def value_set(s, mc):
    wacc = calculate_wacc(s["market_cap"], s["total_debt"], s["cash"], s["int_cov"], s["d2e"],
                          risk_free_rate=mc.risk_free_rate, market_risk_premium=mc.equity_risk_premium)
    owner = calculate_owner_earnings_value(s["net_income"], s["dep"], s["capex"], s["wc"],
                          growth_rate=s["growth"], required_return=mc.required_return)
    dcf = calculate_dcf_scenarios(s["fcf_history"],
                          {"revenue_growth":s["rev_g"],"fcf_growth":s["fcf_g"],"earnings_growth":s["earn_g"]},
                          wacc=wacc, market_cap=s["market_cap"], revenue_growth=s["rev_g"])["expected_value"]
    rim = calculate_residual_income_value(s["market_cap"], s["net_income"], s["pb"],
                          book_value_growth=s["bvg"], cost_of_equity=mc.cost_of_equity)
    mv = {"dcf":(dcf,0.35),"owner_earnings":(owner,0.35),"ev_ebitda":(0,0.20),"residual_income":(rim,0.10)}
    tw = sum(w for v,w in mv.values() if v>0)
    wgap = sum(w*((v-s["market_cap"])/s["market_cap"]) for v,w in mv.values() if v>0)/tw if tw else 0
    sig = "bullish" if wgap>0.15 else "bearish" if wgap<-0.15 else "neutral"
    return wacc, owner, dcf, rim, wgap, sig

print(f"{'股票':28s}{'口径':4s}{'WACC':>6s}{'owner($亿)':>11s}{'DCF($亿)':>10s}{'RIM($亿)':>10s}{'加权gap':>8s}  信号")
for name,s in STOCKS.items():
    for tag,mc in [("US",_US),("CN",_CN)]:
        wacc,owner,dcf,rim,wgap,sig=value_set(s,mc)
        print(f"{name:28s}{tag:4s}{wacc:>6.1%}{owner/1e8:>11.0f}{dcf/1e8:>10.0f}{rim/1e8:>10.0f}{wgap:>+8.1%}  {sig}")
    print()
