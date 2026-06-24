#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.2 补丁应用器 — 在 v3.1 基础上做信息增强（对齐 openclaw 信源能力）。

前置条件: 已应用 v3.1 补丁（apply_briefing_patch.py），否则本脚本会因
找不到锚点而中止（不会改坏文件）。

用法（项目根目录）:
    poetry run python apply_briefing_patch_v32.py

补丁内容:
  Q1  引入 fetch_tencent_indices + news_collector
  Q2  快照注入【全球指数温度计】: A股核心指数+恒指/恒生科技+隔夜美股三大
      （腾讯源，海外IP友好）—— 对齐 openclaw 的"隔夜美股映射"
  Q3  快照注入【全球财经电报】+【观察池个股新闻】（财联社/东财/新浪逐级回退）
      —— 对齐 openclaw 的事件级催化剂归因
  Q4  早报prompt: 宏观定调必须基于指数温度计给出隔夜映射结论
  Q5  早报prompt: 催化剂必须引用电报/个股新闻原文要点
  Q6  晚报prompt: 红黑榜驱动逻辑优先引用个股新闻
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

INDICES_BLOCK = (
    '    # ── v3.2 全球指数温度计（腾讯源，海外友好；美股为隔夜收盘）──\n'
    '    lines.append("\\n【全球指数温度计】")\n'
    '    try:\n'
    '        idx = fetch_tencent_indices()\n'
    '        if idx:\n'
    '            lines.append(" | ".join(\n'
    '                f"{q.name} {q.price:,.0f}({q.change_pct:+.2f}%)" for q in idx.values()\n'
    '            ))\n'
    '            health["indices"] = True\n'
    '        else:\n'
    '            lines.append("【数据缺口】")\n'
    '            health["errors"].append("指数温度计: 腾讯接口无返回")\n'
    '    except Exception as ex:\n'
    '        lines.append("【数据缺口】")\n'
    '        health["errors"].append(f"指数温度计: {type(ex).__name__}: {str(ex)[:90]}")\n'
    '\n'
    '    lines.append("\\n【北向资金（近3日）】")'
)

NEWS_BLOCK = (
    '    # ── v3.2 新闻注入（财联社电报 + 观察池个股新闻，多级回退）──\n'
    '    lines.append("\\n【全球财经电报（最新）】")\n'
    '    try:\n'
    '        tg = get_global_telegraph(limit=12)\n'
    '        if tg:\n'
    '            lines += [f"· {t}" for t in tg]\n'
    '            health["news"] = True\n'
    '        else:\n'
    '            lines.append("【数据缺口】")\n'
    '            health["errors"].append("电报快讯: 全部信源无返回")\n'
    '    except Exception as ex:\n'
    '        lines.append("【数据缺口】")\n'
    '        health["errors"].append(f"电报快讯: {type(ex).__name__}: {str(ex)[:90]}")\n'
    '\n'
    '    lines.append("\\n【观察池个股新闻（A股，近2条/只）】")\n'
    '    try:\n'
    '        nw = get_pool_news([e.ticker for e in pool.state.entries], per_ticker=2)\n'
    '        if nw:\n'
    '            lines += [f"· {t}" for t in nw]\n'
    '        else:\n'
    '            lines.append("【数据缺口】")\n'
    '    except Exception as ex:\n'
    '        lines.append("【数据缺口】")\n'
    '        health["errors"].append(f"个股新闻: {type(ex).__name__}: {str(ex)[:90]}")\n'
    '\n'
    '    total = health["quotes_total"] or 1'
)

PATCHES = [
    # (编号, 说明, 已应用标记, 原文, 新文)
    ("Q1", "引入指数温度计 + 新闻采集器",
     "news_collector",
     "from src.tools.quotes_fallback import fetch_tencent_quotes",
     "from src.tools.quotes_fallback import fetch_tencent_quotes, fetch_tencent_indices\n"
     "from src.tools.news_collector import get_global_telegraph, get_pool_news"),

    ("Q2", "快照注入全球指数温度计",
     "全球指数温度计】",
     '    lines.append("\\n【北向资金（近3日）】")',
     INDICES_BLOCK),

    ("Q3", "快照注入电报 + 个股新闻",
     "全球财经电报（最新）",
     '    total = health["quotes_total"] or 1',
     NEWS_BLOCK),

    ("Q4", "早报prompt: 宏观定调基于指数温度计",
     "开盘底色",
     "🌍 一、宏观情绪定调：基于数据快照判断市场温度",
     "🌍 一、宏观情绪定调：基于快照中的全球指数温度计（隔夜美股/恒指→A股映射）"
     "与电报快讯，给出一句话开盘底色结论"),

    ("Q5", "早报prompt: 催化剂必须引用新闻",
     "必须引用快照中的电报",
     "🎯 二、盘前核心催化剂：利多/利空（按信号权重排序，标注影响类别）",
     "🎯 二、盘前核心催化剂：利多/利空（按信号权重排序，标注影响类别）。"
     "必须引用快照中的电报与个股新闻原文要点；无新闻数据时标注【数据缺口】"),

    ("Q6", "晚报prompt: 红黑榜引用个股新闻",
     "优先引用快照中的个股新闻",
     "🔍 二、自选股异动红黑榜：涨跌超±3%的观察池标的，逐只标注 → 驱动逻辑 → "
     "早报是否命中(✅命中/❌遗漏/⚠️方向相反) → 分类处置建议",
     "🔍 二、自选股异动红黑榜：涨跌超±3%的观察池标的，逐只标注 → 驱动逻辑"
     "（优先引用快照中的个股新闻与电报条目）→ 早报是否命中"
     "(✅命中/❌遗漏/⚠️方向相反) → 分类处置建议"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="src/strategy/briefing_generator.py")
    args = ap.parse_args()
    path = Path(args.path)
    if not path.exists():
        sys.exit(f"[中止] 找不到 {path}，请在项目根目录运行，或用 --path 指定")

    text = path.read_text(encoding="utf-8")
    if "SNAPSHOT_MIN_COVERAGE" not in text:
        sys.exit("[中止] 该文件尚未应用 v3.1 补丁，请先运行 apply_briefing_patch.py")

    original = text
    applied, skipped, failed = [], [], []

    for pid, desc, marker, old, new in PATCHES:
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
        sys.exit(1)

    if text != original:
        bak = path.with_suffix(".py.bak2")
        shutil.copy2(path, bak)
        path.write_text(text, encoding="utf-8")
        print(f"[备份] {bak}")

    print(f"[完成] 已应用: {', '.join(applied) or '无'} | 已存在跳过: {', '.join(skipped) or '无'}")
    print("验证: poetry run python src/tools/quotes_fallback.py && "
          "poetry run python src/tools/news_collector.py")


if __name__ == "__main__":
    main()
