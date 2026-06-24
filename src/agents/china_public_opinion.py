"""
China public opinion and black swan detection agent.

Analyzes pre-market sentiment from multiple sources to detect:
1. Overall market mood (fear/greed temperature)
2. Stock-specific sentiment from news and social media
3. Black swan events (regulatory shocks, geopolitical events,
   key figure statements, unexpected policy changes)

This agent should run FIRST in the workflow (before market open)
to set the risk context for all other agents.

Data sources (current):
  - 东方财富 stock news (via AKShare)
  - 东方财富 global financial news (via AKShare)

Data sources (planned, extensible via plugins):
  - 财联社电报 (fastest CN financial wire)
  - 雪球热帖 (social sentiment from China's StockTwits)
  - 微博财经KOL (weibo opinion leaders)
  - 央行/证监会/国务院公告 (regulatory announcements)
"""

from __future__ import annotations

import json
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, model_validator
from typing import Literal

from src.graph.state import AgentState, show_agent_reasoning
from src.tools.api_china import get_company_news, get_public_opinion
from src.utils.llm import call_llm
from src.utils.progress import progress


# ─────────────────────────────────────────────
# Output models
# ─────────────────────────────────────────────

class BlackSwanAssessment(BaseModel):
    """LLM's assessment of whether a black swan event is present."""
    detected: bool = Field(description="Whether a potential black swan event is detected")
    severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    description: str = Field(default="", description="Brief description of the event")
    affected_sectors: list[str] = Field(default_factory=list)
    recommended_caution: str = Field(default="", description="Recommended risk adjustment")

    @model_validator(mode="before")
    @classmethod
    def _coerce_bs(cls, data):
        if isinstance(data, dict) and not isinstance(data.get("detected"), bool):
            sev = str(data.get("severity") or "none").lower()
            data = {**data, "detected": sev not in ("none", "", "无")}
        return data


class SentimentAssessment(BaseModel):
    """LLM's overall sentiment assessment for a ticker."""
    signal: Literal["bullish", "bearish", "neutral"]
    confidence: int = Field(description="Confidence 0-100")
    market_temperature: Literal[
        "extreme_fear", "fear", "neutral", "greed", "extreme_greed"
    ]
    key_narratives: list[str] = Field(
        default_factory=list,
        description="Top 3 narratives driving sentiment"
    )
    black_swan: BlackSwanAssessment
    reasoning: str = Field(description="Concise reasoning for the assessment")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data):
        """容错：DeepSeek 常返回 {market_sentiment, key_risks, summary} 等变体
        而非规范 schema，这里映射 + 补默认，使其能解析成真实信号而非退中性。"""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if d.get("signal") not in ("bullish", "bearish", "neutral"):
            src = str(d.get("signal") or d.get("market_sentiment")
                      or d.get("overall_sentiment") or d.get("sentiment") or "").lower()
            if any(k in src for k in ("负", "利空", "空", "bear", "negative", "消极", "悲观")):
                d["signal"] = "bearish"
            elif any(k in src for k in ("正", "利好", "多头", "多", "bull", "positive", "积极", "乐观")):
                d["signal"] = "bullish"
            else:
                d["signal"] = "neutral"
        if not isinstance(d.get("confidence"), (int, float)):
            d["confidence"] = 50
        if d.get("market_temperature") not in ("extreme_fear", "fear", "neutral", "greed", "extreme_greed"):
            d["market_temperature"] = {"bearish": "fear", "bullish": "greed"}.get(d["signal"], "neutral")
        if not isinstance(d.get("black_swan"), dict):
            d["black_swan"] = {"detected": False, "severity": "none"}
        if not d.get("reasoning"):
            d["reasoning"] = str(d.get("summary") or d.get("key_risks")
                                 or d.get("analysis") or "（模型未返回 reasoning，已回填）")
        if not d.get("key_narratives"):
            kr = d.get("key_risks") or d.get("key_narratives")
            if isinstance(kr, str):
                d["key_narratives"] = [x.strip() for x in kr.replace("、", ",").split(",") if x.strip()][:3]
        return d


# ─────────────────────────────────────────────
# System prompt for the sentiment LLM
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """你是一名资深的中国A股/港股舆情分析师和风险预警专家。

你的职责是：
1. 分析个股相关新闻和舆论的情绪倾向
2. 评估整体市场情绪温度（极度恐慌→极度贪婪）
3. 检测可能的黑天鹅事件或重大风险信号

黑天鹅事件的识别标准：
- 突发的监管政策变化（如行业整顿、反垄断、教培双减类事件）
- 关键人物的意外发言（央行行长、证监会主席、国家领导人）
- 地缘政治事件（中美关系恶化、制裁、台海局势）
- 重大安全事故或公共卫生事件
- 上市公司爆雷（财务造假、实控人出事、突然退市风险）
- 流动性事件（银行挤兑、信托暴雷、房企债务违约）
- 国际市场大幅波动可能传导至A股

情绪温度判断标准：
- extreme_fear: 恐慌性抛售信号，融资余额大幅下降，多条负面重磅消息
- fear: 市场偏空，负面消息居多，投资者情绪谨慎
- neutral: 多空消息均衡，市场方向不明
- greed: 市场偏多，利好消息居多，投资者积极
- extreme_greed: 过度乐观，全民炒股迹象，可能需要警惕回调

分析时要特别注意：
- 消息发布的时间（开盘前vs盘后vs周末，影响力不同）
- 消息来源的权威性（官方>主流媒体>自媒体>股吧）
- A股"政策市"特征：政策面的一条消息可能抵消所有技术面信号
- 港股同时受A股情绪和国际资金流影响

输出要求（强制，违反即解析失败）：
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
}"""


# ─────────────────────────────────────────────
# Agent function
# ─────────────────────────────────────────────

def china_public_opinion_agent(
    state: AgentState,
    agent_id: str = "china_public_opinion_agent",
):
    """
    Analyzes public opinion and detects black swan risks for CN/HK stocks.

    This agent:
    1. Fetches news and public opinion data for each ticker
    2. Fetches market-wide sentiment data
    3. Uses an LLM to assess overall sentiment and detect black swans
    4. Outputs a signal with confidence and structured reasoning

    The black swan detection is the key differentiator — it flags
    events that could cause the market to gap down significantly,
    allowing the risk manager to adjust position sizing.
    """
    data = state.get("data", {})
    end_date = data.get("end_date")
    tickers = data.get("tickers", [])

    opinion_analysis = {}

    # Step 1: Fetch market-wide opinion data (shared across tickers)
    progress.update_status(agent_id, None, "Fetching market-wide sentiment")
    market_opinion = get_public_opinion(ticker=None, limit=30)
    market_headlines = [
        f"[{item.source}] {item.title}" for item in market_opinion[:20]
    ]

    for ticker in tickers:
        # Step 2: Fetch ticker-specific news
        progress.update_status(agent_id, ticker, "Fetching stock news")
        stock_news = get_company_news(
            ticker=ticker, end_date=end_date, limit=50,
        )
        stock_headlines = [
            f"[{n.source}] {n.title}" for n in stock_news[:20]
        ]

        # Step 3: Fetch ticker-specific public opinion
        progress.update_status(agent_id, ticker, "Fetching public opinion")
        stock_opinion = get_public_opinion(ticker=ticker, limit=30)
        opinion_headlines = [
            f"[{item.source}] {item.title}" for item in stock_opinion[:15]
        ]

        # Step 4: Combine all data for LLM analysis
        progress.update_status(agent_id, ticker, "Analyzing sentiment and risks")

        all_headlines = []
        if stock_headlines:
            all_headlines.append(f"=== 个股新闻 ({ticker}) ===")
            all_headlines.extend(stock_headlines)
        if opinion_headlines:
            all_headlines.append(f"\n=== 个股舆论 ({ticker}) ===")
            all_headlines.extend(opinion_headlines)
        if market_headlines:
            all_headlines.append("\n=== 市场整体资讯 ===")
            all_headlines.extend(market_headlines)

        if not all_headlines:
            # No data available — default to neutral
            opinion_analysis[ticker] = {
                "signal": "neutral",
                "confidence": 20,
                "reasoning": {
                    "summary": "Insufficient public opinion data available.",
                    "data_sources": 0,
                    "black_swan": {"detected": False},
                },
            }
            progress.update_status(agent_id, ticker, "Done (no data)")
            continue

        # Step 5: LLM assessment
        prompt = (
            f"请分析以下关于股票 {ticker} 的新闻和舆论信息，"
            f"评估市场情绪和潜在风险。\n\n"
            f"分析日期: {end_date}\n\n"
            + "\n".join(all_headlines)
            + "\n\n请严格按 system 中定义的 JSON schema 输出单个 JSON 对象，所有字段必填，禁止自创字段。"
        )

        assessment = call_llm(
            prompt=prompt,
            pydantic_model=SentimentAssessment,
            agent_name=agent_id,
            state=state,
            default_factory=lambda: SentimentAssessment(
                signal="neutral",
                confidence=30,
                market_temperature="neutral",
                key_narratives=[],
                black_swan=BlackSwanAssessment(detected=False),
                reasoning="LLM analysis failed, defaulting to neutral.",
            ),
        )

        # Step 6: Build structured output
        reasoning = {
            "market_temperature": assessment.market_temperature,
            "key_narratives": assessment.key_narratives,
            "black_swan": {
                "detected": assessment.black_swan.detected,
                "severity": assessment.black_swan.severity,
                "description": assessment.black_swan.description,
                "affected_sectors": assessment.black_swan.affected_sectors,
                "recommended_caution": assessment.black_swan.recommended_caution,
            },
            "data_sources": {
                "stock_news_count": len(stock_headlines),
                "opinion_count": len(opinion_headlines),
                "market_news_count": len(market_headlines),
            },
            "llm_reasoning": assessment.reasoning,
        }

        # Adjust confidence based on data quality
        data_quality_factor = min(1.0, (len(stock_headlines) + len(opinion_headlines)) / 10)
        adjusted_confidence = int(assessment.confidence * data_quality_factor)

        # If black swan detected, override signal to bearish with high confidence
        if assessment.black_swan.detected and assessment.black_swan.severity in ("high", "critical"):
            final_signal = "bearish"
            adjusted_confidence = max(adjusted_confidence, 80)
        else:
            final_signal = assessment.signal

        opinion_analysis[ticker] = {
            "signal": final_signal,
            "confidence": adjusted_confidence,
            "reasoning": reasoning,
        }

        progress.update_status(
            agent_id, ticker, "Done",
            analysis=json.dumps(reasoning, indent=4, ensure_ascii=False),
        )

    # Build message
    message = HumanMessage(
        content=json.dumps(opinion_analysis, ensure_ascii=False),
        name=agent_id,
    )

    if state.get("metadata", {}).get("show_reasoning"):
        show_agent_reasoning(opinion_analysis, "China Public Opinion Agent (舆情分析)")

    if "analyst_signals" not in state["data"]:
        state["data"]["analyst_signals"] = {}
    state["data"]["analyst_signals"][agent_id] = opinion_analysis

    progress.update_status(agent_id, None, "Done")

    return {
        "messages": [message],
        "data": state["data"],
    }
