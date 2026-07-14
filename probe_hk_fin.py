# -*- coding: utf-8 -*-
"""
probe_hk_fin.py — 港股财务链分诊探针(只读, 不改项目文件)
================================================================
待区分:
  A) stock_financial_hk_report_em 被东财反爬掐连接(ERROR: RemoteDisconnected 等)
  B) 接口通但空返回 / 列名漂移(EMPTY 或列对不上 营业额/股东应占溢利)
  C) 备选 stock_financial_hk_analysis_indicator_em 是否可用
     (它直接含 营业总收入同比增长率/净利润同比增长率, 若通则一步到位)
运行: poetry run python probe_hk_fin.py
输出: hk_fin_probe2.txt (utf-8; 控制台只打 ASCII)
"""
import sys
from pathlib import Path

_lines = []
def log(s=""): _lines.append(str(s))

# NO_PROXY 注入(复用项目, 失败手动兜底)
try:
    sys.path.insert(0, "src")
    from markets.proxy import inject_no_proxy  # type: ignore
    inject_no_proxy()
    log("NO_PROXY: 项目注入 OK")
except Exception as e:
    import os
    extra = ("eastmoney.com,push2.eastmoney.com,datacenter-web.eastmoney.com,"
             "emweb.securities.eastmoney.com,f10.eastmoney.com")
    os.environ["NO_PROXY"] = os.environ.get("NO_PROXY", "") + "," + extra
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    log(f"NO_PROXY: 手动兜底({type(e).__name__})")

import akshare as ak
log(f"akshare {getattr(ak, '__version__', '?')}")

def report(name, fn):
    try:
        df = fn()
    except Exception as e:
        log(f"[{name}] ERROR {type(e).__name__}: {str(e)[:220]}")
        return
    if df is None or len(df) == 0:
        log(f"[{name}] EMPTY")
        return
    cols = list(df.columns)
    log(f"[{name}] {len(df)} 行, 列({len(cols)}): {cols[:18]}")
    try:
        log("  首2行: " + df.head(2).to_string(index=False)[:700].replace("\n", " ┃ "))
    except Exception:
        pass

TESTS = [
    ("report_em 09880 年度",
     lambda: ak.stock_financial_hk_report_em(stock="09880", symbol="利润表", indicator="年度")),
    ("report_em 09880 报告期",
     lambda: ak.stock_financial_hk_report_em(stock="09880", symbol="利润表", indicator="报告期")),
    ("report_em 09660 年度",
     lambda: ak.stock_financial_hk_report_em(stock="09660", symbol="利润表", indicator="年度")),
    ("indicator_em 09880 年度",
     lambda: ak.stock_financial_hk_analysis_indicator_em(symbol="09880", indicator="年度")),
    ("indicator_em 09880 报告期",
     lambda: ak.stock_financial_hk_analysis_indicator_em(symbol="09880", indicator="报告期")),
    ("indicator_em 09660 年度",
     lambda: ak.stock_financial_hk_analysis_indicator_em(symbol="09660", indicator="年度")),
]
for name, fn in TESTS:
    report(name, fn)

log()
log("判读: report_em 全 ERROR/EMPTY 且 indicator_em OK -> 修类方向=报表链换 indicator 链;")
log("      两者全 ERROR(RemoteDisconnected) -> 东财反爬掐 f10 域, 考虑 curl 头或缓存策略;")
log("      report_em OK -> 根因仅为 _fetch_hk_metrics 丢字段的构造缺陷, 直接补字段。")

Path("hk_fin_probe2.txt").write_text("\n".join(_lines), encoding="utf-8")
print("probe done -> hk_fin_probe2.txt")
