"""
API Bridge — 让原版 Agent 透明地使用中国数据源。

原版 agents (warren_buffett, technicals, fundamentals, valuation, ...)
在代码中直接 `from src.tools.api import get_prices, ...`，绑定的是
financialdatasets.ai（仅美股）。本模块在 agent 导入之前对
src.tools.api 的函数做路由替换：

    - CN/HK ticker → src.tools.api_china (AKShare)
    - US ticker    → 原函数 (financialdatasets.ai)
    - 无中国等价物的接口 (insider_trades, line_items) → CN/HK 返回空列表，
      避免 404 噪音

v3.3 修复：路由到中国数据源时，按目标函数签名过滤 kwargs。\nv3.4 修复：search_line_items 路由到 line_items_china（详见该模块文档）。
原版 agent 会传 api_key 等中国侧函数不认识的参数 —— 此前只有 get_prices
被手工处理（坑#9），get_financial_metrics 等其余函数漏了，导致
"got an unexpected keyword argument 'api_key'" 让个股分析整体 500。
现在统一用 inspect 过滤，对所有路由函数生效，未知参数静默剥离并记 debug 日志。

⚠️ install() 必须在导入任何 agent 模块之前调用。
   main_china.py 和 web_app.py 的第一行 import 即是。
"""

from __future__ import annotations

import inspect
import logging

logger = logging.getLogger(__name__)

_installed = False


def _signature_info(fn) -> tuple[set[str], bool]:
    """返回 (可接受的参数名集合, 是否有 **kwargs)。签名不可解析时视为全收。"""
    try:
        params = inspect.signature(fn).parameters
        names = set(params)
        has_varkw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        return names, has_varkw
    except (TypeError, ValueError):
        return set(), True


def install():
    """Patch src.tools.api with market-routing wrappers. Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True

    import src.tools.api as us_api
    import src.tools.api_china as cn_api
    from src.markets.ticker import parse_ticker

    def _is_cn_hk(ticker: str) -> bool:
        try:
            m = parse_ticker(ticker).market
            return m.is_china or m.is_hk
        except ValueError:
            return False

    # ── 双边都有的接口：按市场路由 ──
    routed = ["get_prices", "get_financial_metrics", "get_company_news",
              "get_market_cap", "get_price_data"]

    originals = {}
    for name in routed:
        if hasattr(us_api, name) and hasattr(cn_api, name):
            originals[name] = getattr(us_api, name)

    def _make_router(name, orig_fn):
        cn_fn = getattr(cn_api, name)
        cn_params, cn_has_varkw = _signature_info(cn_fn)

        def router(ticker, *args, **kwargs):
            if _is_cn_hk(ticker):
                if kwargs and not cn_has_varkw:
                    dropped = [k for k in kwargs if k not in cn_params]
                    if dropped:
                        logger.debug(
                            "bridge %s: CN/HK 路由剥离未知参数 %s", name, dropped
                        )
                        kwargs = {k: v for k, v in kwargs.items()
                                  if k in cn_params}
                return cn_fn(ticker, *args, **kwargs)
            return orig_fn(ticker, *args, **kwargs)

        router.__name__ = name
        return router

    for name, orig in originals.items():
        setattr(us_api, name, _make_router(name, orig))

    # ── v3.4: search_line_items 路由到中国实现（不再清空）──
    # 此前 CN/HK 一律返回 []，导致 buffett 的盈利一致性/定价权/账面价值/
    # 管理层/内在价值五项子分析与 taleb 的脆弱性分析全部退化为"数据不足"
    # → 低置信中性。现在 A股走新浪三表、港股走东财报表做尽力映射。
    if hasattr(us_api, "search_line_items"):
        orig_sli = getattr(us_api, "search_line_items")

        def _sli_router(ticker, *args, **kwargs):
            if _is_cn_hk(ticker):
                try:
                    from src.tools.line_items_china import search_line_items_china
                    return search_line_items_china(ticker, *args, **kwargs)
                except Exception as e:
                    logger.warning("line_items_china failed for %s: %s", ticker, e)
                    return []
            return orig_sli(ticker, *args, **kwargs)

        _sli_router.__name__ = "search_line_items"
        setattr(us_api, "search_line_items", _sli_router)

    # ── 仍无中国等价物的接口：CN/HK 返回空，避免 404 噪音 ──
    empty_for_cn = ["get_insider_trades"]
    for name in empty_for_cn:
        if not hasattr(us_api, name):
            continue
        orig_fn = getattr(us_api, name)

        def _make_guard(orig):
            def guard(ticker, *args, **kwargs):
                if _is_cn_hk(ticker):
                    return []
                return orig(ticker, *args, **kwargs)
            return guard

        setattr(us_api, name, _make_guard(orig_fn))

    logger.info("API bridge installed (v3.4 line-items routing): %s routed, "
                "search_line_items→china, %s guarded",
                list(originals.keys()), empty_for_cn)
