"""
snapshot.py — 个股深度页快照编排(Step 16 / TECH §7.3)

10 维并行采集(asyncio.gather + to_thread),单维失败降级【数据缺口】,
≥50% 失败抛 MajorPageDataFailure 暂停整页(I6.2)。

设计要点:
  - 每维独立超时(AIHF_SNAPSHOT_DIM_TIMEOUT,默认 90s),超时计失败不挂页。
  - agents / fraud-LLM 默认关闭(skipped 不计入失败分母)——页面加载不隐式烧
    LLM 分钟级调用;agents 走既有 POST /api/analyze,或 snapshot?agents=1 显式开。
    【设计裁决,待 Alex 认可入 v1.2 增量】
  - footer 元信息(I8.3):captured_at + 每维 status/elapsed/gaps + 汇总 data_gaps。
  - 模块自身零取数逻辑:全部委托既有模块(单一真相源,不建并行链路)。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

__version__ = "1.0.0"

logger = logging.getLogger(__name__)

DIM_TIMEOUT = float(os.getenv("AIHF_SNAPSHOT_DIM_TIMEOUT", "90"))
AGENTS_TIMEOUT = float(os.getenv("AIHF_SNAPSHOT_AGENTS_TIMEOUT", "600"))
FAIL_PAUSE_RATIO = 0.5          # I6.2
KLINE_DAYS = 250                # 规格:近 250 交易日
CAPFLOW_DAYS = 20               # F13:20 日 sparkline

DIM_NAMES = ["kline", "valuation", "peers", "industry_index", "capital",
             "unlock", "news", "agents", "dcf", "fraud"]


class MajorPageDataFailure(Exception):
    """≥50% 维度失败 → 暂停整页(I6.2)。"""

    def __init__(self, failed: list[str], attempted: int, detail: dict):
        self.failed, self.attempted, self.detail = failed, attempted, detail
        super().__init__(
            f"深度页数据失败 {len(failed)}/{attempted} ≥ 50%: {', '.join(failed)}")


# ──────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────

def _dump(obj) -> Any:
    """pydantic → dict;list/基础类型透传。"""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_dump(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _dump(v) for k, v in obj.items()}
    return obj


def _getattr_any(obj, names: tuple[str, ...]):
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _norm(ticker: str) -> tuple[str, str]:
    from src.markets.ticker import parse_ticker
    info = parse_ticker(ticker)
    market = "HK" if info.full_ticker.upper().endswith(".HK") else "CN"
    return info.full_ticker, market


# ──────────────────────────────────────────────────────────────
# 10 个维度 fetcher(全部同步函数;编排层 to_thread 并行)
# 约定:返回 dict(可含 data_gaps: list[str]);抛异常 = 维度失败
# ──────────────────────────────────────────────────────────────

def _fetch_kline(norm: str, market: str, asof: str) -> dict:
    from src.tools.api_china import get_prices
    end = datetime.strptime(asof, "%Y-%m-%d")
    start = end - timedelta(days=int(KLINE_DAYS * 1.7) + 30)
    prices = get_prices(norm, start.strftime("%Y-%m-%d"), asof)
    if not prices:
        return {"rows": [], "data_gaps": ["K线数据缺失(全链无返回)【数据缺口】"]}
    closes = [float(p.close) for p in prices]

    def ma(n, i):
        return round(sum(closes[i + 1 - n:i + 1]) / n, 3) if i + 1 >= n else None

    rows = [{"t": p.time, "o": float(p.open), "h": float(p.high),
             "l": float(p.low), "c": float(p.close), "v": int(p.volume),
             "ma5": ma(5, i), "ma20": ma(20, i), "ma60": ma(60, i)}
            for i, p in enumerate(prices)][-KLINE_DAYS:]
    return {"rows": rows, "count": len(rows)}


def _fetch_valuation(norm: str, market: str, asof: str) -> dict:
    """顶部 strip + 核心指标 8 卡(F7)。缺项 None,禁 0 冒充(I1.1)。"""
    from src.tools import api_china
    gaps: list[str] = []
    strip: dict[str, Any] = {"ticker": norm}
    try:
        q = api_china.quote(norm)
        strip.update(name=getattr(q, "name", None),
                     price=_getattr_any(q, ("price", "current", "close")),
                     pct_chg=_getattr_any(q, ("pct_change", "pct_chg", "change_pct")),
                     market_cap=getattr(q, "market_cap", None),
                     quote_source=getattr(q, "source", None))
    except Exception as exc:
        gaps.append(f"实时报价失败: {str(exc)[:60]}【数据缺口】")

    cards: dict[str, Optional[float]] = {k: None for k in (
        "pe_ttm", "pb", "roe", "dividend_yield", "revenue_yoy",
        "net_profit_yoy", "debt_ratio", "institutional_holding")}
    try:
        ms = api_china.get_financial_metrics(norm, end_date=asof, limit=1)
        if ms:
            m = ms[0]
            cards["pe_ttm"] = _getattr_any(m, ("price_to_earnings_ratio", "pe_ttm", "pe"))
            cards["pb"] = _getattr_any(m, ("price_to_book_ratio", "pb"))
            cards["roe"] = _getattr_any(m, ("return_on_equity",))
            cards["dividend_yield"] = _getattr_any(m, ("dividend_yield", "payout_yield"))
            cards["revenue_yoy"] = _getattr_any(m, ("revenue_growth",))
            cards["net_profit_yoy"] = _getattr_any(m, ("earnings_growth", "net_income_growth"))
            cards["debt_ratio"] = _getattr_any(m, ("debt_to_assets", "debt_ratio", "debt_to_equity"))
            if strip.get("market_cap") is None:
                strip["market_cap"] = getattr(m, "market_cap", None)
        else:
            gaps.append("财务指标缺失(get_financial_metrics 空)【数据缺口】")
    except Exception as exc:
        gaps.append(f"财务指标失败: {str(exc)[:60]}【数据缺口】")

    for k, v in cards.items():
        if v is None:
            gaps.append(f"{k}【数据缺口】")
    # 5 年百分位 / 机构持仓 v1 无稳定源:诚实缺口,不硬凑
    gaps.append("pe_5y_percentile 无稳定源(v1 已知缺口)")
    return {"strip": strip, "cards": cards, "data_gaps": gaps}


def _fetch_peers(norm: str, market: str, asof: str) -> dict:
    from src.analysis import peer_compare
    return _dump(peer_compare.fetch_peers(norm))


def _fetch_industry_index(norm: str, market: str, asof: str) -> dict:
    from src.analysis import peer_compare
    return _dump(peer_compare.industry_index(norm, days=120))


def _fetch_capital(norm: str, market: str, asof: str) -> dict:
    """F13:北向 / 融资 / 主力 20 日 sparkline。三路各自 fail-soft。"""
    if market == "HK":
        return {"data_gaps": ["港股资金面 v1 不覆盖(北向/两融为 A 股口径)【数据缺口】"]}
    from src.tools import api_china
    out: dict[str, Any] = {"data_gaps": []}
    for key, fn in (("northbound", lambda: api_china.get_northbound_flow(limit=CAPFLOW_DAYS)),
                    ("margin", lambda: api_china.get_margin_trading(limit=CAPFLOW_DAYS)),
                    ("main_flow", lambda: api_china.get_main_capital_flow(norm, limit=CAPFLOW_DAYS))):
        try:
            rows = fn() or []
            out[key] = _dump(rows)
            if not rows:
                out["data_gaps"].append(f"{key} 空返回【数据缺口】")
        except Exception as exc:
            out[key] = []
            out["data_gaps"].append(f"{key} 失败: {str(exc)[:60]}【数据缺口】")
    return out


def _fetch_unlock(norm: str, market: str, asof: str) -> dict:
    from src.analysis import unlock_radar
    return _dump(unlock_radar.fetch(norm, asof=asof))


def _fetch_news(norm: str, market: str, asof: str) -> dict:
    """F15:A 股自动拉;港股 get_company_news 内置本地 openclaw 存档路径。"""
    from src.tools.api_china import get_company_news
    start = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    items = get_company_news(norm, end_date=asof, start_date=start, limit=50) or []
    out = {"items": _dump(items), "count": len(items), "data_gaps": []}
    if not items:
        out["data_gaps"].append(
            "无 openclaw 数据,建议导入(I1.3)" if market == "HK"
            else "公告/新闻空返回【数据缺口】")
    return out


def _fetch_dcf(norm: str, market: str, asof: str) -> dict:
    from src.analysis import dcf
    return _dump(dcf.compute(norm, asof=asof))


def _fetch_fraud(norm: str, market: str, asof: str, with_llm: bool = False) -> dict:
    from src.analysis import fraud_detector
    return _dump(fraud_detector.check(norm, asof=asof, with_llm=with_llm))


def _fetch_agents(norm: str, market: str, asof: str) -> dict:
    """9-agent 决议(显式开启时)。复用 /api/analyze 同一路径,零并行逻辑。"""
    from src.main_china import run_china_hedge_fund
    from src.web_app import default_model
    start = (datetime.strptime(asof, "%Y-%m-%d") - timedelta(days=215)).strftime("%Y-%m-%d")
    model_name, model_provider = default_model()
    portfolio = {
        "cash": 1000000.0, "margin_requirement": 0.5, "margin_used": 0.0,
        "positions": {norm: {"long": 0, "short": 0, "long_cost_basis": 0.0,
                             "short_cost_basis": 0.0, "short_margin_used": 0.0}},
        "realized_gains": {norm: {"long": 0.0, "short": 0.0}},
    }
    result = run_china_hedge_fund(
        tickers=[norm], start_date=start, end_date=asof, portfolio=portfolio,
        show_reasoning=False, selected_analysts=None,
        model_name=model_name, model_provider=model_provider)
    return _dump(result) if isinstance(result, dict) else {"raw": str(result)[:2000]}


# ──────────────────────────────────────────────────────────────
# 编排
# ──────────────────────────────────────────────────────────────

async def _run_dim(name: str, fn: Callable[[], dict], timeout: float) -> dict:
    t0 = time.monotonic()
    try:
        data = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        return {"name": name, "status": "ok", "data": data,
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    except asyncio.TimeoutError:
        logger.warning("snapshot dim %s 超时(%ss)", name, timeout)
        return {"name": name, "status": "failed",
                "error": f"超时 {timeout:.0f}s",
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        logger.warning("snapshot dim %s 失败: %s", name, exc)
        return {"name": name, "status": "failed", "error": str(exc)[:200],
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}


async def build_stock_snapshot(ticker: str, *, include_agents: bool = False,
                               with_llm: bool = False,
                               asof: Optional[str] = None,
                               _fetchers: Optional[dict[str, Callable]] = None) -> dict:
    """深度页快照(TECH §7.3)。_fetchers 仅供沙箱确定性测试注入。

    Raises:
        MajorPageDataFailure: 尝试维度中 ≥50% 失败(I6.2)。
    """
    norm, market = _norm(ticker)
    asof = asof or datetime.now().strftime("%Y-%m-%d")

    default_fetchers: dict[str, Callable[[], dict]] = {
        "kline": lambda: _fetch_kline(norm, market, asof),
        "valuation": lambda: _fetch_valuation(norm, market, asof),
        "peers": lambda: _fetch_peers(norm, market, asof),
        "industry_index": lambda: _fetch_industry_index(norm, market, asof),
        "capital": lambda: _fetch_capital(norm, market, asof),
        "unlock": lambda: _fetch_unlock(norm, market, asof),
        "news": lambda: _fetch_news(norm, market, asof),
        "agents": lambda: _fetch_agents(norm, market, asof),
        "dcf": lambda: _fetch_dcf(norm, market, asof),
        "fraud": lambda: _fetch_fraud(norm, market, asof, with_llm=with_llm),
    }
    fetchers = {**default_fetchers, **(_fetchers or {})}

    skipped: dict[str, str] = {}
    if not include_agents:
        skipped["agents"] = "默认跳过(LLM 分钟级+成本);snapshot?agents=1 或 POST /api/analyze 显式运行"

    tasks = [
        _run_dim(n, fetchers[n], AGENTS_TIMEOUT if n == "agents" else DIM_TIMEOUT)
        for n in DIM_NAMES if n not in skipped
    ]
    results = await asyncio.gather(*tasks)
    by_name = {r["name"]: r for r in results}

    attempted = len(results)
    failed = [r["name"] for r in results if r["status"] == "failed"]
    if attempted and len(failed) / attempted >= FAIL_PAUSE_RATIO:
        raise MajorPageDataFailure(failed, attempted, {
            n: by_name[n].get("error") for n in failed})

    # 组装 + 汇总缺口(I8.3 footer)
    dims: dict[str, Any] = {}
    all_gaps: list[str] = []
    dim_meta: dict[str, dict] = {}
    for n in DIM_NAMES:
        if n in skipped:
            dims[n] = None
            dim_meta[n] = {"status": "skipped", "note": skipped[n]}
            continue
        r = by_name[n]
        dim_meta[n] = {"status": r["status"], "elapsed_ms": r["elapsed_ms"]}
        if r["status"] == "failed":
            dims[n] = None
            dim_meta[n]["error"] = r["error"]
            all_gaps.append(f"{n} 维度失败: {r['error']}【数据缺口】")
        else:
            dims[n] = r["data"]
            gaps = (r["data"] or {}).get("data_gaps") or []
            all_gaps.extend(f"[{n}] {g}" for g in gaps)

    # 跨维回填:同业中位 PE → 估值卡(F7 的 industry_median 要求)
    try:
        med = ((dims.get("peers") or {}).get("industry_median") or {}).get("pe_ttm")
        if dims.get("valuation") is not None:
            dims["valuation"]["cards"]["industry_median_pe"] = med
    except Exception:
        pass

    return {
        "ticker": norm, "market": market, "asof": asof,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "dimensions": dims,
        "footer": {                       # I8.2/I8.3
            "data_gaps": all_gaps,
            "dim_meta": dim_meta,
            "failed_dims": failed,
            "snapshot_version": __version__,
        },
    }


def build_stock_snapshot_sync(ticker: str, **kw) -> dict:
    """同步壳(TestClient / 脚本用)。"""
    return asyncio.run(build_stock_snapshot(ticker, **kw))
