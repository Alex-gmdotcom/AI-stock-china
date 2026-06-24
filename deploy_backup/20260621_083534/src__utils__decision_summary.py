"""
v3.1 强制结论层 — 无论各分析师信号如何，必须输出可操作的结构化结论。

背景：原版 portfolio_manager 在全员中性时输出 "hold 0 / No valid trade
available"（如 00148.HK 案例），等于分析白跑。本模块在 CLI 输出末尾追加
结论段，核心规则：

1. 区分两种中性，绝不混为一谈：
   - 数据不足型中性：置信度 < 50（常见于中国特色 agent 分析港股时缺
     北向/板块/东财新闻数据），这不是市场观点，是"没看到"
   - 多空平衡型中性：置信度 ≥ 50，真实研判后的中性，是有效观点
2. 数据不足型中性不参与评级计算，单独列入"数据缺口"段并给出补救动作
3. 永远输出 评级 + 信号构成 + 核心依据 + 下一步，禁止空结论
"""
from __future__ import annotations

from colorama import Fore, Style

# 置信度低于此值的中性信号视为"数据不足型中性"
DATA_STARVED_CONF = 50.0

# 理由文本命中这些关键词的中性信号，无论置信度高低均视为数据不足型
# （典型案例：资金流agent对港股给出"中性95%"，理由却是"北向数据不覆盖港股"
#  —— 这是对"没数据"很自信，不是市场观点）
DATA_GAP_KEYWORDS = (
    "不覆盖", "无数据", "数据缺", "数据不足", "缺失", "未返回", "无法获取",
    "no data", "unavailable", "not available", "404", "数据缺口", "无相关数据",
    # 真实数据错误的常见表述（区分"取不到"与"算出来是弱信号"）
    "no valid", "insufficient", "no data points", "not covered", "取不到",
    "无有效", "failed to fetch", "remotedisconnected", "proxyerror",
    "connection aborted", "接口故障", "接口不存在", "max retries",
)


def _is_data_starved(row: dict) -> bool:
    """数据不足判定 —— 以 reasoning 里的真实数据错误为准，而非单纯低置信。

    修正前: confidence < 50 一律判为"数据不足"，把 technical 等真实弱信号也误杀，
    并触发误导性的"代理劫持东财"告警。修正后: 只有 reasoning 命中真实数据错误关键词，
    或置信极低(<12)且理由空泛(没真正算出来)，才算数据不足。
    """
    text = row["reasoning"].lower()
    if any(k in text for k in DATA_GAP_KEYWORDS):
        return True
    if row["confidence"] < 12 and len(text.strip()) < 24:
        return True
    return False

HK_SUGGESTED_ANALYSTS = (
    "technical_analyst,valuation_analyst,fundamentals_analyst,"
    "warren_buffett,nassim_taleb,china_public_opinion"
)


def _norm_reasoning(r) -> str:
    if isinstance(r, dict):
        r = "; ".join(f"{k}:{v}" for k, v in list(r.items())[:3])
    s = str(r or "").replace("\n", " ").strip()
    return (s[:110] + "…") if len(s) > 110 else s


def _collect(ticker: str, analyst_signals: dict) -> list[dict]:
    rows = []
    for agent, by_ticker in (analyst_signals or {}).items():
        if not isinstance(by_ticker, dict):
            continue
        s = by_ticker.get(ticker)
        if not isinstance(s, dict):
            continue
        rows.append({
            "agent": agent.replace("_agent", "").replace("_", " "),
            "signal": str(s.get("signal", "neutral")).lower(),
            "confidence": float(s.get("confidence") or 0),
            "reasoning": _norm_reasoning(s.get("reasoning")),
        })
    return rows


def summarize_ticker(ticker: str, decision: dict | None, analyst_signals: dict) -> str:
    rows = _collect(ticker, analyst_signals)
    bull = [r for r in rows if r["signal"] == "bullish"]
    bear = [r for r in rows if r["signal"] == "bearish"]
    neutral = [r for r in rows if r["signal"] not in ("bullish", "bearish")]
    starved = [r for r in neutral if _is_data_starved(r)]
    balanced = [r for r in neutral if not _is_data_starved(r)]

    bull_w = sum(r["confidence"] for r in bull)
    bear_w = sum(r["confidence"] for r in bear)

    # ── 评级（确定性规则，永不为空）──
    if bull_w + bear_w > 0:
        net = (bull_w - bear_w) / (bull_w + bear_w)
        if net >= 0.5:
            rating, tone = "买入关注", Fore.RED
        elif net >= 0.15:
            rating, tone = "偏多观察", Fore.RED
        elif net > -0.15:
            rating, tone = "持有观察", Fore.YELLOW
        elif net > -0.5:
            rating, tone = "偏空减持", Fore.GREEN
        else:
            rating, tone = "回避", Fore.GREEN
        basis_pool = sorted(bull + bear, key=lambda r: -r["confidence"])[:2]
        basis = [
            f"{r['agent']}（{'多' if r['signal'] == 'bullish' else '空'},"
            f"{r['confidence']:.0f}%）: {r['reasoning']}"
            for r in basis_pool
        ]
    elif balanced:
        rating, tone = "持有观察（多空平衡型中性）", Fore.YELLOW
        basis = [
            f"{r['agent']}（中性,{r['confidence']:.0f}%）: {r['reasoning']}"
            for r in sorted(balanced, key=lambda r: -r["confidence"])[:2]
        ]
    else:
        rating, tone = "无法评级（信息不足，非市场观点）", Fore.YELLOW
        basis = ["所有分析师均为数据不足型中性 —— 本次运行未产生有效市场观点，"
                 "结论是『分析配置不当』而非『该股无机会』"]

    out = [f"\n{Fore.CYAN}═══ 【{ticker}】结论 ═══{Style.RESET_ALL}"]
    out.append(
        f"评级: {tone}{rating}{Style.RESET_ALL}   "
        f"信号构成: {len(bull)}多 / {len(bear)}空 / "
        f"{len(balanced)}有效中性 / {len(starved)}数据不足"
    )
    out.append("核心依据:")
    out += [f"  · {b}" for b in basis]

    if starved:
        out.append(f"{Fore.YELLOW}数据缺口（低置信中性，已剔除出评级计算）:{Style.RESET_ALL}")
        out.append("  · " + ", ".join(f"{r['agent']}({r['confidence']:.0f}%)" for r in starved))
        if ticker.upper().endswith(".HK"):
            out.append(f"  → 港股数据覆盖弱属正常，建议改用: --analysts {HK_SUGGESTED_ANALYSTS}")
        else:
            out.append("  → 注: 估值/资金/板块类 agent 依赖东财/同花顺财报·板块数据，"
                       "海外 IP 直连受限，需国内 VPS/代理通道；"
                       "趋势(technical)与叙事(舆情/政策)数据可达，应为有效信号。")

    # 实时价格锚点（腾讯源，失败静默跳过，不影响结论输出）
    try:
        from src.tools.quotes_fallback import fetch_tencent_quotes
        q = fetch_tencent_quotes([ticker]).get(ticker)
        if q:
            vr = f" 量比{q.volume_ratio:.2f}" if q.volume_ratio else ""
            out.append(f"价格锚点: {q.name} 现价{q.price:.2f}（当日{q.change_pct:+.2f}%{vr}）")
    except Exception:
        pass

    if decision:
        out.append(
            f"决策引擎: {decision.get('action', '?')} {decision.get('quantity', 0)}股 "
            f"(置信度{decision.get('confidence', 0)}%)"
        )
        # v3.4 绊线：原版 portfolio_manager 在风险层取不到价格时确定性短路输出
        # hold/0/100%/"No valid trade available"（compute_allowed_actions 仅剩 hold）
        # —— 这是数据链路故障的下游症状，不是市场判断，必须显式告知
        if "No valid trade" in str(decision.get("reasoning", "")):
            out.append(
                f"{Fore.YELLOW}⚠️ 数据链路警报: 决策引擎被确定性短路 —— 风险管理层"
                f"未能获取该标的价格（current_price=0 → 可买数量=0 → 仅剩hold）。"
                f"这不是'该股不值得交易'，是行情接口故障。"
                f"运行 doctor_china.py 排查。{Style.RESET_ALL}"
            )
    return "\n".join(out)


def print_decision_summary(result: dict, tickers: list[str]) -> None:
    """在 print_trading_output 之后调用，逐只输出强制结论。"""
    decisions = result.get("decisions") or {}
    signals = result.get("analyst_signals") or {}
    for t in tickers:
        d = decisions.get(t) if isinstance(decisions, dict) else None
        print(summarize_ticker(t, d, signals))
    print()


import re as _re

_ANSI = _re.compile(r"\x1b\[[0-9;]*m")


def summarize_ticker_plain(ticker: str, decision: dict | None,
                           analyst_signals: dict) -> str:
    """无 ANSI 色码的纯文本结论（供 Web 控制台等非终端环境使用）。"""
    return _ANSI.sub("", summarize_ticker(ticker, decision, analyst_signals))


def build_conclusions(result: dict, tickers: list[str]) -> dict[str, str]:
    """为每个 ticker 生成纯文本结论。供 web_app /api/analyze 注入响应。"""
    decisions = result.get("decisions") or {}
    signals = result.get("analyst_signals") or {}
    out = {}
    for t in tickers:
        d = decisions.get(t) if isinstance(decisions, dict) else None
        out[t] = summarize_ticker_plain(t, d, signals)
    return out
