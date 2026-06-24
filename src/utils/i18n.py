"""
utils/i18n.py — 中文本地化层（重建 v3.5 丢失的 i18n）

职责：
  1. agent 内部名 → 中文显示名（解决"nassim taleb / warren buffett"裸英文）
  2. signal → 中文（bullish→看多 等）
  3. reasoning 文本里常见英文术语 → 中文（缓解中英混杂；freeform LLM 文本
     无法 100% 翻译，已知局限，但固定短语全覆盖）

设计原则：纯字符串映射，无外部依赖，可全量单测。
"""
from __future__ import annotations

import re

__version__ = "v1.0.0"

# ── agent 内部名 → 中文 ───────────────────────────────────────
AGENT_NAME_ZH = {
    "china_public_opinion": "舆情+黑天鹅",
    "china_policy": "政策解读",
    "china_capital_flow": "资金流向",
    "china_sector_rotation": "板块轮动",
    "technical_analyst": "技术分析",
    "technicals": "技术分析",
    "fundamentals_analyst": "基本面",
    "fundamentals": "基本面",
    "valuation_analyst": "估值分析",
    "valuation": "估值分析",
    "sentiment_analyst": "情绪分析",
    "sentiment": "情绪分析",
    "warren_buffett": "巴菲特视角",
    "charlie_munger": "芒格视角",
    "ben_graham": "格雷厄姆视角",
    "peter_lynch": "彼得·林奇视角",
    "phil_fisher": "费雪视角",
    "cathie_wood": "木头姐视角",
    "michael_burry": "伯里视角",
    "bill_ackman": "阿克曼视角",
    "stanley_druckenmiller": "德鲁肯米勒视角",
    "aswath_damodaran": "达摩达兰估值",
    "rakesh_jhunjhunwala": "Jhunjhunwala视角",
    "mohnish_pabrai": "帕伯莱视角",
    "nassim_taleb": "塔勒布尾部风险",
    "growth": "成长分析",
    "news_sentiment": "新闻情绪",
    "risk_management": "风险管理",
    "portfolio_management": "组合管理",
    "portfolio_manager": "组合管理",
}

# ── signal → 中文 ────────────────────────────────────────────
SIGNAL_ZH = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}

# ── reasoning 常见固定术语 → 中文（按长度降序替换，长词优先）──
TERM_ZH = {
    "No valid trade available": "无可执行交易",
    "All neutral signals indicate no clear direction": "信号全中性，方向不明",
    "All signals neutral, no strong reason to trade": "信号全中性，无明确交易理由",
    "All signals neutral; hold to avoid unnecessary trading": "信号全中性，持有以避免无谓交易",
    "Insufficient data for analysis": "数据不足，无法分析",
    "Insufficient historical data": "历史数据不足",
    "Insufficient data": "数据不足",
    "No valid northbound data points": "无有效北向数据",
    "Insufficient northbound data": "北向数据不足",
    "no skin in the game": "无利益绑定",
    "skin in the game": "利益绑定",
    "tail risk": "尾部风险",
    "antifragility": "反脆弱性",
    "fragility": "脆弱性",
    "consistent FCF": "自由现金流稳定",
    "positive skew": "正偏度",
    "thin tails": "薄尾",
    "severe drawdown": "严重回撤",
    "low vol": "低波动",
    "Mixed signals": "信号分化",
    "no strong bullish": "无明显看多",
    "no strong bearish": "无明显看空",
    "mean reversion": "均值回归",
    "moderate": "中等",
    "bullish": "看多",
    "bearish": "看空",
    "neutral": "中性",
    "momentum": "动量",
    "volatility": "波动率",
    "trend": "趋势",
    "fear": "恐慌",
    "greed": "贪婪",
    "error": "错误",
    "unavailable": "不可用",
}
_TERM_KEYS = sorted(TERM_ZH, key=len, reverse=True)


def agent_name_zh(agent: str) -> str:
    """agent 内部名 → 中文显示名。未知名回退为去下划线的标题形式。"""
    key = (agent or "").strip().lower()
    key = re.sub(r"_agent$", "", key)
    if key in AGENT_NAME_ZH:
        return AGENT_NAME_ZH[key]
    return key.replace("_", " ").strip() or agent


def signal_zh(signal: str) -> str:
    return SIGNAL_ZH.get((signal or "").strip().lower(), signal or "中性")


def localize_reasoning(text) -> str:
    """把 reasoning 里的固定英文术语替换成中文（freeform 部分保留原文）。"""
    if not isinstance(text, str):
        text = str(text)
    out = text
    for k in _TERM_KEYS:
        if k in out:
            out = out.replace(k, TERM_ZH[k])
    return out


# 给前端 JS 用的 agent 名映射（注入 HTML）
def agent_name_map_json() -> dict:
    return dict(AGENT_NAME_ZH)
