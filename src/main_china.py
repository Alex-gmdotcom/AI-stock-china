"""
AI Hedge Fund — China / Hong Kong Market Edition

Entry point for running the AI hedge fund against China A-share
(主板/创业板/科创板) and Hong Kong markets.

Usage:
    # A-share stocks
    poetry run python src/main_china.py --ticker 600519,000858,300750

    # Hong Kong stocks
    poetry run python src/main_china.py --ticker 00700.HK,09988.HK

    # Mixed A-share + HK
    poetry run python src/main_china.py --ticker 600519,00700.HK

    # With specific date range
    poetry run python src/main_china.py --ticker 600519 --start-date 2024-01-01 --end-date 2024-06-30

    # Show reasoning
    poetry run python src/main_china.py --ticker 600519 --show-reasoning

    # Select specific analysts
    poetry run python src/main_china.py --ticker 600519 --analysts china_public_opinion,china_policy,technical_analyst
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph
from colorama import Fore, Style, init

# ⭐ v3.1: 代理守卫必须最先安装（注入NO_PROXY，修复Windows系统代理劫持东财子域）
from src.tools import proxy_guard  # noqa: F401

# ⚠️ API bridge 必须在导入任何 agent 之前安装，
# 使原版 agent 的数据调用按市场自动路由（CN/HK → AKShare）
from src.tools.api_bridge import install as _install_api_bridge
_install_api_bridge()

from src.agents.portfolio_manager import portfolio_management_agent
from src.agents.risk_manager import risk_management_agent
from src.graph.state import AgentState
from src.markets.ticker import parse_ticker, TickerInfo
from src.markets.config import get_market_config, get_risk_context
from src.utils.analysts_china import (
    CHINA_ANALYST_CONFIG,
    get_china_analyst_nodes,
    get_recommended_china_analysts,
    get_recommended_hk_analysts,
)
from src.utils.display import print_trading_output
from src.utils.decision_summary import print_decision_summary
from src.utils.progress import progress
from src.tools.api_factory import detect_market_mode

load_dotenv()
init(autoreset=True)


def parse_hedge_fund_response(response):
    """Parses a JSON string and returns a dictionary."""
    try:
        return json.loads(response)
    except (json.JSONDecodeError, TypeError, Exception) as e:
        print(f"Error parsing response: {e}")
        return None


def create_china_workflow(selected_analysts: list[str] | None = None):
    """
    Create the LangGraph workflow for China/HK market analysis.

    Same structure as the original create_workflow() but uses
    China-specific analyst nodes.
    """
    workflow = StateGraph(AgentState)
    workflow.add_node("start_node", lambda state: state)

    # Get China analyst nodes
    analyst_nodes = get_china_analyst_nodes()

    # Default to recommended analysts if none selected
    if selected_analysts is None:
        selected_analysts = get_recommended_china_analysts()

    # Validate selected analysts exist
    valid_analysts = []
    for key in selected_analysts:
        if key in analyst_nodes:
            valid_analysts.append(key)
        else:
            print(f"{Fore.YELLOW}Warning: Unknown analyst '{key}', skipping.{Style.RESET_ALL}")

    if not valid_analysts:
        print(f"{Fore.RED}Error: No valid analysts selected.{Style.RESET_ALL}")
        print("Available analysts:")
        for key, config in sorted(CHINA_ANALYST_CONFIG.items(), key=lambda x: x[1]["order"]):
            print(f"  {key:30s} — {config['display_name']}")
        sys.exit(1)

    # Add analyst nodes (parallel execution)
    for key in valid_analysts:
        node_name, node_func = analyst_nodes[key]
        workflow.add_node(node_name, node_func)
        workflow.add_edge("start_node", node_name)

    # Risk management and portfolio management (sequential)
    workflow.add_node("risk_management_agent", risk_management_agent)
    workflow.add_node("portfolio_manager", portfolio_management_agent)

    for key in valid_analysts:
        node_name = analyst_nodes[key][0]
        workflow.add_edge(node_name, "risk_management_agent")

    workflow.add_edge("risk_management_agent", "portfolio_manager")
    workflow.add_edge("portfolio_manager", END)
    workflow.set_entry_point("start_node")

    return workflow


def run_china_hedge_fund(
    tickers: list[str],
    start_date: str,
    end_date: str,
    portfolio: dict,
    show_reasoning: bool = False,
    selected_analysts: list[str] | None = None,
    model_name: str = "gpt-4.1",
    model_provider: str = "OpenAI",
) -> dict:
    """Run the hedge fund for China/HK markets."""
    progress.start()

    try:
        workflow = create_china_workflow(selected_analysts)
        agent = workflow.compile()

        final_state = agent.invoke({
            "messages": [
                HumanMessage(
                    content="Make trading decisions based on the provided data.",
                )
            ],
            "data": {
                "tickers": tickers,
                "portfolio": portfolio,
                "start_date": start_date,
                "end_date": end_date,
                "analyst_signals": {},
            },
            "metadata": {
                "show_reasoning": show_reasoning,
                "model_name": model_name,
                "model_provider": model_provider,
            },
        })

        return {
            "decisions": parse_hedge_fund_response(
                final_state["messages"][-1].content
            ),
            "analyst_signals": final_state["data"]["analyst_signals"],
        }
    finally:
        progress.stop()


def parse_china_cli_inputs():
    """Parse command line inputs for China market mode."""
    parser = argparse.ArgumentParser(
        description="AI Hedge Fund — China/HK Market Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # A-share stocks (auto-detect exchange)
  %(prog)s --ticker 600519,000858,300750,688981

  # Hong Kong stocks
  %(prog)s --ticker 00700.HK,09988.HK,01810.HK

  # Explicit exchange suffix
  %(prog)s --ticker 600519.SH,000858.SZ

  # Custom date range
  %(prog)s --ticker 600519 --start-date 2024-01-01 --end-date 2024-06-30

  # Select specific analysts
  %(prog)s --ticker 600519 --analysts china_public_opinion,china_policy,technical_analyst

Available analysts:
  China-specific:
    china_public_opinion   — 舆情分析 + 黑天鹅检测
    china_policy           — 政策解读
    china_capital_flow     — 资金流向 (北向/融资融券/主力)
    china_sector_rotation  — 板块轮动

  Universal quantitative:
    technical_analyst      — 技术分析
    fundamentals_analyst   — 基本面分析
    valuation_analyst      — 估值分析
    growth_analyst         — 成长性分析

  Famous investors:
    nassim_taleb           — 黑天鹅风险 (尾部风险专家)
    warren_buffett         — 价值投资
    peter_lynch            — 成长投资
        """,
    )

    parser.add_argument(
        "--ticker", type=str, required=True,
        help="Comma-separated stock codes (e.g. 600519,000858,00700.HK)",
    )
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="Start date (YYYY-MM-DD). Default: 3 months before end date.",
    )
    parser.add_argument(
        "--end-date", type=str, default=None,
        help="End date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--analysts", type=str, default=None,
        help="Comma-separated analyst keys. Default: recommended set.",
    )
    parser.add_argument(
        "--show-reasoning", action="store_true",
        help="Show detailed reasoning from each analyst.",
    )
    parser.add_argument(
        "--initial-cash", type=float, default=1000000.0,
        help="Initial cash in portfolio (default: 1,000,000).",
    )
    parser.add_argument(
        "--model-name", type=str, default="gpt-4.1",
        help="LLM model name (default: gpt-4.1).",
    )
    parser.add_argument(
        "--model-provider", type=str, default="OpenAI",
        help="LLM provider (default: OpenAI).",
    )

    args = parser.parse_args()

    # Parse tickers
    raw_tickers = [t.strip() for t in args.ticker.split(",") if t.strip()]
    validated_tickers = []
    for raw in raw_tickers:
        try:
            info = parse_ticker(raw)
            validated_tickers.append(info.full_ticker)
            print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {info.display_name}")
        except ValueError as e:
            print(f"  {Fore.RED}✗{Style.RESET_ALL} {raw}: {e}")

    if not validated_tickers:
        print(f"\n{Fore.RED}No valid tickers provided.{Style.RESET_ALL}")
        sys.exit(1)

    # Dates
    end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
    if args.start_date:
        start_date = args.start_date
    else:
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (end_dt - relativedelta(months=3)).strftime("%Y-%m-%d")

    # Analysts
    selected_analysts = None
    if args.analysts:
        selected_analysts = [a.strip() for a in args.analysts.split(",")]

    return {
        "tickers": validated_tickers,
        "start_date": start_date,
        "end_date": end_date,
        "selected_analysts": selected_analysts,
        "show_reasoning": args.show_reasoning,
        "initial_cash": args.initial_cash,
        "model_name": args.model_name,
        "model_provider": args.model_provider,
    }


def main():
    """Main entry point."""
    # Windows 下重定向到文件时 stdout 默认走 GBK,编不出 ✓/emoji/部分中文 → UnicodeEncodeError。
    # 入口处强制 UTF-8,避免任何运行方式(管道/重定向/控制台)被本地编码噎住。
    import sys as _sys
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  AI Hedge Fund — China / Hong Kong Market Edition{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")

    inputs = parse_china_cli_inputs()
    tickers = inputs["tickers"]

    # Display market info
    market_mode = detect_market_mode(tickers)
    print(f"\n{Fore.YELLOW}Market mode: {market_mode.upper()}{Style.RESET_ALL}")
    print(f"Date range: {inputs['start_date']} → {inputs['end_date']}")

    # ⭐ v3.1: 纯港股自动切换分析师组合。
    # 中国特色agent（北向/板块/东财新闻）对港股天然缺数据 → 全员低置信中性 → 无效结论。
    if inputs["selected_analysts"] is None and market_mode == "hk":
        inputs["selected_analysts"] = get_recommended_hk_analysts()
        print(f"{Fore.YELLOW}检测到纯港股标的，自动切换港股分析师组合: "
              f"{', '.join(inputs['selected_analysts'])}{Style.RESET_ALL}")

    # ⭐ v3.1: 未显式指定模型时按 .env 自动选择，避免默认OpenAI报错（踩坑#5复发）
    import os as _os
    if inputs["model_name"] == "gpt-4.1" and inputs["model_provider"] == "OpenAI":
        if _os.getenv("DEEPSEEK_API_KEY"):
            inputs["model_name"], inputs["model_provider"] = "deepseek-v4-flash", "DeepSeek"
        elif _os.getenv("ANTHROPIC_API_KEY"):
            inputs["model_name"], inputs["model_provider"] = "claude-sonnet-4-6", "Anthropic"
        if inputs["model_provider"] != "OpenAI":
            print(f"{Fore.YELLOW}模型自动选择: {inputs['model_provider']} / "
                  f"{inputs['model_name']}{Style.RESET_ALL}")

    # Show market rules for each ticker
    for t in tickers:
        info = parse_ticker(t)
        risk_ctx = get_risk_context(info.market)
        print(f"\n{Fore.CYAN}--- {info.full_ticker} ---{Style.RESET_ALL}")
        for line in risk_ctx.split("\n"):
            print(f"  {line}")

    # Build portfolio
    portfolio = {
        "cash": inputs["initial_cash"],
        "margin_requirement": 0.5,
        "margin_used": 0.0,
        "positions": {
            t: {
                "long": 0, "short": 0,
                "long_cost_basis": 0.0, "short_cost_basis": 0.0,
                "short_margin_used": 0.0,
            }
            for t in tickers
        },
        "realized_gains": {
            t: {"long": 0.0, "short": 0.0}
            for t in tickers
        },
    }

    print(f"\n{Fore.GREEN}Running analysis...{Style.RESET_ALL}\n")

    result = run_china_hedge_fund(
        tickers=tickers,
        start_date=inputs["start_date"],
        end_date=inputs["end_date"],
        portfolio=portfolio,
        show_reasoning=inputs["show_reasoning"],
        selected_analysts=inputs["selected_analysts"],
        model_name=inputs["model_name"],
        model_provider=inputs["model_provider"],
    )

    print_trading_output(result)

    # ⭐ v3.1: 强制结论层 —— 即使全员中性也必须给出评级/依据/下一步
    print_decision_summary(result, tickers)


if __name__ == "__main__":
    main()
