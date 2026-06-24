#!/usr/bin/env python3
"""
smoke_test_phase1_v2.py — Phase 1 能力演示 + 数据源探测

变化（vs v1）：
  - Demo 1 从 AKShare 单源 改成 4 个真实数据源直接探测，定位你环境下到底
    哪个数据源能通（而不是依赖 AKShare 抽象掩盖问题）
  - Demo 3 改用真实备用源做 fallback chain，而不是模拟
  - Demo 4 跑 DeepSeek V4-flash 默认模型（不再用 deepseek-chat 旧名）

用法：
    cd E:\\AI-tool\\Stock\\ai-hedge-fund
    python smoke_test_phase1_v2.py
"""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# === 必须最先注入 NO_PROXY ===
from src.markets.proxy import inject_no_proxy
n_hosts, merged = inject_no_proxy()

# === 启动横幅 ===
from src.boot import boot_print_versions
boot_print_versions()

print("\n" + "=" * 70)
print("Phase 1 Smoke Test v2 — 数据源探测 + 能力演示")
print("=" * 70)


# =================================================================
# Demo 1: 直接探测 4 个真实数据源（不经 AKShare）
# =================================================================
print("\n[Demo 1] 真实数据源探测（拉茅台 600519）")
print("-" * 70)

import requests

# 浏览器 UA — 大部分行情接口要求像浏览器
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

probes = [
    {
        "name": "腾讯财经 (qt.gtimg.cn)",
        "url":  "http://qt.gtimg.cn/q=sh600519",
        "expect_in_text": "贵州茅台",
        "headers": {"User-Agent": BROWSER_UA, "Referer": "http://gu.qq.com/"},
    },
    {
        "name": "新浪财经 (hq.sinajs.cn)",
        "url":  "http://hq.sinajs.cn/list=sh600519",
        "expect_in_text": "贵州茅台",
        "headers": {"User-Agent": BROWSER_UA, "Referer": "http://finance.sina.com.cn/"},
    },
    {
        "name": "东方财富 quote (push2.eastmoney.com)",
        "url":  "http://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=f43,f44,f57,f58&_=1",
        "expect_in_text": '"f43"',
        "headers": {"User-Agent": BROWSER_UA, "Referer": "http://quote.eastmoney.com/"},
    },
    {
        "name": "东方财富 spot (datacenter)",
        "url":  "http://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fields=f12,f14,f2&fs=m:1+t:2",
        "expect_in_text": '"f12"',
        "headers": {"User-Agent": BROWSER_UA, "Referer": "http://quote.eastmoney.com/"},
    },
]

results_d1 = []
for p in probes:
    name = p["name"]
    try:
        r = requests.get(p["url"], headers=p["headers"], timeout=8)
        if r.status_code != 200:
            results_d1.append((name, False, f"HTTP {r.status_code}"))
            print(f"  ✗ {name:<42s} HTTP {r.status_code}")
            continue
        # 部分接口返回 GBK 编码，主动 decode 一下
        try:
            text = r.content.decode("gbk", errors="replace")
        except Exception:
            text = r.text
        if p["expect_in_text"] in text or p["expect_in_text"] in r.text:
            preview = text[:120].replace("\n", " ").replace("\r", "")
            results_d1.append((name, True, preview))
            print(f"  ✓ {name:<42s} OK ({len(r.content)} bytes)")
            print(f"        预览: {preview[:100]}")
        else:
            results_d1.append((name, False, f"got 200 but expected token absent: {text[:80]}"))
            print(f"  ⚠ {name:<42s} HTTP 200 但内容异常: {text[:80]}")
    except requests.exceptions.ProxyError as e:
        results_d1.append((name, False, f"ProxyError: {e}"))
        print(f"  ✗ {name:<42s} ProxyError → NO_PROXY 未覆盖此 host")
    except Exception as e:
        results_d1.append((name, False, f"{type(e).__name__}: {str(e)[:120]}"))
        print(f"  ✗ {name:<42s} {type(e).__name__}: {str(e)[:80]}")

n_ok = sum(1 for _, ok, _ in results_d1 if ok)
print(f"\n  汇总：{n_ok}/{len(probes)} 数据源可达")
if n_ok == 0:
    print(f"  → 所有数据源都失败：检查 Clash/v2rayN 的 TUN 模式或防火墙规则")
elif n_ok < len(probes):
    print(f"  → 部分通：Phase 2 的 fallback chain 把可达源放前面就行")
else:
    print(f"  → 全通：网络环境完美")


# =================================================================
# Demo 2: 三分法池真实操作
# =================================================================
print("\n[Demo 2] 三分法池初始化 + 配对迁移")
print("-" * 70)

from src.strategy.three_categories import (
    PoolState, PoolEntry, MigrationLeg,
    add_initial_entry, save_pool_state, load_pool_state,
    execute_migration_pair, get_monthly_migration_count,
)

with tempfile.TemporaryDirectory() as tmp:
    tmp_dir = Path(tmp)

    seeds = {
        "V": [("600519.SH", "贵州茅台"), ("000858.SZ", "五粮液"),
              ("601318.SH", "中国平安"), ("600036.SH", "招商银行"),
              ("600900.SH", "长江电力")],
        "T": [("300750.SZ", "宁德时代"), ("002594.SZ", "比亚迪"),
              ("002460.SZ", "赣锋锂业"), ("300059.SZ", "东方财富"),
              ("300760.SZ", "迈瑞医疗")],
        "N": [("00700.HK", "腾讯控股"), ("09988.HK", "阿里巴巴"),
              ("03690.HK", "美团"), ("09618.HK", "京东集团"),
              ("01024.HK", "快手")],
    }
    state = PoolState()
    now = datetime.now().isoformat()
    for cat, items in seeds.items():
        for ticker, name in items:
            state = add_initial_entry(state, PoolEntry(
                ticker=ticker, name=name, category=cat, sub_id="(auto)",
                rationale=f"{cat} 池演示", added_at=now,
            ), persist=False)
    save_pool_state(state, dir_override=tmp_dir)
    print(f"  ✓ 池初始化  V={len(state.v_pool)}  T={len(state.t_pool)}  N={len(state.n_pool)}")

    after = execute_migration_pair(
        state,
        exit_leg=MigrationLeg("600519.SH", "V", "T",
            signal="V2T_OVERBOUGHT",
            evidence=[{"key": "cum_return_5d", "value": 0.18}]),
        enter_leg=MigrationLeg("300750.SZ", "T", "V",
            signal="T2V_PROSPERITY_WEAKEN_3D",
            evidence=[{"key": "capital_flow_3d", "value": [-1.2, -0.8, -1.5]}]),
        user_rationale="演示场景：茅台涨幅过高转 T；宁德景气走弱转 V",
        dir_override=tmp_dir,
    )
    print(f"  ✓ 配对迁移完成，月度配额 {get_monthly_migration_count(after)}/2")


# =================================================================
# Demo 3: fallback chain 实战（用真实备用源）
# =================================================================
print("\n[Demo 3] fallback chain 实战（用 Demo 1 的真实源）")
print("-" * 70)

from src.tools.data_fallback import call_with_fallback, DataSourceExhaustedError

def _make_real_source(probe_idx):
    """把 Demo 1 的探测包装成 fallback source。"""
    p = probes[probe_idx]
    def fn(ticker):
        r = requests.get(p["url"], headers=p["headers"], timeout=8)
        r.raise_for_status()
        text = r.content.decode("gbk", errors="replace") if "gtimg" in p["url"] or "sinajs" in p["url"] else r.text
        if p["expect_in_text"] not in text and p["expect_in_text"] not in r.text:
            raise ValueError(f"expected token absent in {p['name']}")
        return {"source": p["name"], "preview": text[:80], "bytes": len(r.content)}
    return fn

# 故意先放一个一定失败的，然后依次用真实源
def src_fake_first(ticker):
    raise ConnectionError("eastmoney 502 (simulated)")

chain = [("eastmoney_simulated_502", src_fake_first)]
for i, p in enumerate(probes):
    chain.append((p["name"].split(" ")[0], _make_real_source(i)))

try:
    result = call_with_fallback(ticker="600519.SH", chain=chain)
    print(f"  ✓ 第一个通的 source: {result.source_used}")
    print(f"    数据: {result.data}")
    print(f"    尝试过的 source: {result.attempts}")
    print(f"    失败记录:")
    for err in result.errors:
        print(f"      - {err}")
except DataSourceExhaustedError as e:
    print(f"  ✗ 全部失败: {e}")
    print(f"    → 这意味着 Demo 1 全部失败，跟 fallback 机制本身无关")


# =================================================================
# Demo 4: DeepSeek V4-flash 真实调用
# =================================================================
print("\n[Demo 4] DeepSeek V4-flash 真实调用（v1.0.1 默认模型）")
print("-" * 70)

env_path = Path(".env")
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

if not os.environ.get("DEEPSEEK_API_KEY"):
    print(f"  ⊘ SKIP: 没找到 DEEPSEEK_API_KEY")
else:
    try:
        from src.llm_text import llm_text, LLMCallType
        text, log = llm_text(
            call_type=LLMCallType.OTHER,
            provider="deepseek",     # 默认走 deepseek-v4-flash
            system_prompt="你是简洁的中文金融助手。",
            user_prompt="用一句话回答：当前 A 股市场风格偏向价值还是成长？",
            max_tokens=200,
        )
        print(f"  ✓ PASS")
        print(f"    回复:   {text.strip()[:200]}")
        print(f"    模型:   {log.model}")
        print(f"    tokens: prompt={log.prompt_tokens} completion={log.completion_tokens}")
        print(f"    成本:   ${log.estimated_cost_usd:.6f}")
        print(f"    耗时:   {log.duration_seconds:.2f}s")
    except Exception as e:
        print(f"  ✗ FAIL: {type(e).__name__}: {e}")


print("\n" + "=" * 70)
print("Phase 1 Smoke Test v2 结束")
print("=" * 70)

# 总结表
print(f"\n数据源探测结果：")
for name, ok, info in results_d1:
    mark = "✓ 通" if ok else "✗ 断"
    print(f"  {mark}  {name}")
print(f"\nNO_PROXY hosts 已注入: {n_hosts}")
print(f"\nDemo 1 给出的是 Phase 2 fallback chain 的事实依据：把通的源放前面。")
