"""
Extended analyst configuration for China / Hong Kong markets.

Adds four China-specific agents to the base analyst roster,
and adjusts agent descriptions for the CN/HK context.

The China agents are:
  1. china_public_opinion - 舆情分析 + 黑天鹅检测 (runs first)
  2. china_policy          - 政策解读
  3. china_capital_flow    - 资金流向 (北向/融资融券/主力)
  4. china_sector_rotation - 板块轮动

These work alongside the existing quantitative agents
(technicals, fundamentals, valuation) which remain valid
for any market.

Usage:
    from src.utils.analysts_china import CHINA_ANALYST_CONFIG, get_china_analyst_nodes

    # Get all China-applicable analysts
    nodes = get_china_analyst_nodes()

    # Or get a recommended subset for China A-shares
    recommended = get_recommended_china_analysts()
"""

from __future__ import annotations

from src.agents.china_public_opinion import china_public_opinion_agent
from src.agents.china_policy import china_policy_agent
from src.agents.china_capital_flow import china_capital_flow_agent
from src.agents.china_sector_rotation import china_sector_rotation_agent

# Import existing agents that work well for China
from src.agents.technicals import technical_analyst_agent
from src.agents.fundamentals import fundamentals_analyst_agent
from src.agents.valuation import valuation_analyst_agent
from src.agents.news_sentiment import news_sentiment_agent
from src.agents.nassim_taleb import nassim_taleb_agent
from src.agents.cathie_wood import cathie_wood_agent
from src.agents.peter_lynch import peter_lynch_agent
from src.agents.warren_buffett import warren_buffett_agent
from src.agents.growth_agent import growth_analyst_agent


# ─────────────────────────────────────────────
# China-specific analyst definitions
# ─────────────────────────────────────────────

CHINA_SPECIFIC_ANALYSTS = {
    "china_public_opinion": {
        "display_name": "舆情分析师 (Public Opinion)",
        "description": "Sentiment and Black Swan Detector",
        "investing_style": (
            "Analyzes news, social media, and public opinion from Chinese "
            "financial platforms (东方财富/雪球/财联社) to detect market sentiment "
            "shifts and potential black swan events before market open. "
            "This is the first-line defense against overnight risk."
        ),
        "agent_func": china_public_opinion_agent,
        "type": "china_analyst",
        "order": 100,
    },
    "china_policy": {
        "display_name": "政策解读师 (Policy Analyst)",
        "description": "China Macro Policy Interpreter",
        "investing_style": (
            "Interprets regulatory and macro policy signals from the State Council, "
            "PBOC, CSRC, NDRC, and other government bodies. In China's 'policy market', "
            "a single policy announcement can override all technical and fundamental signals. "
            "Classifies policy impact by type, duration, and magnitude."
        ),
        "agent_func": china_policy_agent,
        "type": "china_analyst",
        "order": 101,
    },
    "china_capital_flow": {
        "display_name": "资金流向分析师 (Capital Flow)",
        "description": "Northbound & Margin Flow Tracker",
        "investing_style": (
            "Tracks three key capital flow indicators: northbound capital via "
            "Stock Connect (smart money proxy), margin trading balance (leveraged "
            "sentiment), and per-stock institutional order flow (主力资金). "
            "These are among the most reliable short-term indicators for A-shares."
        ),
        "agent_func": china_capital_flow_agent,
        "type": "china_analyst",
        "order": 102,
    },
    "china_sector_rotation": {
        "display_name": "板块轮动分析师 (Sector Rotation)",
        "description": "A-Share Sector Momentum Tracker",
        "investing_style": (
            "Monitors sector and concept plate performance rankings to identify "
            "rotation patterns. A-share markets exhibit extremely strong sector "
            "co-movement — when a theme heats up, the entire plate moves together. "
            "Detects both momentum opportunities and late-cycle crowding risks."
        ),
        "agent_func": china_sector_rotation_agent,
        "type": "china_analyst",
        "order": 103,
    },
}

# ─────────────────────────────────────────────
# Curated existing analysts for China markets
# ─────────────────────────────────────────────

# These existing agents work well for CN/HK markets.
# We include them with China-context notes.
CHINA_COMPATIBLE_EXISTING = {
    "technical_analyst": {
        "display_name": "Technical Analyst",
        "description": "Chart Pattern Specialist (A-share adapted)",
        "investing_style": (
            "Technical analysis with awareness of A-share constraints: "
            "T+1 settlement, daily price limits (±10% main board, ±20% ChiNext/STAR), "
            "and the impact of limit-up/limit-down on indicator reliability."
        ),
        "agent_func": technical_analyst_agent,
        "type": "analyst",
        "order": 200,
    },
    "fundamentals_analyst": {
        "display_name": "Fundamentals Analyst",
        "description": "Financial Statement Specialist",
        "investing_style": (
            "Analyzes financial statements and key metrics. For A-shares, pays "
            "attention to Chinese accounting standards (CAS) nuances and "
            "government subsidies that can distort reported earnings."
        ),
        "agent_func": fundamentals_analyst_agent,
        "type": "analyst",
        "order": 201,
    },
    "valuation_analyst": {
        "display_name": "Valuation Analyst",
        "description": "Company Valuation Specialist",
        "investing_style": (
            "Calculates intrinsic value using multiple valuation models. "
            "A-share valuations often carry a premium to global peers due to "
            "limited investment alternatives and retail investor dominance."
        ),
        "agent_func": valuation_analyst_agent,
        "type": "analyst",
        "order": 202,
    },
    "growth_analyst": {
        "display_name": "Growth Analyst",
        "description": "Growth Specialist",
        "investing_style": (
            "Analyzes growth trends and valuation for growth opportunities. "
            "Particularly relevant for ChiNext and STAR Market listings."
        ),
        "agent_func": growth_analyst_agent,
        "type": "analyst",
        "order": 203,
    },
    "nassim_taleb": {
        "display_name": "Nassim Taleb",
        "description": "Black Swan Risk Analyst",
        "investing_style": (
            "Focuses on tail risk, antifragility, and asymmetric payoffs. "
            "Especially relevant for A-shares where policy black swans, "
            "regulatory crackdowns, and geopolitical events can cause "
            "sudden multi-day limit-down situations."
        ),
        "agent_func": nassim_taleb_agent,
        "type": "analyst",
        "order": 204,
    },
    "warren_buffett": {
        "display_name": "Warren Buffett",
        "description": "The Oracle of Omaha",
        "investing_style": (
            "Value investing with focus on competitive moats and management quality. "
            "Applicable to large-cap A-shares and Hong Kong blue chips with "
            "established business models."
        ),
        "agent_func": warren_buffett_agent,
        "type": "analyst",
        "order": 205,
    },
    "peter_lynch": {
        "display_name": "Peter Lynch",
        "description": "The 10-Bagger Investor",
        "investing_style": (
            "Invests in companies with understandable business models. "
            "'Buy what you know' philosophy maps well to domestic Chinese "
            "consumer brands and everyday businesses."
        ),
        "agent_func": peter_lynch_agent,
        "type": "analyst",
        "order": 206,
    },
}


# ─────────────────────────────────────────────
# Combined configuration
# ─────────────────────────────────────────────

CHINA_ANALYST_CONFIG = {
    **CHINA_SPECIFIC_ANALYSTS,
    **CHINA_COMPATIBLE_EXISTING,
}


def get_china_analyst_nodes() -> dict:
    """Get all China-applicable analyst nodes."""
    return {
        key: (f"{key}_agent", config["agent_func"])
        for key, config in CHINA_ANALYST_CONFIG.items()
    }


def get_china_agents_list() -> list[dict]:
    """Get the list of agents for API responses."""
    return [
        {
            "key": key,
            "display_name": config["display_name"],
            "description": config["description"],
            "investing_style": config["investing_style"],
            "order": config["order"],
        }
        for key, config in sorted(
            CHINA_ANALYST_CONFIG.items(),
            key=lambda x: x[1]["order"],
        )
    ]


def get_recommended_china_analysts() -> list[str]:
    """
    Return the recommended analyst keys for typical China A-share analysis.

    This is the default selection when no specific analysts are chosen.
    Balances China-specific signals with universal quantitative analysis.
    """
    return [
        # China-specific (must have)
        "china_public_opinion",
        "china_policy",
        "china_capital_flow",
        "china_sector_rotation",
        # Universal quantitative (always useful)
        "technical_analyst",
        "fundamentals_analyst",
        "valuation_analyst",
        # Famous investors (selective)
        "nassim_taleb",    # Tail risk is critical in A-shares
        "warren_buffett",  # Value framework
    ]


def get_recommended_hk_analysts() -> list[str]:
    """
    Return the recommended analyst keys for Hong Kong market.

    HK sits between A-share and international dynamics.
    """
    return [
        "china_public_opinion",
        "china_policy",
        "china_capital_flow",
        # Universal
        "technical_analyst",
        "fundamentals_analyst",
        "valuation_analyst",
        "growth_analyst",
        # Famous investors
        "nassim_taleb",
        "warren_buffett",
        "peter_lynch",
    ]
