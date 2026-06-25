# -*- coding: utf-8 -*-
"""
tools/baostock_data.py — A 股数据层 Baostock 底座 (v2.0.0, 2026-06-22)
=====================================================================

为什么存在
----------
原 A 股数据走 akshare(东财 push2his / 同花顺财务摘要),三大病根:
  1. 东财连接层 drop akshare(疑 TLS 指纹反爬),与 IP 无关。
  2. 同花顺财报跑 py_mini_racer(V8),9 agent 并行多线程 → V8 非线程安全
     → 进程级硬崩 (partition_address_space.cc),Python try/except 接不住。
  3. stock_financial_abstract_ths 解析脆弱。
Baostock:纯 socket、无反爬、无 JS/V8、无 IP 限制,A 股价格 + 财报齐全。

⚠️ 并发安全
----------
Baostock 是**单 socket 全局会话**,多线程并发查询会串话/错乱。
本模块用一把全局 RLock 串行化所有查询,并单次登录(进程级)。
Baostock 查询是 socket 级,极快,串行化不影响整体吞吐;
配合上层 _cache_v2,同一 ticker 只查一次。

覆盖范围
--------
  - A 股 SH/SZ:价格 + 6 张财务表齐全(实测探测确认)。
  - 北交所 BJ:Baostock 部分版本支持(bj. 前缀),查不到自动空→上层兜底。
  - 港股 HK:Baostock **不支持**,本模块对 HK 一律返回空,由上层走 akshare/腾讯。
  - 次新股(如 605788):Baostock 数据更新滞后,可能查无此票(stock_basic 也空)
    → 返回空,上层用腾讯实时报价兜底,避免 current_price=0。

财报是"比率制"不是"报表制"
---------------------------
Baostock 财务表给的是比率(净利率/毛利率/周转率/杜邦…)+ 少量绝对值
(netProfit / epsTTM / totalShare)。报表绝对值(营收/总资产/权益/流动资产…)
本模块用**比率代数反推**(已用 600519 三重交叉校验自洽):
    revenue       = netProfit / npMargin
    gross_profit  = revenue × gpMargin
    operating     = revenue × dupontEbittogr        (EBIT 近似)
    total_assets  = revenue / dupontAssetTurn
    equity        = total_assets / assetToEquity
    liabilities   = total_assets × liabilityToAsset
    current_assets= total_assets × CAToAsset
    current_liab  = current_assets / currentRatio
    cash(近似)    = cashRatio × current_liab        (低置信)
拿不到:capital_expenditure / depreciation / free_cash_flow / dividends
        → None(Baostock 无现金流量表绝对科目;诚实留空,优于 V8 崩溃)。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)
__version__ = "2.0.0"

# ── 软导入 baostock(sandbox 无网时 _HAVE_BS=False,可被测试注入 fake)──
try:
    import baostock as _bs  # type: ignore
    _HAVE_BS = True
except ImportError:
    _bs = None  # type: ignore
    _HAVE_BS = False

# ── 并发控制:全局锁 + 单次登录 ───────────────────────────────────────
_BS_LOCK = threading.RLock()
_logged_in = False
_last_login_ts = 0.0
_LOGIN_COOLDOWN = 30.0   # 秒内不重复 relogin,防登录风暴(尤其 9 路并发)

# 仅这些"疑似会话/连接级"错误才值得重登;数据级错误(无数据/代码不支持)重登无用
_SESSION_ERR_HINTS = ("登录", "login", "网络", "连接", "connect",
                      "session", "超时", "timeout", "断开", "closed")


def _ensure_login() -> bool:
    """单次登录(进程级)。已登录直接 True。"""
    global _logged_in, _last_login_ts
    if not _HAVE_BS or _bs is None:
        return False
    if _logged_in:
        return True
    try:
        lg = _bs.login()
        _logged_in = (getattr(lg, "error_code", "1") == "0")
        _last_login_ts = time.time()
        if not _logged_in:
            logger.warning("baostock login 失败: %s", getattr(lg, "error_msg", "?"))
        return _logged_in
    except Exception as exc:
        logger.warning("baostock login 异常: %s", exc)
        return False


def _relogin() -> bool:
    """带冷却的重登:冷却期内不重复登录,避免登录风暴。"""
    global _logged_in, _last_login_ts
    now = time.time()
    if now - _last_login_ts < _LOGIN_COOLDOWN:
        return _logged_in
    _logged_in = False
    return _ensure_login()


def _is_session_error(rs) -> bool:
    msg = str(getattr(rs, "error_msg", "") or "")
    return any(h in msg for h in _SESSION_ERR_HINTS)


def _query(method: str, *args, **kwargs) -> Optional[dict]:
    """串行化执行一个 baostock 查询(按方法名),排空结果集 → {'fields':[...], 'rows':[[...]]}。

    传方法名字符串(而非 _bs.xxx 句柄),避免 _bs is None 时在实参求值阶段
    就 AttributeError 逃逸。错误码非 0 时重登一次重试。任何异常 fail-soft → None。
    """
    if not _HAVE_BS or _bs is None:
        return None
    fn = getattr(_bs, method, None)
    if fn is None:
        return None
    with _BS_LOCK:
        if not _ensure_login():
            return None
        try:
            rs = fn(*args, **kwargs)
        except Exception as exc:
            # 连接级异常 → 带冷却重登一次重试
            logger.warning("baostock %s 异常: %s", method, str(exc)[:120])
            if _relogin():
                try:
                    rs = fn(*args, **kwargs)
                except Exception:
                    return None
            else:
                return None
        try:
            # 只有"疑似会话错误"才重登重试;数据级错误(无数据/不支持)不重登
            if getattr(rs, "error_code", "0") != "0" and _is_session_error(rs):
                if _relogin():
                    rs = fn(*args, **kwargs)
            if getattr(rs, "error_code", "1") != "0":
                # 数据级错误/无数据 → 空结果(不是 None,调用方按"无数据"处理)
                logger.debug("baostock %s err=%s msg=%s", method,
                             getattr(rs, "error_code", "?"),
                             getattr(rs, "error_msg", "?"))
                return {"fields": list(getattr(rs, "fields", []) or []), "rows": []}
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            return {"fields": list(rs.fields), "rows": rows}
        except Exception as exc:
            logger.warning("baostock %s 排空异常: %s", method, str(exc)[:160])
            return None


# ── 工具 ───────────────────────────────────────────────────────────────

def to_bs_code(norm: str) -> Optional[str]:
    """'600519.SH' → 'sh.600519' ; '000333.SZ' → 'sz.000333' ; HK → None。"""
    try:
        code, suf = norm.split(".")
    except ValueError:
        return None
    # 北交所(BJ)实测 Baostock 不覆盖(430047/920019 均 0 行)→ 短路走上层兜底,
    # 避免每次查询触发错误码、浪费往返。若将来 Baostock 支持,把 BJ 加回映射即可。
    pref = {"SH": "sh", "SZ": "sz"}.get(suf.upper())
    if not pref:
        return None
    return f"{pref}.{code}"


def _f(v, default=None) -> Optional[float]:
    """Baostock 字段(字符串)→ float;空串/None → default。"""
    if v is None:
        return default
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _row_dict(res: Optional[dict]) -> dict:
    """单行结果 → {field: value};空 → {}。"""
    if not res or not res.get("rows"):
        return {}
    return dict(zip(res["fields"], res["rows"][0]))


def _quarters_back(end_date: str, n: int) -> list[tuple[int, int]]:
    """从 end_date 所在季度起,往前列 n 个 (year, quarter)。"""
    try:
        dt = datetime.strptime(end_date[:10], "%Y-%m-%d")
    except Exception:
        dt = datetime.now()
    y, q = dt.year, (dt.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return out


# ── 价格 ───────────────────────────────────────────────────────────────

_PRICE_FIELDS = ("date,open,high,low,close,preclose,volume,amount,turn,"
                 "pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM")


def get_daily_rows(norm: str, start_date: str, end_date: str,
                   adjustflag: str = "2") -> list[dict]:
    """A 股日线 → list[dict](含估值字段)。adjustflag '2'=前复权。

    查无此票(次新股未覆盖 / 停牌全程)→ []，由上层兜底。
    """
    bscode = to_bs_code(norm)
    if not bscode:
        return []
    res = _query("query_history_k_data_plus", bscode, _PRICE_FIELDS,
                 start_date=start_date[:10], end_date=end_date[:10],
                 frequency="d", adjustflag=adjustflag)
    if not res or not res.get("rows"):
        return []
    return [dict(zip(res["fields"], r)) for r in res["rows"]]


def get_prices_dicts(norm: str, start_date: str, end_date: str) -> list[dict]:
    """→ list[{time,open,close,high,low,volume}](喂 Price 构造)。"""
    out = []
    for d in get_daily_rows(norm, start_date, end_date):
        out.append({
            "time": d.get("date", ""),
            "open": _f(d.get("open"), 0.0),
            "close": _f(d.get("close"), 0.0),
            "high": _f(d.get("high"), 0.0),
            "low": _f(d.get("low"), 0.0),
            "volume": int(_f(d.get("volume"), 0) or 0),
        })
    return out


def latest_valuation(norm: str, asof: str) -> dict:
    """asof 当日或之前最近一个交易日的估值 → {pe,pb,ps,close,date}。"""
    try:
        end = datetime.strptime(asof[:10], "%Y-%m-%d")
    except Exception:
        end = datetime.now()
    start = (end - timedelta(days=20)).strftime("%Y-%m-%d")
    rows = get_daily_rows(norm, start, end.strftime("%Y-%m-%d"))
    for d in reversed(rows):  # 最近一行优先
        pe = _f(d.get("peTTM"))
        if pe is not None or d.get("close"):
            return {
                "pe": pe,
                "pb": _f(d.get("pbMRQ")),
                "ps": _f(d.get("psTTM")),
                "close": _f(d.get("close")),
                "date": d.get("date", ""),
            }
    return {}


# ── 财务(6 表)───────────────────────────────────────────────────────

def _fetch_quarter(bscode: str, year: int, quarter: int) -> dict:
    """取一个季度的 6 张表,合成 {table: {field: value}}。profit 空 → {}。"""
    profit = _row_dict(_query("query_profit_data", code=bscode,
                              year=year, quarter=quarter))
    if not profit:
        return {}
    return {
        "profit": profit,
        "balance": _row_dict(_query("query_balance_data", code=bscode,
                                    year=year, quarter=quarter)),
        "cash": _row_dict(_query("query_cash_flow_data", code=bscode,
                                 year=year, quarter=quarter)),
        "growth": _row_dict(_query("query_growth_data", code=bscode,
                                   year=year, quarter=quarter)),
        "operation": _row_dict(_query("query_operation_data", code=bscode,
                                      year=year, quarter=quarter)),
        "dupont": _row_dict(_query("query_dupont_data", code=bscode,
                                   year=year, quarter=quarter)),
    }


def get_quarters(norm: str, end_date: str, limit: int = 8,
                 max_lookback: int = 16) -> list[dict]:
    """返回最多 limit 个季度的合成数据(按报告期降序),已按 pubDate<=end_date 过滤。

    每个元素: {"statDate","pubDate","profit","balance","cash","growth",
               "operation","dupont"}。
    """
    bscode = to_bs_code(norm)
    if not bscode:
        return []
    want = min(max(int(limit), 1), 12)
    out: list[dict] = []
    for (y, q) in _quarters_back(end_date, max_lookback):
        if len(out) >= want:
            break
        block = _fetch_quarter(bscode, y, q)
        if not block:
            continue
        prof = block["profit"]
        stat = prof.get("statDate", "")
        pub = prof.get("pubDate", "")
        if end_date and pub and pub > end_date[:10]:
            continue  # 防前视:未公布的季度跳过
        block["statDate"] = stat
        block["pubDate"] = pub
        out.append(block)
    return out


# ── 比率代数反推:6 表 → FinancialMetrics 字段 ──────────────────────────

def metrics_from_block(block: dict) -> dict:
    """一个季度块 → FinancialMetrics 字段 dict(不含 pe/pb/ps/market_cap,
    那些由 latest_valuation / market_cap 在上层附加到最新一期)。"""
    p = block.get("profit", {})
    b = block.get("balance", {})
    c = block.get("cash", {})
    g = block.get("growth", {})
    o = block.get("operation", {})
    du = block.get("dupont", {})

    npm = _f(p.get("npMargin"))
    liab_to_asset = _f(b.get("liabilityToAsset"))
    dupont_roe = _f(du.get("dupontROE"))
    dupont_a2e = _f(du.get("dupontAssetStoEquity"))
    nr_days = _f(o.get("NRTurnDays"))
    inv_days = _f(o.get("INVTurnDays"))

    # ROE 改 TTM 口径:单季 roeAvg 会被 buffett 的 15% 年度阈值误判为"weak"。
    # 复用 line_items 的权益反推(单一真相源,不重复),roe_ttm = TTM净利 / 权益。
    _li = line_items_from_block(block)
    _roe_ttm = ((_li["net_income"] / _li["shareholders_equity"])
                if (_li.get("net_income") and _li.get("shareholders_equity"))
                else _f(p.get("roeAvg")))

    rec = {
        "gross_margin": _f(p.get("gpMargin")),
        "net_margin": npm,
        "operating_margin": _f(du.get("dupontEbittogr")),       # EBIT/GR 近似
        "return_on_equity": _roe_ttm,                           # TTM 口径(原单季 roeAvg 会误判)
        "return_on_assets": (dupont_roe / dupont_a2e
                             if dupont_roe is not None and dupont_a2e else None),
        "asset_turnover": _f(o.get("AssetTurnRatio")),
        "inventory_turnover": _f(o.get("INVTurnRatio")),
        "receivables_turnover": _f(o.get("NRTurnRatio")),
        "days_sales_outstanding": nr_days,
        "operating_cycle": ((nr_days + inv_days)
                            if nr_days is not None and inv_days is not None else None),
        "current_ratio": _f(b.get("currentRatio")),
        "quick_ratio": _f(b.get("quickRatio")),
        "cash_ratio": _f(b.get("cashRatio")),
        "debt_to_assets": liab_to_asset,
        "debt_to_equity": (liab_to_asset / (1 - liab_to_asset)
                           if liab_to_asset is not None and liab_to_asset < 1 else None),
        "interest_coverage": _f(c.get("ebitToInterest")),
        "earnings_growth": _f(g.get("YOYPNI")) or _f(g.get("YOYNI")),
        "book_value_growth": _f(g.get("YOYEquity")),
        "earnings_per_share_growth": _f(g.get("YOYEPSBasic")),
        "earnings_per_share": _f(p.get("epsTTM")),
        # Baostock 无 → 显式 None(模型字段需存在)
        "revenue_growth": None,
        "free_cash_flow_growth": None,
        "operating_income_growth": None,
        "ebitda_growth": None,
        "return_on_invested_capital": None,
        "working_capital_turnover": None,
        "operating_cash_flow_ratio": None,
        "payout_ratio": None,
        "enterprise_value": None,
        "enterprise_value_to_ebitda_ratio": None,
        "enterprise_value_to_revenue_ratio": None,
        "free_cash_flow_yield": None,
        "free_cash_flow_per_share": None,
    }
    return rec


# ── 比率代数反推:6 表 → LineItem 字段(绝对值,单位元)──────────────────

def line_items_from_block(block: dict) -> dict:
    """一个季度块 → LineItem 字段 dict(绝对值)。拿不到的留 None。"""
    p = block.get("profit", {})
    b = block.get("balance", {})
    c = block.get("cash", {})
    du = block.get("dupont", {})

    net_income_q = _f(p.get("netProfit"))   # 单季累计:仅用于平衡表链反推(已离线校验,勿动)
    npm = _f(p.get("npMargin"))
    gpm = _f(p.get("gpMargin"))
    total_share = _f(p.get("totalShare"))
    eps_ttm = _f(p.get("epsTTM"))
    ebit_to_gr = _f(du.get("dupontEbittogr"))
    asset_turn = _f(du.get("dupontAssetTurn"))
    a2e = _f(b.get("assetToEquity"))
    liab_to_asset = _f(b.get("liabilityToAsset"))
    ca_to_asset = _f(c.get("CAToAsset"))
    current_ratio = _f(b.get("currentRatio"))
    cash_ratio = _f(b.get("cashRatio"))

    # ── 平衡表链(资产/权益/流动科目):用单季营收反推,口径自洽,已离线校验,勿动 ──
    revenue_q = (net_income_q / npm) if (net_income_q is not None and npm) else None
    total_assets = (revenue_q / asset_turn) if (revenue_q is not None and asset_turn) else None
    shareholders_equity = (total_assets / a2e) if (total_assets is not None and a2e) else None
    total_liabilities = (total_assets * liab_to_asset) if (total_assets is not None and liab_to_asset is not None) else None
    current_assets = (total_assets * ca_to_asset) if (total_assets is not None and ca_to_asset is not None) else None
    current_liabilities = (current_assets / current_ratio) if (current_assets is not None and current_ratio) else None
    cash_and_equiv = (cash_ratio * current_liabilities) if (cash_ratio is not None and current_liabilities is not None) else None

    # ── 流量科目(净利/营收/毛利/营业利润):输出 TTM,而非单季累计 ──
    # buffett/valuation 等 agent 按年度/TTM 口径消费流量科目;单季累计会让增长率
    # (如把 Q1 单季当全年去比 FY)、residual income 内在价值等口径全错。
    # baostock 现成提供 epsTTM → TTM 净利 = epsTTM × totalShare。
    net_income = (eps_ttm * total_share) if (eps_ttm is not None and total_share is not None) else None
    revenue = (net_income / npm) if (net_income is not None and npm) else None
    gross_profit = (revenue * gpm) if (revenue is not None and gpm is not None) else None
    operating_income = (revenue * ebit_to_gr) if (revenue is not None and ebit_to_gr is not None) else None

    return {
        "revenue": revenue,
        "net_income": net_income,
        "operating_income": operating_income,
        "gross_profit": gross_profit,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        # working_capital = 流动资产 − 流动负债(数据已在手,补 emit;valuation 裸访问需要它)
        "working_capital": (
            (current_assets - current_liabilities)
            if (current_assets is not None and current_liabilities is not None) else None
        ),
        "shareholders_equity": shareholders_equity,
        "cash_and_equivalents": cash_and_equiv,           # 低置信
        "outstanding_shares": total_share,
        # Baostock 无现金流量表绝对科目 → None(诚实留空)
        "free_cash_flow": None,
        "capital_expenditure": None,
        "depreciation_and_amortization": None,
        "operating_expense": None,
        "research_and_development": None,
        "goodwill_and_intangible_assets": None,
        "dividends_and_other_cash_distributions": None,
    }


def market_cap(norm: str, asof: str) -> Optional[float]:
    """市值 = totalShare × asof 最近收盘价。"""
    val = latest_valuation(norm, asof)
    close = val.get("close")
    qs = get_quarters(norm, asof, limit=1)
    if not qs or close is None:
        return None
    ts = _f(qs[0].get("profit", {}).get("totalShare"))
    if ts is None:
        return None
    return ts * close


def available() -> bool:
    return _HAVE_BS
