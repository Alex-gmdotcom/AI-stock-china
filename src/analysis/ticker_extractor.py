# -*- coding: utf-8 -*-
"""
analysis/ticker_extractor.py — 早晚报标的抽取 (Phase 3 Step 11, v1.0.0)
========================================================================
规格: TECH v1.1 §8.5 / PRODUCT F3 / I2.2 (fail-loud 降级手动多选) /
      I2.3 (角色三分类 focus/risk/passing) / I6.3 (prompt 超限不发请求)

设计要点(对着真实 call_llm 行为,非规格伪代码):
  · call_llm 重试耗尽后**静默返回默认对象**(create_default_response),
    正是 I2.2 要防的静默回退 → 本模块传入哨兵 default_factory
    (llm_failed=True 标志),检测到哨兵即抛 TickerExtractionFailed。
  · call_llm 无 state 时默认 gpt-4.1/OPENAI(非 DeepSeek)→ 本模块构造
    最小 state 注入模型配置,复刻 main_china 自动选择(DEEPSEEK_API_KEY
    存在 → deepseek-v4-flash);可被显式 state / AIHF_EXTRACTOR_MODEL 覆盖。
  · 抽取结果逐个过真实 markets/ticker 解析器校验+归一化;非 A股/港股域
    (如美股/乱码)丢弃并记日志(F5 域约束);按归一化 ticker 去重,
    角色冲突取优先级 focus > risk > passing。
  · 空文本/超长文本(I6.3)不发请求直接 fail-loud。

调用方(Phase 4 briefings_archive/ingest)约定:
    try:
        tickers = extract_tickers(text, "morning")
    except TickerExtractionFailed as e:
        # 标记 extraction_failed=True,UI 弹错 + 降级手动多选(I2.2)
"""
from __future__ import annotations

import logging
import os
from typing import Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
__version__ = "1.0.0"

# I6.3: 早晚报典型 4-8K tokens;超过此字符数不发请求直接 fail-loud
MAX_TEXT_CHARS = 60000
_ROLE_PRIORITY = {"focus": 0, "risk": 1, "passing": 2}


class TickerExtractionFailed(Exception):
    """I2.2: 抽取失败必须显式上抛,调用方降级到手动多选,禁止静默跳过。"""


class ExtractedTicker(BaseModel):
    ticker: str                                   # 归一化后(600519.SH / 09880.HK)
    name: str = ""
    role: Literal["focus", "risk", "passing"]
    raw_mention: str = ""                         # 报告原文片段


class _LLMTicker(BaseModel):
    ticker: str = ""
    name: str = ""
    role: str = ""
    raw_mention: str = ""


class _LLMExtraction(BaseModel):
    tickers: list[_LLMTicker] = []
    llm_failed: bool = False                      # 哨兵位: default_factory 置 True


_SYSTEM_RULES = """你是金融文本解析助手。从下面的投研早晚报文本中识别所有被提到的股票标的。

【识别范围】仅限 A 股(6 位数字代码,如 600519/000858/300308/688019)与港股(数字代码+.HK,如 09880.HK)。文本可能只写公司名不写代码——若你能确定其代码则补全,不能确定则跳过该标的,禁止编造代码。

【角色三分类,每个标的必须归入其一】
- "focus"(重点关注): 标题级讨论、独立段落分析、含明确买卖/加减仓意见
- "risk"(风险标的): 负面提及、风险提示列表、利空事件主体
- "passing"(一笔带过): 对比中顺带提到、行业全景罗列、单句路过

【输出】只输出 JSON,不要任何其他文字,格式:
{"tickers": [{"ticker": "600519", "name": "贵州茅台", "role": "focus", "raw_mention": "原文中提到该标的的片段(≤60字)"}]}
规则: raw_mention 必须取自原文;同一标的只出现一次;文本中没有提到的标的一律不得出现。"""


def _default_model_state(state: Optional[dict]) -> dict:
    """模型配置注入(call_llm 无 state 默认 gpt-4.1/OPENAI 的坑)。
    优先级: 显式 state > AIHF_EXTRACTOR_MODEL/PROVIDER > DeepSeek 自动选择。"""
    if state is not None:
        return state
    model = os.environ.get("AIHF_EXTRACTOR_MODEL", "").strip()
    provider = os.environ.get("AIHF_EXTRACTOR_PROVIDER", "").strip()
    if not (model and provider):
        if os.environ.get("DEEPSEEK_API_KEY", "").strip():
            model, provider = "deepseek-v4-flash", "DeepSeek"   # 同 main_china 自动选择
        else:
            raise TickerExtractionFailed(
                "AI 抽取失败: 未配置 LLM(缺 DEEPSEEK_API_KEY 或 AIHF_EXTRACTOR_MODEL/"
                "PROVIDER)。请手动选择标的。")
    return {"metadata": {"model_name": model, "model_provider": provider}}


def _normalize_and_validate(item: _LLMTicker) -> Optional[ExtractedTicker]:
    """真实解析器校验+归一化;非 A股/港股域丢弃(F5);角色异常收敛 passing。"""
    try:
        from src.markets.ticker import normalize_ticker, is_china_ticker, is_hk_ticker
    except ImportError:
        from markets.ticker import normalize_ticker, is_china_ticker, is_hk_ticker  # type: ignore
    raw = (item.ticker or "").strip()
    if not raw:
        return None
    try:
        norm = normalize_ticker(raw)
    except Exception:
        logger.warning("ticker_extractor 丢弃不可解析标的: %r", raw[:20])
        return None
    if not (is_china_ticker(norm) or is_hk_ticker(norm)):
        logger.warning("ticker_extractor 丢弃非 A股/港股域标的: %r → %r", raw[:20], norm[:20])
        return None
    role = item.role if item.role in _ROLE_PRIORITY else "passing"
    if role != item.role:
        logger.warning("ticker_extractor 角色异常 %r 收敛为 passing (%s)", item.role[:20], norm)
    return ExtractedTicker(ticker=norm, name=(item.name or "").strip()[:40],
                           role=role, raw_mention=(item.raw_mention or "").strip()[:120])


def extract_tickers(briefing_text: str, briefing_type: str = "morning",
                    state: Optional[dict] = None) -> list[ExtractedTicker]:
    """早晚报文本 → 标的列表(F3)。任何失败路径抛 TickerExtractionFailed(I2.2)。"""
    text = (briefing_text or "").strip()
    if not text:
        raise TickerExtractionFailed("AI 抽取失败: 报告文本为空。请手动选择标的。")
    if len(text) > MAX_TEXT_CHARS:
        # I6.3: 超限不发请求(截断会静默丢失尾部标的,违反数据诚实)
        raise TickerExtractionFailed(
            f"AI 抽取失败: 报告超长({len(text)} 字符 > {MAX_TEXT_CHARS}),未发送请求。"
            "请拆分后重试或手动选择标的。")

    llm_state = _default_model_state(state)
    prompt = f"{_SYSTEM_RULES}\n\n【报告类型】{briefing_type}\n【报告全文】\n{text}"

    try:
        from src.utils.llm import call_llm
    except ImportError:
        from utils.llm import call_llm  # type: ignore

    result = call_llm(
        prompt=prompt,
        pydantic_model=_LLMExtraction,
        agent_name=None,
        state=llm_state,
        default_factory=lambda: _LLMExtraction(tickers=[], llm_failed=True),  # 哨兵
    )

    if result is None or getattr(result, "llm_failed", False):
        raise TickerExtractionFailed(
            "AI 抽取失败: LLM 调用重试耗尽。请手动选择标的。")

    # 校验 + 归一化 + 去重(角色优先级 focus > risk > passing)
    dedup: dict[str, ExtractedTicker] = {}
    for item in result.tickers:
        et = _normalize_and_validate(item)
        if et is None:
            continue
        prev = dedup.get(et.ticker)
        if prev is None or _ROLE_PRIORITY[et.role] < _ROLE_PRIORITY[prev.role]:
            dedup[et.ticker] = et
    out = list(dedup.values())
    logger.info("ticker_extractor 抽取完成: %d 原始 → %d 有效 (%s)",
                len(result.tickers), len(out), briefing_type)
    return out
