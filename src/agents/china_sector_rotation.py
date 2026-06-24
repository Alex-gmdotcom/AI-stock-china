"""
China sector rotation analysis agent.

A-share markets exhibit extremely strong sector/concept rotation patterns.
When a theme heats up (AI, new energy, military, semiconductor, etc.),
the entire sector moves together. This agent:

1. Tracks sector performance rankings and momentum
2. Identifies which sectors are heating up vs cooling down
3. Checks if the target stock's sector is in a favorable rotation phase
4. Detects dangerous late-cycle crowding (everyone piling in → reversal risk)

This is a deterministic agent (no LLM needed) — pure data analysis.
"""

from __future__ import annotations

import json
from langchain_core.messages import HumanMessage

from src.graph.state import AgentState, show_agent_reasoning
from src.tools.api_china import (
    get_sector_performance,
    get_stock_sector_info,
)
from src.utils.progress import progress


def china_sector_rotation_agent(
    state: AgentState,
    agent_id: str = "china_sector_rotation_agent",
):
    """
    Analyzes sector rotation patterns to generate trading signals.

    For each ticker, determines whether its sector is currently
    in a favorable (momentum) or unfavorable (crowded/exhausted) phase.
    """
    data = state.get("data", {})
    tickers = data.get("tickers", [])

    rotation_analysis = {}

    # Step 1: Get sector rankings
    progress.update_status(agent_id, None, "Fetching sector performance data")
    sectors = get_sector_performance(limit=50)

    if not sectors:
        # No sector data — return neutral for all tickers
        for ticker in tickers:
            rotation_analysis[ticker] = {
                "signal": "neutral",
                "confidence": 20,
                "reasoning": {"error": "Sector performance data unavailable"},
            }

        return _build_output(state, rotation_analysis, agent_id)

    # Step 2: Build sector rankings
    ranked_sectors = sorted(
        sectors,
        key=lambda s: s.change_pct or 0,
        reverse=True,
    )
    total_sectors = len(ranked_sectors)

    # Identify hot and cold sectors
    hot_sectors = [s.sector_name for s in ranked_sectors[:5]]
    cold_sectors = [s.sector_name for s in ranked_sectors[-5:]]

    # Market breadth: are most sectors rising or falling?
    up_count = sum(1 for s in sectors if (s.change_pct or 0) > 0)
    down_count = sum(1 for s in sectors if (s.change_pct or 0) < 0)
    breadth_ratio = up_count / max(up_count + down_count, 1)

    market_breadth = {
        "sectors_up": up_count,
        "sectors_down": down_count,
        "breadth_ratio": round(breadth_ratio, 3),
        "assessment": (
            "strong" if breadth_ratio > 0.7
            else "healthy" if breadth_ratio > 0.5
            else "weak" if breadth_ratio > 0.3
            else "very_weak"
        ),
    }

    for ticker in tickers:
        progress.update_status(agent_id, ticker, "Analyzing sector position")

        # Get this stock's sector membership
        sector_info = get_stock_sector_info(ticker)
        stock_industry = sector_info.industry if sector_info else None

        # Find the stock's sector in the rankings
        sector_rank = None
        sector_change = None
        sector_turnover = None

        if stock_industry:
            for i, s in enumerate(ranked_sectors):
                if s.sector_name == stock_industry:
                    sector_rank = i + 1
                    sector_change = s.change_pct
                    sector_turnover = s.turnover_rate
                    break

        # Generate signal based on sector position
        signal, confidence = _evaluate_sector_position(
            sector_rank=sector_rank,
            total_sectors=total_sectors,
            sector_change=sector_change,
            sector_turnover=sector_turnover,
            breadth_ratio=breadth_ratio,
        )

        reasoning = {
            "stock_industry": stock_industry or "unknown",
            "sector_rank": f"{sector_rank}/{total_sectors}" if sector_rank else "not found",
            "sector_change_pct": sector_change,
            "sector_turnover_rate": sector_turnover,
            "market_breadth": market_breadth,
            "hot_sectors_today": hot_sectors[:3],
            "cold_sectors_today": cold_sectors[:3],
            "concepts": (sector_info.concepts[:5] if sector_info else []),
        }

        rotation_analysis[ticker] = {
            "signal": signal,
            "confidence": confidence,
            "reasoning": reasoning,
        }

        progress.update_status(
            agent_id, ticker, "Done",
            analysis=json.dumps(reasoning, indent=4, ensure_ascii=False),
        )

    return _build_output(state, rotation_analysis, agent_id)


def _evaluate_sector_position(
    sector_rank: int | None,
    total_sectors: int,
    sector_change: float | None,
    sector_turnover: float | None,
    breadth_ratio: float,
) -> tuple[str, int]:
    """
    Evaluate signal based on the stock's sector ranking.

    Logic:
    - Top 20% sectors with healthy turnover → bullish (momentum)
    - Top 20% but extreme turnover → caution (possible crowding)
    - Bottom 20% sectors → bearish (cold sector, no tailwind)
    - Broad market breadth modifies confidence
    """
    if sector_rank is None:
        return "neutral", 25

    percentile = sector_rank / max(total_sectors, 1)

    # Top quintile — sector has momentum
    if percentile <= 0.20:
        # Check for crowding (extreme turnover might indicate late-stage)
        if sector_turnover and sector_turnover > 10:
            # Very high turnover — possible blow-off top
            return "neutral", 45
        if breadth_ratio > 0.5:
            return "bullish", 70
        else:
            # Hot sector but weak breadth — selective
            return "bullish", 55

    # Second quintile — decent positioning
    elif percentile <= 0.40:
        if breadth_ratio > 0.6:
            return "bullish", 55
        else:
            return "neutral", 45

    # Middle — no strong edge
    elif percentile <= 0.60:
        return "neutral", 40

    # Fourth quintile — underperforming
    elif percentile <= 0.80:
        if breadth_ratio < 0.4:
            return "bearish", 55
        else:
            return "neutral", 40

    # Bottom quintile — cold sector
    else:
        if sector_change is not None and sector_change < -2:
            return "bearish", 70
        return "bearish", 55


def _build_output(
    state: AgentState,
    analysis: dict,
    agent_id: str,
) -> dict:
    """Build the standard agent output."""
    message = HumanMessage(
        content=json.dumps(analysis, ensure_ascii=False),
        name=agent_id,
    )

    if state.get("metadata", {}).get("show_reasoning"):
        show_agent_reasoning(analysis, "China Sector Rotation Agent (板块轮动)")

    if "analyst_signals" not in state["data"]:
        state["data"]["analyst_signals"] = {}
    state["data"]["analyst_signals"][agent_id] = analysis

    progress.update_status(agent_id, None, "Done")

    return {
        "messages": [message],
        "data": state["data"],
    }
