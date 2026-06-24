#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.1 补丁应用器 — 对 src/strategy/briefing_generator.py 打 8 处补丁。

用法（在项目根目录）:
    poetry run python apply_briefing_patch.py
    poetry run python apply_briefing_patch.py --path src/strategy/briefing_generator.py

行为:
  - 修改前自动备份为 briefing_generator.py.bak
  - 每处补丁精确匹配原文，找不到或匹配多处则中止并报告（不会改坏文件）
  - 幂等：已应用的补丁自动跳过，可重复运行

补丁内容:
  P1  引入 proxy_guard（NO_PROXY注入）+ 腾讯行情兜底
  P2  重写 _collect_market_snapshot: 返回(文本,健康度)，逐条记录异常，
      东财失败自动回退腾讯实时行情；新增 _snapshot_gate fail-fast 门槛
  P3  早报: 覆盖率<60%时中止生成，输出诊断（不写历史日志）
  P4  晚报: 同上
  P5  晚报prompt第五节: 仅在有早报且数据完整时才允许打分
  P6  晚报prompt结尾: 基准期/数据缺口禁止打分（修复"100分A级假锚点"）
  P7  晚报system prompt: 增加第⑥条评分护栏
  P8  模型ID更新: deepseek-chat→deepseek-v4-flash(旧ID 2026-07-24弃用),
      claude-sonnet-4-20250514→claude-sonnet-4-6, gpt-4.1→gpt-5.2
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# ════════════════════════════════════════════════════════════
# P2 的替换文本（新版快照采集 + fail-fast 门槛）
# ════════════════════════════════════════════════════════════

NEW_SNAPSHOT_FUNC = '''SNAPSHOT_MIN_COVERAGE = 0.6  # 行情覆盖率低于此阈值时拒绝生成报告


def _collect_market_snapshot(pool: ThreeCategoryPool, end_date: str) -> tuple[str, dict]:
    """
    采集观察池15只标的的真实收盘数据 + 北向资金 + 板块排名。

    v3.1 变更：
    - 返回 (快照文本, 健康度dict)，调用方据此 fail-fast
    - AKShare(东财) 失败的标的自动回退腾讯实时行情 qt.gtimg.cn
    - 每个失败条目记录异常原文，不再静默吞掉
    """
    health = {
        "quotes_ok": 0, "quotes_total": 0, "quotes_fallback": 0,
        "northbound": False, "sectors": False, "errors": [],
    }
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start = (end_dt - timedelta(days=12)).strftime("%Y-%m-%d")

    # ── 第一轮：AKShare（东财日线，含5日累计/量比）──
    results: dict[str, str | None] = {}
    failed: list = []
    for e in pool.state.entries:
        health["quotes_total"] += 1
        try:
            prices = get_prices(e.ticker, start, end_date)
            if len(prices) >= 2:
                p0, p1 = prices[-2], prices[-1]
                chg = (p1.close - p0.close) / p0.close * 100
                chg5 = ((p1.close - prices[max(0, len(prices)-6)].close)
                        / prices[max(0, len(prices)-6)].close * 100) if len(prices) >= 6 else None
                vol_ratio = (p1.volume / p0.volume) if p0.volume else None
                extra = []
                if chg5 is not None:
                    extra.append(f"5日{chg5:+.1f}%")
                if vol_ratio is not None:
                    extra.append(f"量比{vol_ratio:.2f}")
                results[e.ticker] = (
                    f"{e.slot} {e.name}({e.ticker}): 收{p1.close:.2f} "
                    f"当日{chg:+.2f}% " + " ".join(extra)
                )
                health["quotes_ok"] += 1
            else:
                results[e.ticker] = None
                failed.append((e, "返回数据不足2条"))
        except Exception as ex:
            results[e.ticker] = None
            failed.append((e, f"{type(ex).__name__}: {str(ex)[:90]}"))

    # ── 第二轮：腾讯实时行情兜底（仅当日涨跌/量比，无5日历史）──
    if failed:
        tq = {}
        try:
            tq = fetch_tencent_quotes([e.ticker for e, _ in failed])
        except Exception as ex:
            health["errors"].append(f"腾讯备用源整体失败: {type(ex).__name__}: {str(ex)[:90]}")
        for e, err in failed:
            q = tq.get(e.ticker)
            if q is not None and q.price is not None and q.change_pct is not None:
                extra = f" 量比{q.volume_ratio:.2f}" if q.volume_ratio else ""
                results[e.ticker] = (
                    f"{e.slot} {e.name}({e.ticker}): 收{q.price:.2f} "
                    f"当日{q.change_pct:+.2f}%{extra} [腾讯实时·5日数据缺口]"
                )
                health["quotes_ok"] += 1
                health["quotes_fallback"] += 1
            else:
                results[e.ticker] = f"{e.slot} {e.name}({e.ticker}): 【数据缺口】"
                health["errors"].append(f"{e.ticker} {e.name}: 东财={err}; 腾讯=无返回")

    lines = ["━━━ 实时数据快照 ━━━", "\\n【观察池15只标的收盘数据】"]
    for e in pool.state.entries:
        lines.append(results.get(e.ticker) or f"{e.slot} {e.name}({e.ticker}): 【数据缺口】")

    lines.append("\\n【北向资金（近3日）】")
    try:
        nb = get_northbound_flow(limit=3)
        valid = [f"{x.date}: {x.total_net_buy:+.1f}亿" for x in nb if x.total_net_buy is not None]
        if valid:
            lines.append("; ".join(valid))
            health["northbound"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("北向资金: 接口返回但无有效数值")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"北向资金: {type(ex).__name__}: {str(ex)[:90]}")

    lines.append("\\n【行业板块当日排名】")
    try:
        sectors = get_sector_performance(limit=40)
        if sectors:
            ranked = sorted(sectors, key=lambda s: s.change_pct or 0, reverse=True)
            top = ", ".join(f"{s.sector_name}{s.change_pct:+.1f}%" for s in ranked[:5] if s.change_pct is not None)
            bot = ", ".join(f"{s.sector_name}{s.change_pct:+.1f}%" for s in ranked[-5:] if s.change_pct is not None)
            lines.append(f"领涨: {top}")
            lines.append(f"领跌: {bot}")
            health["sectors"] = True
        else:
            lines.append("【数据缺口】")
            health["errors"].append("板块排名: 接口返回空")
    except Exception as ex:
        lines.append("【数据缺口】")
        health["errors"].append(f"板块排名: {type(ex).__name__}: {str(ex)[:90]}")

    total = health["quotes_total"] or 1
    lines.append(
        f"\\n【快照健康度】行情 {health['quotes_ok']}/{total}"
        f"（含腾讯兜底{health['quotes_fallback']}） | "
        f"北向 {'✓' if health['northbound'] else '✗'} | "
        f"板块 {'✓' if health['sectors'] else '✗'}"
    )
    lines.append("━━━ 快照结束 ━━━")
    return "\\n".join(lines), health


def _snapshot_gate(health: dict, end_date: str, kind: str) -> str | None:
    """行情覆盖率低于阈值时返回诊断报告（中止生成、不写历史日志），否则返回 None。

    设计原则：空数据下生成的报告（尤其是带评分的晚报）会作为假锚点进入
    次日早报的校准回路，比不生成更有害。宁可中止，明确报错。
    """
    total = health.get("quotes_total") or 1
    coverage = health.get("quotes_ok", 0) / total
    if coverage >= SNAPSHOT_MIN_COVERAGE:
        return None
    lines = [
        f"━━━ {end_date} {kind}生成中止：数据采集失败 ━━━",
        "",
        f"行情覆盖率 {health.get('quotes_ok', 0)}/{total}（{coverage:.0%}），"
        f"低于阈值 {SNAPSHOT_MIN_COVERAGE:.0%}。",
        "为避免无数据支撑的报告污染反馈闭环，本次不调用 LLM、不写入历史日志。",
        "",
        "逐项错误：",
    ]
    errors = health.get("errors") or ["（无详细错误记录）"]
    lines += [f"  · {e}" for e in errors[:20]]
    lines += [
        "",
        "排查建议：",
        "  1. 运行 doctor_china.py 自检完整数据链路",
        "  2. 若错误为 ProxyError：系统代理劫持了东财子域。本版已注入 NO_PROXY，",
        "     但 TUN/全局模式下无效，需在代理客户端添加直连规则：",
        "     *.eastmoney.com / qt.gtimg.cn / *.sinajs.cn / *.sina.com.cn",
        "  3. 海外网络长期方案：采集端部署到国内 VPS 定时运行",
    ]
    return "\\n".join(lines)
'''

# ════════════════════════════════════════════════════════════
# 补丁定义
# ════════════════════════════════════════════════════════════

EXACT_PATCHES = [
    # (编号, 说明, 已应用标记, 原文, 新文)
    ("P1", "引入 proxy_guard + 腾讯兜底",
     "from src.tools import proxy_guard",
     "from src.utils.llm import call_llm",
     "from src.utils.llm import call_llm\n"
     "\n"
     "# ⭐ v3.1: 必须最先安装代理绕行（修复 Windows 系统代理劫持东财子域导致的 ProxyError）\n"
     "from src.tools import proxy_guard  # noqa: F401\n"
     "from src.tools.quotes_fallback import fetch_tencent_quotes"),

    ("P3", "早报 fail-fast 门槛",
     '_snapshot_gate(health, end_date, "早报")',
     '    snapshot = _collect_market_snapshot(pool, end_date)\n'
     '\n'
     '    # 读取最近一份晚报（校准回路的关键输入）',
     '    snapshot, health = _collect_market_snapshot(pool, end_date)\n'
     '\n'
     '    # ⭐ v3.1 fail-fast：行情覆盖率不足时拒绝生成，输出诊断（不写历史日志）\n'
     '    gate = _snapshot_gate(health, end_date, "早报")\n'
     '    if gate:\n'
     '        return gate\n'
     '\n'
     '    # 读取最近一份晚报（校准回路的关键输入）'),

    ("P4", "晚报 fail-fast 门槛",
     '_snapshot_gate(health, end_date, "晚报")',
     '    snapshot = _collect_market_snapshot(pool, end_date)\n'
     '\n'
     '    # 读取今天的早报（偏差评分的对照基准）',
     '    snapshot, health = _collect_market_snapshot(pool, end_date)\n'
     '\n'
     '    # ⭐ v3.1 fail-fast：行情覆盖率不足时拒绝生成，输出诊断（不写历史日志）\n'
     '    gate = _snapshot_gate(health, end_date, "晚报")\n'
     '    if gate:\n'
     '        return gate\n'
     '\n'
     '    # 读取今天的早报（偏差评分的对照基准）'),

    ("P5", "晚报第五节: 限定打分前提",
     "仅当今日早报存在",
     "⚖️ 五、预测偏差量化评分：6维表格（方向准确性/催化剂识别/信号权重校准/风险预警/"
     "量能适配性/三分法跟踪质量，各20分），每项附评分依据，综合得分标准化至100分+"
     "偏差等级(A≥85/B≥70/C≥55/D<55)",
     "⚖️ 五、预测偏差量化评分：6维表格（方向准确性/催化剂识别/信号权重校准/风险预警/"
     "量能适配性/三分法跟踪质量，各20分），每项附评分依据，综合得分标准化至100分+"
     "偏差等级(A≥85/B≥70/C≥55/D<55)。仅当今日早报存在且快照中观察池数据完整时才允许打分"),

    ("P6", "晚报结尾: 禁止假锚点评分",
     "无数据支撑的满分是假锚点",
     '若今日无早报，第三、五节输出"基准期，无对照基准"。总字数1200字以内。"""',
     '若今日无早报或快照存在【数据缺口】，第三、五节只输出"不适用（基准期/数据缺口）"，'
     '禁止给出任何分数、等级或"参考锚点"——无数据支撑的满分是假锚点，'
     '会污染次日早报的校准回路。总字数1200字以内。"""'),

    ("P7", "晚报 system prompt: 第⑥条评分护栏",
     "⑥ 无对照基准或数据缺失时",
     '⑤ 数据缺失处标注【数据缺口】，严禁编造具体数字"""',
     '⑤ 数据缺失处标注【数据缺口】，严禁编造具体数字\n'
     '⑥ 无对照基准或数据缺失时，相应评分节只写"不适用"，禁止打分'
     '——尤其禁止给出100分/A级之类的"锚点"分"""'),

    ("P8", "模型ID更新（deepseek-chat 2026-07-24弃用）",
     "deepseek-v4-flash",
     '    if os.getenv("DEEPSEEK_API_KEY"):\n'
     '        return "deepseek-chat", "DeepSeek"\n'
     '    if os.getenv("ANTHROPIC_API_KEY"):\n'
     '        return "claude-sonnet-4-20250514", "Anthropic"\n'
     '    if os.getenv("OPENAI_API_KEY"):\n'
     '        return "gpt-4.1", "OpenAI"',
     '    if os.getenv("DEEPSEEK_API_KEY"):\n'
     '        return "deepseek-v4-flash", "DeepSeek"  # 旧ID deepseek-chat 于 2026-07-24 弃用\n'
     '    if os.getenv("ANTHROPIC_API_KEY"):\n'
     '        return "claude-sonnet-4-6", "Anthropic"\n'
     '    if os.getenv("OPENAI_API_KEY"):\n'
     '        return "gpt-5.2", "OpenAI"'),
]

# P2: 整函数替换（regex 锚定，避免长文本逐字匹配的脆弱性）
P2_PATTERN = re.compile(
    r'def _collect_market_snapshot\(pool: ThreeCategoryPool, end_date: str\) -> str:'
    r'.*?\n    return "\\n"\.join\(lines\)\n',
    re.S,
)
P2_MARKER = "SNAPSHOT_MIN_COVERAGE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="src/strategy/briefing_generator.py")
    args = ap.parse_args()
    path = Path(args.path)
    if not path.exists():
        sys.exit(f"[中止] 找不到 {path}，请在项目根目录运行，或用 --path 指定")

    text = path.read_text(encoding="utf-8")
    original = text
    applied, skipped, failed = [], [], []

    # ── P2（先做整函数替换，避免与P3/P4的上下文互相干扰）──
    if P2_MARKER in text:
        skipped.append("P2")
    else:
        m = P2_PATTERN.search(text)
        if m:
            text = text[:m.start()] + NEW_SNAPSHOT_FUNC + text[m.end():]
            applied.append("P2")
        else:
            failed.append(("P2", "未找到 _collect_market_snapshot 函数体（可能已被改动）"))

    # ── 其余精确匹配补丁 ──
    for pid, desc, marker, old, new in EXACT_PATCHES:
        if marker in text:
            skipped.append(pid)
            continue
        n = text.count(old)
        if n == 1:
            text = text.replace(old, new)
            applied.append(pid)
        else:
            failed.append((pid, f"原文匹配到 {n} 处（期望1处）: {desc}"))

    if failed:
        print("[中止] 以下补丁无法应用，文件未做任何修改：")
        for pid, why in failed:
            print(f"  ✗ {pid}: {why}")
        print("\n请把 briefing_generator.py 发回会话，针对性重出补丁。")
        sys.exit(1)

    if text != original:
        bak = path.with_suffix(".py.bak")
        shutil.copy2(path, bak)
        path.write_text(text, encoding="utf-8")
        print(f"[备份] {bak}")

    print(f"[完成] 已应用: {', '.join(applied) or '无'} | 已存在跳过: {', '.join(skipped) or '无'}")
    print("验证: poetry run python -c \"from src.strategy import briefing_generator; print('import OK')\"")


if __name__ == "__main__":
    main()
