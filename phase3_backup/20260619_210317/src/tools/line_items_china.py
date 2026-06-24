"""
v3.4 中国版 line items 适配器 — 补齐 buffett/taleb 等原版价值类 agent 的数据腿。

背景：api_bridge 此前对 CN/HK 的 search_line_items 一律返回空列表，导致
warren_buffett 的五项核心子分析（盈利一致性/定价权/账面价值增长/管理层质量/
内在价值）与 nassim_taleb 的脆弱性分析全部退化为"数据不足"→ 低置信中性。
这是 00148.HK 分析全员中性的两大根因之一（另一根因是港股价格链）。

数据源：
  A股: ak.stock_financial_report_sina（新浪三大报表，对海外/代理环境友好）
  港股: ak.stock_financial_hk_report_em（东财港股报表，长表格式）

字段映射采用多候选中文科目名（精确匹配优先，子串匹配兜底），符号约定对齐
原版 financialdatasets.ai：现金流出（capex/股息）为负数。无法映射的字段置
None —— 原版 agent 对 None 字段有完备容错（逐项 if 判断），有多少数据用多少。

近似说明：period="ttm" 请求按年度报表近似（A股/港股无现成 TTM line items），
report_period 取报表日期。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# 原版字段 → 中文科目候选名（顺序即优先级；前缀 "~" 表示允许子串匹配）
FIELD_CANDIDATES: dict[str, list[str]] = {
    "net_income": [
        "归属于母公司所有者的净利润", "归属于母公司股东的净利润",
        "股东应占溢利", "本公司拥有人应占溢利", "净利润",
    ],
    "revenue": [
        "营业总收入", "营业收入", "营业额", "营运收入", "~收益总额", "收益",
    ],
    "gross_profit": ["毛利", "毛利润"],
    "cost_of_revenue": [
        "营业成本", "销售成本", "已售货品及服务成本", "已售货品成本", "销售及服务成本",
    ],
    "total_assets": ["资产总计", "总资产", "资产合计"],
    "total_liabilities": ["负债合计", "总负债", "负债总额"],
    "shareholders_equity": [
        "所有者权益(或股东权益)合计", "归属于母公司股东权益合计",
        "股东权益合计", "股东权益", "~权益总额",
    ],
    "current_assets": ["流动资产合计", "流动资产总额", "~流动资产"],
    "current_liabilities": ["流动负债合计", "流动负债总额", "~流动负债"],
    "outstanding_shares": ["实收资本(或股本)", "股本", "~已发行股本"],
    "operating_cash_flow": [
        "经营活动产生的现金流量净额", "经营业务所得现金净额", "~经营活动",
    ],
    "capital_expenditure": [
        "购建固定资产、无形资产和其他长期资产支付的现金",
        "购建固定资产、无形资产和其他长期资产所支付的现金",
        "~购建固定资产", "~购买物业", "~添置固定资产",
    ],
    "depreciation_and_amortization": [
        "固定资产折旧、油气资产折耗、生产性生物资产折旧",
        "~折旧", "折旧及摊销",
    ],
    "dividends_and_other_cash_distributions": [
        "分配股利、利润或偿付利息支付的现金", "~分配股利", "已派股息", "~派付股息",
    ],
}

# 现金流出类字段：源数据为正数（支付额），按原版约定转为负
NEGATE_FIELDS = {"capital_expenditure", "dividends_and_other_cash_distributions"}

_REPORT_TYPES_CN = ["资产负债表", "利润表", "现金流量表"]
_REPORT_TYPES_HK = ["资产负债表", "利润表", "现金流量表"]


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).replace(",", "").strip()
        if s in ("", "--", "-", "nan", "None"):
            return None
        m = 1.0
        if s.endswith("万"):
            m, s = 1e4, s[:-1]
        elif s.endswith("亿"):
            m, s = 1e8, s[:-1]
        return float(s) * m
    except (ValueError, TypeError):
        return None


def _match_field(field: str, item_names: dict[str, float]) -> float | None:
    """按候选名匹配科目。精确优先；'~' 前缀候选做子串匹配。"""
    for cand in FIELD_CANDIDATES.get(field, []):
        if cand.startswith("~"):
            sub = cand[1:]
            for name, val in item_names.items():
                if sub in name and val is not None:
                    return val
        else:
            if cand in item_names and item_names[cand] is not None:
                return item_names[cand]
            # 容忍科目名里的全角/空白差异
            for name, val in item_names.items():
                if name.replace(" ", "") == cand.replace(" ", "") and val is not None:
                    return val
    return None


def _norm_date(v) -> str:
    s = re.sub(r"[^0-9]", "", str(v))[:8]
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(v)[:10]


def _collect_cn(ak, code: str) -> dict[str, dict[str, float]]:
    """A股：新浪三大报表 → {report_date: {科目名: 值}}。单表失败不影响其余。"""
    prefix = "sh" if code[0] in ("6", "9", "5") else "sz"
    by_date: dict[str, dict[str, float]] = {}
    for rpt in _REPORT_TYPES_CN:
        try:
            df = ak.stock_financial_report_sina(stock=f"{prefix}{code}", symbol=rpt)
            if df is None or df.empty:
                continue
            date_col = next((c for c in df.columns if "报告" in str(c) or str(c).lower() in ("date", "报表日期")), df.columns[0])
            for _, row in df.iterrows():
                d = _norm_date(row[date_col])
                bucket = by_date.setdefault(d, {})
                for col in df.columns:
                    if col == date_col:
                        continue
                    val = _to_float(row[col])
                    if val is not None:
                        bucket.setdefault(str(col).strip(), val)
        except Exception as e:
            logger.debug("CN report %s failed for %s: %s", rpt, code, e)
    return by_date


def _collect_hk(ak, code: str) -> dict[str, dict[str, float]]:
    """港股：东财报表（长表 STD_ITEM_NAME/AMOUNT/REPORT_DATE）→ 同上结构。"""
    by_date: dict[str, dict[str, float]] = {}
    for rpt in _REPORT_TYPES_HK:
        df = None
        for kwargs in ({"stock": code, "symbol": rpt, "indicator": "年度"},
                       {"stock": code, "symbol": rpt}):
            try:
                df = ak.stock_financial_hk_report_em(**kwargs)
                if df is not None and not df.empty:
                    break
            except Exception as e:
                logger.debug("HK report %s %s failed: %s", rpt, kwargs, e)
                df = None
        if df is None or df.empty:
            continue
        cols = {str(c).upper(): c for c in df.columns}
        name_col = cols.get("STD_ITEM_NAME") or cols.get("ITEM_NAME")
        amt_col = cols.get("AMOUNT") or cols.get("STD_ITEM_VALUE")
        date_col = cols.get("REPORT_DATE") or cols.get("DATE")
        if not (name_col and amt_col and date_col):
            logger.debug("HK report %s: 列结构不识别 %s", rpt, list(df.columns)[:8])
            continue
        for _, row in df.iterrows():
            d = _norm_date(row[date_col])
            val = _to_float(row[amt_col])
            if val is None:
                continue
            by_date.setdefault(d, {}).setdefault(str(row[name_col]).strip(), val)
    return by_date


def search_line_items_china(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    **_ignored,
) -> list:
    """中国版 search_line_items。返回原版 LineItem 列表（最近期在前）。"""
    from src.data.models import LineItem
    from src.markets.ticker import parse_ticker
    from src.tools.api_china import _ensure_akshare

    ak = _ensure_akshare()
    info = parse_ticker(ticker)

    if info.market.is_hk:
        by_date = _collect_hk(ak, info.code)
        currency = "HKD"
    else:
        by_date = _collect_cn(ak, info.code)
        currency = "CNY"

    if not by_date:
        logger.warning("line items: %s 无可用报表数据", ticker)
        return []

    # v3.4.1: 仅取年报（12-31）。新浪/东财报表为年初至今累计口径，
    # 季报与年报混排会让 buffett 的盈利一致性/增长趋势分析失真
    # （实测案例: 600519 的 Q1 272亿 与年报 823亿 被当成同口径期间对比）。
    # 年报不足 2 期时回退全部期间（聊胜于无，agent 自有容错）。
    all_dates = sorted((d for d in by_date if d <= end_date), reverse=True)
    annual = [d for d in all_dates if d.endswith("12-31")]
    dates = (annual if len(annual) >= 2 else all_dates)[:limit]
    results = []
    for d in dates:
        items = by_date[d]
        payload: dict = {
            "ticker": ticker, "report_period": d,
            "period": period, "currency": currency,
        }
        hit = 0
        for field in line_items:
            val = _match_field(field, items)
            if val is not None:
                if field in NEGATE_FIELDS:
                    val = -abs(val)
                payload[field] = val
                hit += 1
            else:
                payload[field] = None
        # 派生字段：毛利 = 营业收入 - 营业成本（新浪/东财报表常无"毛利"行）
        if "gross_profit" in line_items and payload.get("gross_profit") is None:
            rev = payload.get("revenue")
            if rev is None:
                rev = _match_field("revenue", items)
            cost = _match_field("cost_of_revenue", items)
            if rev is not None and cost is not None and rev > cost > 0:
                payload["gross_profit"] = rev - cost
                hit += 1

        # 派生字段：毛利率（buffett 定价权分析读取的是 gross_margin 而非 gross_profit）
        gp = payload.get("gross_profit")
        rev2 = payload.get("revenue") or _match_field("revenue", items)
        if gp is not None and rev2:
            payload["gross_margin"] = gp / rev2

        # 派生字段：自由现金流 = 经营现金流 - |capex|
        if "free_cash_flow" in line_items and payload.get("free_cash_flow") is None:
            ocf = _match_field("operating_cash_flow", items)
            capex = payload.get("capital_expenditure")
            if capex is None:
                c = _match_field("capital_expenditure", items)
                capex = -abs(c) if c is not None else None
            if ocf is not None and capex is not None:
                payload["free_cash_flow"] = ocf + capex  # capex 已为负
                hit += 1
        if hit:
            results.append(LineItem(**payload))

    logger.info("line items: %s 命中 %d 个报告期（请求 %d 字段）",
                ticker, len(results), len(line_items))
    return results


if __name__ == "__main__":
    # 自测: poetry run python src/tools/line_items_china.py
    from src.tools import proxy_guard  # noqa: F401
    logging.basicConfig(level=logging.INFO)
    fields = ["net_income", "revenue", "total_assets", "total_liabilities",
              "shareholders_equity", "capital_expenditure",
              "depreciation_and_amortization",
              "dividends_and_other_cash_distributions", "free_cash_flow"]
    for tk in ("600519", "00148.HK"):
        print(f"── {tk} ──")
        for it in search_line_items_china(tk, fields, "2026-06-12", limit=3):
            d = it.model_dump()
            print(" ", d.get("report_period"),
                  {k: v for k, v in d.items()
                   if k in fields and v is not None})
