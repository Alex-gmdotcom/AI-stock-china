# -*- coding: utf-8 -*-
"""smoke_step16.py — Step 16 深度页确定性冒烟(离线,零外网)。

覆盖:编排组装 / agents 默认跳过 / I6.2 半数熔断(两侧) / 超时计失败 /
跨维回填 / 503 暂停页路由 / DCF 重算路由 / HTML 壳 / healthz 注册。
真机(国内)另跑 T-live:GET /stock/600519.SH/snapshot 真数据。
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

from src.analysis import snapshot as S

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {detail}" if detail and not cond else ""))


def ok_fetchers(gap_dim=None):
    def mk(n):
        def f():
            d = {"dummy": n}
            if n == "peers":
                d["industry_median"] = {"pe_ttm": 23.5}
            if n == "valuation":
                d = {"strip": {"ticker": "600519.SH", "price": 1215.0},
                     "cards": {"pe_ttm": 18.7}, "data_gaps": ["pe_5y_percentile 无稳定源(v1 已知缺口)"]}
            if n == gap_dim:
                d["data_gaps"] = ["模拟缺口【数据缺口】"]
            return d
        return f
    return {n: mk(n) for n in S.DIM_NAMES}


def failing(names):
    def boom():
        raise RuntimeError("模拟数据源失败")
    f = ok_fetchers()
    for n in names:
        f[n] = boom
    return f


print("== T1 全绿组装 + footer + 跨维回填 ==")
snap = asyncio.run(S.build_stock_snapshot("600519", _fetchers=ok_fetchers(gap_dim="unlock")))
check("T1a ticker 归一 600519.SH", snap["ticker"] == "600519.SH")
check("T1b market=CN", snap["market"] == "CN")
check("T1c agents 默认 skipped", snap["footer"]["dim_meta"]["agents"]["status"] == "skipped")
check("T1d 9 维 ok", sum(1 for m in snap["footer"]["dim_meta"].values() if m["status"] == "ok") == 9)
check("T1e 缺口汇总含 unlock 模拟缺口", any("unlock" in g and "模拟缺口" in g for g in snap["footer"]["data_gaps"]))
check("T1f 跨维回填 industry_median_pe", snap["dimensions"]["valuation"]["cards"].get("industry_median_pe") == 23.5)
check("T1g captured_at 存在", bool(snap["captured_at"]))

print("== T2 HK 归一 ==")
snap_hk = asyncio.run(S.build_stock_snapshot("09880.HK", _fetchers=ok_fetchers()))
check("T2a 09880.HK / HK", snap_hk["ticker"] == "09880.HK" and snap_hk["market"] == "HK")

print("== T3 I6.2 阈值:4/9 失败 < 50% → 降级不暂停 ==")
snap3 = asyncio.run(S.build_stock_snapshot("600519", _fetchers=failing(["kline", "peers", "capital", "news"])))
check("T3a 不抛异常", True)
check("T3b 失败维 = None + error 入 meta", snap3["dimensions"]["kline"] is None
      and "error" in snap3["footer"]["dim_meta"]["kline"])
check("T3c failed_dims 4 项", len(snap3["footer"]["failed_dims"]) == 4)
check("T3d 失败缺口进汇总", any("kline 维度失败" in g for g in snap3["footer"]["data_gaps"]))

print("== T4 I6.2 阈值:5/9 失败 ≥ 50% → MajorPageDataFailure ==")
try:
    asyncio.run(S.build_stock_snapshot(
        "600519", _fetchers=failing(["kline", "peers", "capital", "news", "dcf"])))
    check("T4a 应抛 MajorPageDataFailure", False)
except S.MajorPageDataFailure as e:
    check("T4a 抛出", True)
    check("T4b failed/attempted = 5/9", len(e.failed) == 5 and e.attempted == 9)
    check("T4c detail 含错误", all(v for v in e.detail.values()))

print("== T5 agents 开启后计入分母:5/10 失败 → 仍 ≥50% 暂停;4/10 → 通过 ==")
try:
    asyncio.run(S.build_stock_snapshot("600519", include_agents=True,
                _fetchers=failing(["kline", "peers", "capital", "news", "dcf"])))
    check("T5a 5/10 应暂停", False)
except S.MajorPageDataFailure as e:
    check("T5a 5/10 暂停", e.attempted == 10)
snap5 = asyncio.run(S.build_stock_snapshot("600519", include_agents=True,
                    _fetchers=failing(["kline", "peers", "capital", "news"])))
check("T5b 4/10 通过且 agents ok", snap5["footer"]["dim_meta"]["agents"]["status"] == "ok")

print("== T6 超时计失败 ==")
S.DIM_TIMEOUT_SAVED = S.DIM_TIMEOUT
S.DIM_TIMEOUT = 0.3
f6 = ok_fetchers()
f6["unlock"] = lambda: (time.sleep(2), {})[1]
snap6 = asyncio.run(S.build_stock_snapshot("600519", _fetchers=f6))
S.DIM_TIMEOUT = S.DIM_TIMEOUT_SAVED
check("T6a unlock 超时→failed", snap6["footer"]["dim_meta"]["unlock"]["status"] == "failed"
      and "超时" in snap6["footer"]["dim_meta"]["unlock"]["error"])
check("T6b 其余维不受影响", snap6["footer"]["dim_meta"]["kline"]["status"] == "ok")

print("== T7 路由层(TestClient,补丁注入)==")
from fastapi.testclient import TestClient
import src.web_app as W

client = TestClient(W.app)

_real = S.build_stock_snapshot_sync
S.build_stock_snapshot_sync = lambda t, **kw: {"ticker": t, "patched": True, "kw": kw,
                                               "dimensions": {}, "footer": {"data_gaps": []}}
r = client.get("/stock/600519.SH/snapshot?agents=1&llm=1")
check("T7a snapshot 200 + flag 透传", r.status_code == 200
      and r.json()["kw"] == {"include_agents": True, "with_llm": True})


def _raise(t, **kw):
    raise S.MajorPageDataFailure(["a", "b", "c", "d", "e"], 9, {"a": "x"})


S.build_stock_snapshot_sync = _raise
r = client.get("/stock/600519.SH/snapshot")
check("T7b ≥50% → 503 + failed_dims", r.status_code == 503
      and r.json()["failed_dims"] == ["a", "b", "c", "d", "e"])
S.build_stock_snapshot_sync = _real

print("== T8 DCF 重算路由(纯计算,patch dcf.compute)==")
from src.analysis import dcf as D
_real_compute = D.compute
D.compute = lambda norm, assumptions=None, asof=None: type(
    "R", (), {"model_dump": lambda self, mode=None: {
        "norm": norm, "wacc": assumptions.wacc,
        "fcf_base": assumptions.fcf_base}})()
r = client.post("/stock/600519/dcf", json={
    "perpetual_growth_rate": 0.03, "wacc": 0.073,
    "five_year_growth_rate": 0.12, "fcf_base": 5.5e10})
D.compute = _real_compute
check("T8a 200 + 归一 + 假设透传", r.status_code == 200
      and r.json() == {"norm": "600519.SH", "wacc": 0.073, "fcf_base": 5.5e10})
r = client.post("/stock/600519/dcf", json={"wacc": 0.073})
check("T8b 缺假设字段 → 422(I1.4 假设必显式)", r.status_code == 422)

print("== T9 HTML 壳 ==")
r = client.get("/stock/600519")
check("T9a 200 HTML + ticker 注入", r.status_code == 200
      and "600519.SH" in r.text and "/snapshot" in r.text and "I6.2" in r.text)

print("== T10 healthz 注册 snapshot ==")
r = client.get("/healthz")
check("T10a snapshot 模块可见", "src.analysis.snapshot" in r.json()["modules"]
      and r.json()["modules"]["src.analysis.snapshot"].get("version") == "1.0.0")

print(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("SMOKE STEP16: ALL GREEN")
