"""
v3.2 新闻采集器 — 缩小与 openclaw（联网搜索版）的信源差距。

openclaw 早报的优势在事件级归因（隔夜美股驱动、政策新闻、个股事件）。
DeepSeek 无联网搜索，本模块把 AKShare 可达的新闻源注入数据快照：

1. 全球财经电报：财联社电报优先，东财/新浪快讯逐级回退
2. 观察池个股新闻：东财个股新闻（doctor 实测 0.1s 可通；港股不覆盖）

设计原则：
- 列名随 AKShare 版本漂移，按候选名探测，全部失败按位置兜底
- 任何一级失败静默降级到下一级，整体失败返回空列表（由快照标【数据缺口】）
- 标题截断去噪，控制注入 prompt 的 token 量
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TITLE_MAX = 60


def _pick_col(df, candidates: list[str], fallback_idx: int) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if 0 <= fallback_idx < len(df.columns):
        return df.columns[fallback_idx]
    return None


def _clean(text) -> str:
    s = str(text or "").replace("\n", " ").replace("\r", " ").strip()
    return (s[:_TITLE_MAX] + "…") if len(s) > _TITLE_MAX else s


def get_global_telegraph(limit: int = 12) -> list[str]:
    """全球财经电报快讯，返回 ["MM-DD HH:MM 标题", ...]。多级回退。"""
    import akshare as ak

    chains = [
        # (接口名, 参数, 标题候选列, 时间候选列)
        ("stock_info_global_cls", {"symbol": "全部"},
         ["标题", "内容"], ["发布时间", "时间"]),
        ("stock_info_global_em", {},
         ["标题", "摘要"], ["发布时间", "时间"]),
        ("stock_info_global_sina", {},
         ["内容", "标题"], ["时间", "发布时间"]),
    ]
    for name, kwargs, title_cands, time_cands in chains:
        fn = getattr(ak, name, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is None or df.empty:
                continue
            df = df.head(limit)
            tcol = _pick_col(df, title_cands, 1)
            mcol = _pick_col(df, time_cands, 0)
            items = []
            for _, row in df.iterrows():
                title = _clean(row.get(tcol))
                when = _clean(row.get(mcol))[:16] if mcol else ""
                if title:
                    items.append(f"{when} {title}".strip())
            if items:
                logger.info("电报源命中: %s (%d条)", name, len(items))
                return items
        except Exception as ex:
            logger.warning("电报源 %s 失败: %s", name, str(ex)[:80])
    return []


def get_pool_news(tickers: list[str], per_ticker: int = 2,
                  max_total: int = 20) -> list[str]:
    """观察池个股新闻（东财源，仅A股；港股不覆盖直接跳过）。

    返回 ["600519: 标题 (MM-DD)", ...]，单只失败不影响其余。
    """
    import akshare as ak

    fn = getattr(ak, "stock_news_em", None)
    if fn is None:
        return []

    out: list[str] = []
    for t in tickers:
        if len(out) >= max_total:
            break
        code = t.upper().split(".")[0]
        if t.upper().endswith(".HK") or not (code.isdigit() and len(code) == 6):
            continue   # 东财个股新闻不覆盖港股
        try:
            df = fn(symbol=code)
            if df is None or df.empty:
                continue
            tcol = _pick_col(df, ["新闻标题", "标题"], 1)
            mcol = _pick_col(df, ["发布时间", "时间"], 0)
            for _, row in df.head(per_ticker).iterrows():
                title = _clean(row.get(tcol))
                when = _clean(row.get(mcol))[5:16] if mcol else ""
                if title:
                    out.append(f"{t}: {title} ({when})".strip())
        except Exception as ex:
            logger.warning("个股新闻 %s 失败: %s", t, str(ex)[:80])
    return out


if __name__ == "__main__":
    # 自测: poetry run python src/tools/news_collector.py
    from src.tools import proxy_guard  # noqa: F401
    logging.basicConfig(level=logging.INFO)
    print("── 电报 ──")
    for line in get_global_telegraph(limit=5):
        print(" ", line)
    print("── 个股新闻 ──")
    for line in get_pool_news(["600519", "300308", "00148.HK"], per_ticker=2):
        print(" ", line)
