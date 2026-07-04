# -*- coding: utf-8 -*-
"""
analysis/dcf.py — 粗 DCF 估值 + 可调假设 (Phase 3 Step 10, v1.0.0)
====================================================================
规格: TECH v1.1 §8.1 (D4 v2) / PRODUCT F9 / I10.1 (TTM) / I10.2 (禁自带 WACC 表) /
      I1.4 (假设可见可调) / I10.3 (高 capex 护栏关联)

D4 v2 核心纪律:
  · 基准 WACC 一律路由自 markets/market_config.py(单一真相源,I10.2):
    base = rf + ERP(CAPM β=1) → CN 7.3% / HK 10.0% / US 10.5%
    本模块的行业表只提供 ±pp 风险溢价调整,任何绝对 WACC 表都违反 I10.2。
  · 基期 FCF = tushare 现金流真值,经修① _ttm_from_ytd 转 TTM(I10.1,
    复用 line_items_china 的 canonical 实现,不另写并行 TTM 逻辑)。
  · 高 capex 护栏(安集 688019 教训): capex_ttm/营收 > CAPEX_RATIO_GUARD 或
    TTM FCF < 0 → confidence_note 强制降级"当期 FCF 被再投资压制,DCF 仅作
    下界参考",guard_triggered=True 供 valuation 侧禁单方法高置信(I10.3)。
    注: 规格原文为"行业 90 分位",真分位需全行业现金流(~20+ 次调用),
    v1 用固定阈值 0.20 近似,真分位列 v1.1 精化项(记录在案)。
  · 粗口径: PV(FCF 两段) 直接视作权益价值,未做净债务调整(spec v1 原样)。

分层(同 peer_compare):
  · 纯计算核心: two_stage_dcf / sensitivity_band / wacc_for — 零 IO
  · 数据适配层: tushare_data.get_abs_periods + daily_basic + index_member_all,
    逐接口 fail-soft,缺数据 → data_gaps,绝不编造(I1.1)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)
__version__ = "1.0.0"

# 高 capex 护栏阈值(capex_ttm / revenue_ttm);真行业 90 分位为 v1.1 精化项
CAPEX_RATIO_GUARD = 0.20
# 敏感性带宽(wacc / 永续g 各 ±1pp,D4 v2)
SENSITIVITY_PP = 0.01
DEFAULT_CONFIDENCE_NOTE = "假设敏感性高,参考用"
GUARD_CONFIDENCE_NOTE = "当期 FCF 被再投资压制,DCF 仅作下界参考"

# ── 行业调整表(D4 v2,TECH §8.1 原文机械换算: 调整pp = v1.0 原表 WACC − 10%) ──
# (风险溢价调整 pp, 永续 g, 5 年增速) — 申万一级全 31 行;判定不到回退 "综合"
INDUSTRY_ADJUSTMENTS: dict[str, tuple[float, float, float]] = {
    # === 大消费(稳健消费 / 高质量) ===
    "食品饮料": (+0.000, 0.030, 0.12),
    "家用电器": (+0.000, 0.030, 0.10),
    "纺织服饰": (+0.010, 0.025, 0.07),
    "商贸零售": (+0.010, 0.025, 0.07),
    "美容护理": (+0.000, 0.035, 0.15),
    "社会服务": (+0.010, 0.035, 0.15),
    "农林牧渔": (+0.010, 0.025, 0.08),
    # === 科技 / 高成长 ===
    "电子":     (+0.010, 0.040, 0.25),
    "计算机":   (+0.020, 0.040, 0.25),
    "通信":     (+0.000, 0.030, 0.12),
    "传媒":     (+0.020, 0.035, 0.15),
    "医药生物": (+0.000, 0.040, 0.18),
    # === 新能源 / 政策驱动 ===
    "电力设备": (+0.010, 0.040, 0.22),
    "汽车":     (+0.010, 0.035, 0.18),
    "国防军工": (+0.000, 0.035, 0.18),
    "环保":     (+0.010, 0.030, 0.12),
    "机械设备": (+0.010, 0.030, 0.12),
    # === 周期 / 大宗 ===
    "钢铁":     (+0.020, 0.020, 0.05),
    "有色金属": (+0.020, 0.025, 0.08),
    "基础化工": (+0.010, 0.025, 0.07),
    "煤炭":     (+0.020, 0.015, 0.03),
    "石油石化": (+0.010, 0.020, 0.05),
    "轻工制造": (+0.010, 0.025, 0.08),
    "建筑材料": (+0.020, 0.020, 0.05),
    "建筑装饰": (+0.020, 0.020, 0.05),
    # === 公用 / 金融(低风险低增长) ===
    "公用事业": (-0.020, 0.020, 0.05),
    "交通运输": (-0.010, 0.025, 0.06),
    "银行":     (-0.020, 0.020, 0.05),
    "非银金融": (+0.000, 0.025, 0.08),
    # === 房地产 / 综合 ===
    "房地产":   (+0.030, 0.015, 0.03),
    "综合":     (+0.010, 0.025, 0.08),
}


# ══════════════════════ 数据模型(TECH §3.5) ══════════════════════

class DCFAssumptions(BaseModel):
    perpetual_growth_rate: float
    wacc: float
    five_year_growth_rate: float
    fcf_base: float


class DCFResult(BaseModel):
    ticker: str = ""
    assumptions: Optional[DCFAssumptions] = None
    intrinsic_value_per_share: Optional[float] = None
    intrinsic_value_low: Optional[float] = None     # 敏感性 −侧
    intrinsic_value_high: Optional[float] = None    # 敏感性 +侧
    current_price: Optional[float] = None
    margin_of_safety_pct: Optional[float] = None    # (intrinsic−current)/intrinsic
    confidence_note: str = DEFAULT_CONFIDENCE_NOTE
    guard_triggered: bool = False                   # 高 capex 护栏(I10.3 联动)
    # 透明度(I1.4 / I8.2): 假设来源与数据链可见
    industry_l1: str = ""
    wacc_source: str = ""                           # 如 "MarketConfig(CN) rf+ERP=7.3% + 行业调整+0.0pp"
    fcf_ttm: Optional[float] = None
    capex_ratio: Optional[float] = None             # capex_ttm/revenue_ttm
    asof: str = ""
    data_gaps: list[str] = []
    source_chain: dict = {}


# ══════════════════════ 纯计算核心(零 IO) ══════════════════════

def two_stage_dcf(fcf_base: float, growth_5y: float,
                  perpetual_g: float, wacc: float) -> Optional[float]:
    """两段式 DCF: 5 年按 growth_5y 增长 + 永续段。
    返回现值合计;参数不合法(wacc ≤ 永续g / wacc ≤ 0)→ None(不编造)。"""
    if fcf_base is None or wacc is None or wacc <= 0 or wacc <= perpetual_g:
        return None
    pv = 0.0
    fcf = float(fcf_base)
    for year in range(1, 6):
        fcf = fcf * (1.0 + growth_5y)
        pv += fcf / ((1.0 + wacc) ** year)
    terminal = fcf * (1.0 + perpetual_g) / (wacc - perpetual_g)
    pv += terminal / ((1.0 + wacc) ** 5)
    return pv


def sensitivity_band(fcf_base: float, growth_5y: float, perpetual_g: float,
                     wacc: float, pp: float = SENSITIVITY_PP) -> tuple[Optional[float], Optional[float]]:
    """wacc / 永续g 各 ±1pp 四组(D4 v2),返回 (low, high);全无效 → (None, None)。"""
    combos = [
        (wacc + pp, perpetual_g),
        (wacc - pp, perpetual_g),
        (wacc, perpetual_g + pp),
        (wacc, perpetual_g - pp),
    ]
    vals = [v for w, g in combos
            if (v := two_stage_dcf(fcf_base, growth_5y, g, w)) is not None]
    if not vals:
        return None, None
    return min(vals), max(vals)


def industry_params(industry_l1: str) -> tuple[float, float, float]:
    """(调整pp, 永续g, 5年增速);未识别行业回退 '综合'。"""
    return INDUSTRY_ADJUSTMENTS.get(industry_l1 or "", INDUSTRY_ADJUSTMENTS["综合"])


def wacc_for(base_wacc: float, industry_l1: str) -> float:
    """D4 v2: 基准 WACC(MarketConfig 路由) + 行业风险溢价调整。禁绝对表(I10.2)。"""
    return base_wacc + industry_params(industry_l1)[0]


def capex_guard(fcf_ttm: Optional[float], capex_ttm_abs: Optional[float],
                revenue_ttm: Optional[float],
                threshold: float = CAPEX_RATIO_GUARD) -> tuple[bool, Optional[float]]:
    """高 capex 成长股护栏(安集教训)。返回 (触发?, capex_ratio)。"""
    ratio = None
    if capex_ttm_abs is not None and revenue_ttm:
        ratio = abs(capex_ttm_abs) / abs(revenue_ttm)
    triggered = bool((fcf_ttm is not None and fcf_ttm < 0)
                     or (ratio is not None and ratio > threshold))
    return triggered, ratio


# ══════════════════════ 数据适配层(fail-soft) ══════════════════════

def _tsd():
    try:
        from src.tools import tushare_data as m
    except ImportError:
        import tushare_data as m  # type: ignore
    return m


def _base_wacc(norm: str) -> tuple[float, str]:
    """基准 WACC 经 MarketConfig 路由(I10.2 单一真相源): rf + ERP(CAPM β=1)。"""
    try:
        from src.markets.market_config import for_ticker
    except ImportError:
        from markets.market_config import for_ticker  # type: ignore
    cfg = for_ticker(norm)
    base = cfg.risk_free_rate + cfg.equity_risk_premium
    return base, f"MarketConfig({cfg.market}) rf+ERP={base*100:.1f}%"


def _fetch_industry_l1(norm: str) -> str:
    """申万一级行业名;失败/港股 → ''(回退 '综合' 并标注)。"""
    if norm.endswith(".HK"):
        return ""
    t = _tsd()
    if not t.available():
        return ""
    df = t._query("index_member_all", ts_code=norm, is_new="Y",
                  fields="ts_code,l1_code,l1_name")
    if df is None or len(df) == 0:
        return ""
    return str(df.iloc[0].get("l1_name") or "")


def _fetch_flows_ttm(norm: str, asof: str) -> tuple[Optional[float], Optional[float], Optional[float], list[str]]:
    """(fcf_ttm, capex_ttm(负), revenue_ttm, gaps)。
    tushare 真值(get_abs_periods,PIT 已过滤)→ 修① _ttm_from_ytd(canonical)。
    TTM 不可得 → 缺口,禁 YTD 直用(I10.1)。"""
    gaps: list[str] = []
    t = _tsd()
    periods = t.get_abs_periods(norm, asof, limit=8) if t.available() else {}
    if not periods:
        return None, None, None, ["现金流真值缺失(tushare 无数据/无 token)【数据缺口】"]
    try:
        from src.tools.line_items_china import _ttm_from_ytd
    except ImportError:
        from tools.line_items_china import _ttm_from_ytd  # type: ignore
    fields = frozenset({"free_cash_flow", "capital_expenditure", "revenue"})
    ttm = _ttm_from_ytd(periods, fields)
    if not ttm:
        return None, None, None, ["TTM 转换无结果(报告期结构异常)【数据缺口】"]
    latest = sorted(ttm.keys(), reverse=True)[0]
    slot = ttm[latest]
    raw = periods.get(latest, {})
    fcf = slot.get("free_cash_flow")
    capex = slot.get("capital_expenditure")
    revenue = slot.get("revenue")
    # 回溯期不足时 _ttm_from_ytd fail-soft 保留 YTD 原值 → 非年报期即"疑似 YTD"
    if latest[5:] != "12-31":
        for name, ttm_v, raw_v in (("FCF", fcf, raw.get("free_cash_flow")),
                                   ("capex", capex, raw.get("capital_expenditure")),
                                   ("营收", revenue, raw.get("revenue"))):
            if ttm_v is not None and raw_v is not None and ttm_v == raw_v:
                gaps.append(f"{name} 回溯期不足,TTM 退化为 YTD(报告期 {latest})【数据缺口】")
    if fcf is None:
        gaps.append("FCF TTM 缺失【数据缺口】")
    return fcf, capex, revenue, gaps


def _fetch_price_shares(norm: str) -> tuple[Optional[float], Optional[float], str]:
    """(现价, 总股本[股], 交易日)。daily_basic 单票最新一行。"""
    t = _tsd()
    if not t.available() or norm.endswith(".HK"):
        return None, None, ""
    df = t._query("daily_basic", ts_code=norm,
                  fields="ts_code,trade_date,close,total_share")
    if df is None or len(df) == 0:
        return None, None, ""
    df = df.sort_values("trade_date", ascending=False)
    row = df.iloc[0]
    close = t._f(row.get("close"))
    total_share_wan = t._f(row.get("total_share"))   # 万股
    shares = total_share_wan * 1e4 if total_share_wan else None
    return close, shares, t._iso(str(row.get("trade_date") or ""))


# ══════════════════════ 公开 API ══════════════════════

def compute(norm: str, assumptions: Optional[DCFAssumptions] = None,
            asof: Optional[str] = None) -> DCFResult:
    """DCF 估值卡(F9)。assumptions 传入时为 UI 滑块重算路径:
    fcf_base 已含 → 纯计算(<50ms),不重取数据。"""
    asof = asof or datetime.now().strftime("%Y-%m-%d")
    gaps: list[str] = []

    # 行业 + 基准 WACC(路由,I10.2)
    industry = _fetch_industry_l1(norm)
    if not industry:
        gaps.append("申万一级行业未识别,回退 '综合' 参数【数据缺口】"
                    if not norm.endswith(".HK")
                    else "港股无申万映射,回退 '综合' 参数【数据缺口】")
    adj_pp, default_pg, default_g5 = industry_params(industry)
    base, base_src = _base_wacc(norm)
    wacc_source = f"{base_src} + 行业调整{adj_pp*100:+.1f}pp({industry or '综合'})"

    # 现价 / 股本
    price, shares, trade_date = _fetch_price_shares(norm)
    if price is None or not shares:
        gaps.append("现价/总股本缺失(daily_basic)【数据缺口】")

    # 假设:UI 传入优先,否则行业默认 + 路由 WACC
    if assumptions is not None:
        a = assumptions
        fcf_ttm, capex, revenue = a.fcf_base, None, None
        flow_gaps: list[str] = []
    else:
        fcf_ttm, capex, revenue, flow_gaps = _fetch_flows_ttm(norm, asof)
        a = DCFAssumptions(
            perpetual_growth_rate=default_pg,
            wacc=wacc_for(base, industry),
            five_year_growth_rate=default_g5,
            fcf_base=fcf_ttm if fcf_ttm is not None else 0.0,
        )
    gaps.extend(flow_gaps)

    # 护栏(安集教训;UI 重算路径无 capex/revenue,沿用 FCF 判据)
    guard, capex_ratio = capex_guard(fcf_ttm, capex, revenue)
    note = GUARD_CONFIDENCE_NOTE if guard else DEFAULT_CONFIDENCE_NOTE

    res = DCFResult(
        ticker=norm, assumptions=a, current_price=price,
        confidence_note=note, guard_triggered=guard,
        industry_l1=industry or "综合", wacc_source=wacc_source,
        fcf_ttm=fcf_ttm, capex_ratio=capex_ratio,
        asof=trade_date or asof, data_gaps=gaps,
        source_chain={"flows": "tushare.cashflow/income(TTM via 修①)",
                      "price_shares": "tushare.daily_basic",
                      "industry": "tushare.index_member_all",
                      "wacc_base": "markets.market_config(I10.2)"})

    if fcf_ttm is None or fcf_ttm == 0.0:
        res.data_gaps.append("基期 FCF 不可用,DCF 未计算【数据缺口】")
        return res
    if fcf_ttm < 0:
        # 负 FCF 两段式无意义,只给护栏结论不给伪内在值(I1.1)
        res.data_gaps.append("TTM FCF 为负,两段式 DCF 不适用(仅护栏结论)【数据缺口】")
        return res

    total_pv = two_stage_dcf(a.fcf_base, a.five_year_growth_rate,
                             a.perpetual_growth_rate, a.wacc)
    lo, hi = sensitivity_band(a.fcf_base, a.five_year_growth_rate,
                              a.perpetual_growth_rate, a.wacc)
    if total_pv is None:
        res.data_gaps.append(f"参数不合法(wacc={a.wacc:.3f} ≤ 永续g={a.perpetual_growth_rate:.3f})【数据缺口】")
        return res
    if shares:
        res.intrinsic_value_per_share = round(total_pv / shares, 4)
        res.intrinsic_value_low = round(lo / shares, 4) if lo else None
        res.intrinsic_value_high = round(hi / shares, 4) if hi else None
        if price and res.intrinsic_value_per_share:
            res.margin_of_safety_pct = round(
                (res.intrinsic_value_per_share - price) / res.intrinsic_value_per_share, 4)
    return res
