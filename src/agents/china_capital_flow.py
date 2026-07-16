"""
China capital flow analysis agent.

Analyzes three key capital flow indicators unique to A-share markets:

1. 北向资金 (Northbound capital flow via Stock Connect)
   - Smart money proxy — foreign institutional investors
   - Strong correlation with market direction
   - Consecutive inflow/outflow streaks are powerful signals

2. 融资融券 (Margin trading)
   - Leveraged sentiment indicator
   - Rising margin balance = bullish leverage buildup
   - Rapid margin deleveraging = potential panic

3. 主力资金 (Main capital flow per stock)
   - Large-order net inflow tracks institutional activity
   - Divergence between price and capital flow = warning signal
"""

from __future__ import annotations

import json
import numpy as np
from langchain_core.messages import HumanMessage

from src.graph.state import AgentState, show_agent_reasoning
from src.tools.api_china import (
    get_northbound_flow,
    get_margin_trading,
    get_main_capital_flow,
)
from src.utils.progress import progress


def china_capital_flow_agent(
    state: AgentState,
    agent_id: str = "china_capital_flow_agent",
):
    """
    Analyzes capital flow patterns to generate trading signals.

    Capital flow is one of the most reliable short-term indicators
    in A-share markets, where retail vs institutional dynamics
    create predictable patterns.
    """
    data = state.get("data", {})
    tickers = data.get("tickers", [])

    flow_analysis = {}

    # Step 1: Northbound daily net-buy RETIRED (裁决⑦, 2024-08-19 披露机制调整)
    # ── marker: CAPFLOW_DECISION7_V1 ──
    # 日度北向净买入源头制度性消失(非权限/非bug, 见 TECH 坑23);
    # 资金面重构: 融资余额 0.45 + 个股主力资金 0.55。
    # 保留 northbound 汇报块(weight=0)以维持 reasoning 结构与前端兼容。
    progress.update_status(agent_id, None, "Northbound retired (裁决⑦)")
    nb_signal, nb_confidence = "neutral", 0
    nb_reasoning = {
        "status": "retired",
        "note": ("2024-08-19 起陆股通不再披露日度净买入(制度性停更, 非数据缺口); "
                 "北向腿退役(裁决⑦); tushare moneyflow_hsgt.north_money 改制后为成交总额, "
                 "禁止当净买入消费(坑23)"),
    }

    # Step 2: Market-wide margin trading
    progress.update_status(agent_id, None, "Fetching margin trading data")
    margin_data = get_margin_trading(limit=30)
    margin_signal, margin_confidence, margin_reasoning = _analyze_margin(margin_data)

    for ticker in tickers:
        # Step 3: Per-stock main capital flow
        progress.update_status(agent_id, ticker, "Fetching main capital flow")
        capital_flow = get_main_capital_flow(ticker, limit=20)
        stock_signal, stock_confidence, stock_reasoning = _analyze_stock_flow(
            capital_flow
        )

        # Step 4: Combine signals with weights
        # 裁决⑦: Margin 45% + Stock-specific 55% (northbound retired)
        progress.update_status(agent_id, ticker, "Combining flow signals")

        signal_scores = {
            "bullish": 0.0,
            "bearish": 0.0,
            "neutral": 0.0,
        }

        for signal, conf, weight in [
            (margin_signal, margin_confidence, 0.45),
            (stock_signal, stock_confidence, 0.55),
        ]:
            signal_scores[signal] += conf * weight

        # Determine overall signal
        # ── marker: REDLINE_FIX_CAPFLOW_DQ_V1 (I10.3 / I1.1) ──
        # 置信度必须反映数据覆盖度,而非归一化占比:
        # 旧式在三腿全缺数据时给出 NEUTRAL@95(2026-07-03 港股实锤),
        # 单腿(北向)缺失时给出 82% 高置信(2026-06 A股实锤)。
        # 修类:coverage = 有数据腿的权重和;置信度 ×coverage;
        #       coverage=0 → 强制 neutral@20;coverage<0.5 → 封顶 30。
        _dq_legs = (
            ("margin", margin_reasoning, 0.45),
            ("stock_flow", stock_reasoning, 0.55),
        )
        data_coverage = sum(w for _n, _r, w in _dq_legs
                            if not (isinstance(_r, dict) and "error" in _r))
        max_signal = max(signal_scores, key=signal_scores.get)
        total_weight_conf = sum(signal_scores.values())
        raw_share = (
            signal_scores[max_signal] / max(total_weight_conf, 1)
            if total_weight_conf > 0 else 0.0
        )
        if data_coverage <= 0:
            max_signal = "neutral"
            overall_confidence = 20
        else:
            overall_confidence = int(min(raw_share * 100 * data_coverage, 95))
            if data_coverage < 0.5:
                overall_confidence = min(overall_confidence, 30)

        reasoning = {
            "northbound_flow": {
                "signal": nb_signal,
                "confidence": nb_confidence,
                "details": nb_reasoning,
                "weight": 0.0,
            },
            "margin_trading": {
                "signal": margin_signal,
                "confidence": margin_confidence,
                "details": margin_reasoning,
                "weight": 0.45,
            },
            "stock_capital_flow": {
                "signal": stock_signal,
                "confidence": stock_confidence,
                "details": stock_reasoning,
                "weight": 0.55,
            },
            "combined": {
                "scores": {k: round(v, 2) for k, v in signal_scores.items()},
            },
            "data_quality": {
                "northbound": "retired-20240819(裁决⑦)",
                "margin": not (isinstance(margin_reasoning, dict) and "error" in margin_reasoning),
                "stock_flow": not (isinstance(stock_reasoning, dict) and "error" in stock_reasoning),
                "coverage": round(data_coverage, 2),
                "note": ("" if data_coverage >= 1.0 else
                         "【数据缺口】部分资金流输入缺失,置信度已按覆盖度降权(I10.3)"),
            },
        }

        flow_analysis[ticker] = {
            "signal": max_signal,
            "confidence": overall_confidence,
            "reasoning": reasoning,
        }

        progress.update_status(
            agent_id, ticker, "Done",
            analysis=json.dumps(reasoning, indent=4, ensure_ascii=False),
        )

    # Build message
    message = HumanMessage(
        content=json.dumps(flow_analysis, ensure_ascii=False),
        name=agent_id,
    )

    if state.get("metadata", {}).get("show_reasoning"):
        show_agent_reasoning(flow_analysis, "China Capital Flow Agent (资金流向)")

    if "analyst_signals" not in state["data"]:
        state["data"]["analyst_signals"] = {}
    state["data"]["analyst_signals"][agent_id] = flow_analysis

    progress.update_status(agent_id, None, "Done")

    return {
        "messages": [message],
        "data": state["data"],
    }


# ─────────────────────────────────────────────
# Analysis functions (deterministic, no LLM)
# ─────────────────────────────────────────────

def _analyze_northbound(data: list) -> tuple[str, int, dict]:
    """
    Analyze northbound capital flow trend.

    Key signals:
    - 5-day consecutive net inflow > 50亿/day → strong bullish
    - 5-day consecutive net outflow → bearish
    - Single-day massive outflow (>100亿) → panic signal
    """
    if not data or len(data) < 3:
        return "neutral", 20, {"error": "Insufficient northbound data"}

    recent = data[-10:]  # Last 10 days
    net_buys = [
        d.total_net_buy for d in recent
        if d.total_net_buy is not None
    ]

    if not net_buys:
        return "neutral", 20, {"error": "No valid northbound data points"}

    # Metrics
    avg_flow = np.mean(net_buys)
    recent_3d = np.mean(net_buys[-3:]) if len(net_buys) >= 3 else avg_flow
    consecutive_inflow = _count_consecutive_positive(net_buys)
    consecutive_outflow = _count_consecutive_negative(net_buys)
    max_single_outflow = min(net_buys) if net_buys else 0

    reasoning = {
        "avg_daily_flow_10d": round(avg_flow, 2),
        "avg_daily_flow_3d": round(recent_3d, 2),
        "consecutive_inflow_days": consecutive_inflow,
        "consecutive_outflow_days": consecutive_outflow,
        "latest_flow": round(net_buys[-1], 2) if net_buys else None,
    }

    # Signal logic
    if consecutive_inflow >= 5 and avg_flow > 30:
        return "bullish", 80, reasoning
    elif consecutive_inflow >= 3 and recent_3d > 20:
        return "bullish", 65, reasoning
    elif recent_3d > 0:
        return "bullish", 50, reasoning
    elif consecutive_outflow >= 5 or max_single_outflow < -100:
        return "bearish", 80, reasoning
    elif consecutive_outflow >= 3:
        return "bearish", 65, reasoning
    elif recent_3d < 0:
        return "bearish", 50, reasoning
    else:
        return "neutral", 40, reasoning


def _analyze_margin(data: list) -> tuple[str, int, dict]:
    """
    Analyze margin trading trends.

    Rising margin balance → bullish leverage buildup
    Falling margin balance → deleveraging / risk-off
    """
    if not data or len(data) < 5:
        return "neutral", 20, {"error": "Insufficient margin data"}

    balances = [
        d.margin_balance for d in data
        if d.margin_balance is not None
    ]

    if len(balances) < 5:
        return "neutral", 20, {"error": "Too few valid margin data points"}

    # Calculate trend
    recent_avg = np.mean(balances[-5:])
    older_avg = np.mean(balances[:5]) if len(balances) >= 10 else balances[0]
    change_pct = (recent_avg - older_avg) / older_avg * 100 if older_avg else 0

    reasoning = {
        "recent_avg_balance": round(recent_avg, 2),
        "change_pct": round(change_pct, 2),
        "trend": "rising" if change_pct > 1 else "falling" if change_pct < -1 else "flat",
    }

    if change_pct > 5:
        return "bullish", 70, reasoning
    elif change_pct > 1:
        return "bullish", 55, reasoning
    elif change_pct < -5:
        return "bearish", 70, reasoning
    elif change_pct < -1:
        return "bearish", 55, reasoning
    else:
        return "neutral", 40, reasoning


def _analyze_stock_flow(data: list) -> tuple[str, int, dict]:
    """Analyze per-stock main capital (大单/超大单) flow."""
    if not data or len(data) < 3:
        return "neutral", 20, {"error": "Insufficient stock flow data"}

    main_flows = [
        d.main_net_inflow for d in data
        if d.main_net_inflow is not None
    ]

    if not main_flows:
        return "neutral", 20, {"error": "No valid main flow data"}

    avg_flow = np.mean(main_flows)
    recent_3d = np.mean(main_flows[-3:]) if len(main_flows) >= 3 else avg_flow
    consecutive_in = _count_consecutive_positive(main_flows)
    consecutive_out = _count_consecutive_negative(main_flows)

    reasoning = {
        "avg_main_flow": round(avg_flow, 2),
        "recent_3d_avg": round(recent_3d, 2),
        "consecutive_inflow_days": consecutive_in,
        "consecutive_outflow_days": consecutive_out,
        # MAINFLOW_TUSHARE_V1: 口径来源(两源分单阈值不同, 幅度跨源不可比)
        "source": getattr(data[0], "source", None),
    }

    if consecutive_in >= 5 and recent_3d > 0:
        return "bullish", 75, reasoning
    elif consecutive_in >= 3:
        return "bullish", 60, reasoning
    elif recent_3d > 0:
        return "bullish", 50, reasoning
    elif consecutive_out >= 5:
        return "bearish", 75, reasoning
    elif consecutive_out >= 3:
        return "bearish", 60, reasoning
    elif recent_3d < 0:
        return "bearish", 50, reasoning
    else:
        return "neutral", 40, reasoning


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _count_consecutive_positive(values: list[float]) -> int:
    """Count consecutive positive values from the end of the list."""
    count = 0
    for v in reversed(values):
        if v > 0:
            count += 1
        else:
            break
    return count


def _count_consecutive_negative(values: list[float]) -> int:
    """Count consecutive negative values from the end of the list."""
    count = 0
    for v in reversed(values):
        if v < 0:
            count += 1
        else:
            break
    return count
