#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v3.3 补丁应用器 — 修复 6/12 错误日志暴露的问题 + 接入 openclaw 信源桥。

用法（项目根目录）:
    poetry run python apply_v33_patch.py

涉及文件（每个文件原子化：该文件所有补丁全部命中才写入，否则跳过并报告）:
  src/tools/api_china.py
    A1  主力资金流: 港股提前优雅跳过（修 KeyError 'hk' 噪音）
    A2  融资融券: stock_margin_account_info 优先 + 逐级回退
        （修 stock_margin_sse 空返回时的 Length mismatch）
    A3  板块行情: 3次重试+退避（缓解海外直连东财 RemoteDisconnected）
  src/agents/china_public_opinion.py
    B1/B2  SYSTEM_PROMPT 和 user prompt 硬化 JSON schema
        （修 deepseek-v4-flash 返回自创字段导致 Pydantic 校验失败→静默中性）
  src/agents/china_policy.py
    C1/C2  同上，PolicyImpact schema 硬化
  src/strategy/briefing_generator.py（要求已应用 v3.1+v3.2）
    D1/D2  快照注入【外部信源简报】（openclaw 文件桥，见 openclaw_task.md）

每个文件修改前备份 .bak3；精确匹配否则不动该文件；幂等可重复运行。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

# ════════════ schema 硬化文本 ════════════

OPINION_SCHEMA = '''输出要求（强制，违反即解析失败）：
只输出一个 JSON 对象。禁止 Markdown 代码块、禁止任何解释文字、禁止自创字段名
（sentiment / risks / summary 等一律不接受）。必须包含且仅包含以下字段：
{
  "signal": "bullish 或 bearish 或 neutral",
  "confidence": 0到100的整数,
  "market_temperature": "extreme_fear/fear/neutral/greed/extreme_greed 之一",
  "key_narratives": ["驱动情绪的核心叙事，最多3条"],
  "black_swan": {
    "detected": true或false,
    "severity": "none/low/medium/high/critical 之一",
    "description": "事件简述，无则空字符串",
    "affected_sectors": ["受影响板块"],
    "recommended_caution": "建议的风险调整，无则空字符串"
  },
  "reasoning": "简明分析依据"
}"""'''

POLICY_SCHEMA = '''输出要求（强制，违反即解析失败）：
只输出一个 JSON 对象。禁止 Markdown 代码块、禁止任何解释文字、禁止自创或省略字段。
必须包含且仅包含以下字段：
{
  "has_relevant_policy": true或false,
  "policy_type": "monetary/fiscal/regulatory/industry/trade/geopolitical/none 之一",
  "impact_direction": "positive/negative/neutral 之一",
  "impact_duration": "short_term/medium_term/long_term 之一",
  "impact_magnitude": "minor/moderate/major/transformative 之一",
  "affected_sectors": ["受影响板块"],
  "signal": "bullish/bearish/neutral 之一",
  "confidence": 0到100的整数,
  "reasoning": "简明分析依据"
}"""'''

MARGIN_NEW = '''    try:
        if ticker:
            info = parse_ticker(ticker)
            df = ak.stock_margin_detail_sse(symbol=info.code)
        else:
            # v3.3: stock_margin_sse 空返回时在 akshare 内部抛 Length mismatch；
            # doctor 实测 stock_margin_account_info 可通，优先使用并逐级回退
            df = None
            for fn_name, kw in (
                ("stock_margin_account_info", {}),
                ("stock_margin_sse", {"start_date": "20240101"}),
            ):
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                try:
                    df = fn(**kw)
                    if df is not None and not df.empty:
                        break
                except Exception as ex:
                    logger.debug("margin source %s failed: %s", fn_name, ex)
                    df = None
    except Exception as e:
        logger.warning("Failed to fetch margin trading data: %s", e)
        return []'''

SECTOR_NEW = '''    # v3.3: 海外直连东财不稳（RemoteDisconnected），3次重试+退避
    import time as _time
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_board_industry_name_em()
            break
        except Exception as e:
            if attempt == 2:
                logger.warning("Failed to fetch sector performance: %s", e)
                return []
            logger.debug("sector fetch retry %d: %s", attempt + 1, e)
            _time.sleep(2 * (attempt + 1))'''

EXT_BRIEF_BLOCK = (
    '    # ── v3.3 外部信源简报（openclaw 跨平台搜索，文件桥）──\n'
    '    ext = read_external_brief()\n'
    '    if ext:\n'
    '        lines.append("\\n【外部信源简报（openclaw）】")\n'
    '        lines.append(ext)\n'
    '        health["external_brief"] = True\n'
    '\n'
    '    lines.append("\\n【全球财经电报（最新）】")'
)

# ════════════ 补丁表 ════════════

FILE_PATCHES = {
    "src/tools/api_china.py": [
        ("A1", "主力资金流: 港股优雅跳过", "主力资金流不覆盖港股",
         'def get_main_capital_flow(\n'
         '    ticker: str,\n'
         '    limit: int = 30,\n'
         ') -> list[MainCapitalFlow]:\n'
         '    """Fetch 主力资金流向 (main capital flow) for a specific stock."""\n'
         '    ak = _ensure_akshare()\n'
         '    info = parse_ticker(ticker)',
         'def get_main_capital_flow(\n'
         '    ticker: str,\n'
         '    limit: int = 30,\n'
         ') -> list[MainCapitalFlow]:\n'
         '    """Fetch 主力资金流向 (main capital flow) for a specific stock."""\n'
         '    ak = _ensure_akshare()\n'
         '    info = parse_ticker(ticker)\n'
         '\n'
         '    # v3.3: 东财主力资金接口仅覆盖 sh/sz，港股直接跳过（已知信源边界）\n'
         '    if info.market.is_hk:\n'
         '        logger.debug("主力资金流不覆盖港股 (%s)，跳过", ticker)\n'
         '        return []'),

        ("A2", "融资融券: 多源回退", "stock_margin_account_info",
         '    try:\n'
         '        if ticker:\n'
         '            info = parse_ticker(ticker)\n'
         '            df = ak.stock_margin_detail_sse(symbol=info.code)\n'
         '        else:\n'
         '            df = ak.stock_margin_sse(start_date="20240101")\n'
         '    except Exception as e:\n'
         '        logger.warning("Failed to fetch margin trading data: %s", e)\n'
         '        return []',
         MARGIN_NEW),

        ("A3", "板块行情: 重试+退避", "for attempt in range(3)",
         '    try:\n'
         '        df = ak.stock_board_industry_name_em()\n'
         '    except Exception as e:\n'
         '        logger.warning("Failed to fetch sector performance: %s", e)\n'
         '        return []',
         SECTOR_NEW),
    ],

    "src/agents/china_public_opinion.py": [
        ("B1", "SYSTEM_PROMPT 硬化 schema", "输出要求（强制",
         '请用JSON格式返回分析结果。"""',
         OPINION_SCHEMA),
        ("B2", "user prompt 强约束", "严格按 system",
         '            + "\\n\\n请返回JSON格式的分析结果。"',
         '            + "\\n\\n请严格按 system 中定义的 JSON schema 输出单个 JSON 对象，'
         '所有字段必填，禁止自创字段。"'),
    ],

    "src/agents/china_policy.py": [
        ("C1", "SYSTEM_PROMPT 硬化 schema", "输出要求（强制",
         '请用JSON格式返回分析结果。"""',
         POLICY_SCHEMA),
        ("C2", "user prompt 强约束", "严格按 system",
         '            + "\\n\\n请返回JSON格式的政策影响分析。"',
         '            + "\\n\\n请严格按 system 中定义的 JSON schema 输出单个 JSON 对象，'
         '所有字段必填，禁止自创字段。"'),
    ],

    "src/strategy/briefing_generator.py": [
        ("D1", "引入外部简报桥", "external_brief",
         "from src.tools.news_collector import get_global_telegraph, get_pool_news",
         "from src.tools.news_collector import get_global_telegraph, get_pool_news\n"
         "from src.tools.external_brief import read_external_brief"),
        ("D2", "快照注入外部简报", "外部信源简报（openclaw）",
         '    lines.append("\\n【全球财经电报（最新）】")',
         EXT_BRIEF_BLOCK),
    ],
}


def apply_file(path: Path, patches) -> bool:
    if not path.exists():
        print(f"[跳过] {path} 不存在")
        return False
    text = path.read_text(encoding="utf-8")

    if path.name == "briefing_generator.py" and "全球财经电报" not in text:
        print(f"[跳过] {path} 尚未应用 v3.2 补丁，请先运行 apply_briefing_patch_v32.py")
        return False

    original = text
    applied, skipped, failed = [], [], []
    for pid, desc, marker, old, new in patches:
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
        print(f"[中止] {path} 以下补丁无法应用，该文件未修改：")
        for pid, why in failed:
            print(f"  ✗ {pid}: {why}")
        return False

    if text != original:
        bak = path.with_suffix(".py.bak3")
        shutil.copy2(path, bak)
        path.write_text(text, encoding="utf-8")
        print(f"[备份] {bak}")
    print(f"[完成] {path.name}: 已应用 {', '.join(applied) or '无'}"
          f" | 跳过 {', '.join(skipped) or '无'}")
    return True


def main():
    ok = True
    for rel, patches in FILE_PATCHES.items():
        ok = apply_file(Path(rel), patches) and ok
    if not ok:
        print("\n部分文件未能应用，请把对应文件发回会话核对。")
        sys.exit(1)
    print("\n全部完成。验证:")
    print('  poetry run python -c "import src.strategy.briefing_generator as bg; '
          "print(bg.__file__); print('v3.1+' if hasattr(bg, '_snapshot_gate') "
          "else '!! 旧版本')\"")


if __name__ == "__main__":
    main()
