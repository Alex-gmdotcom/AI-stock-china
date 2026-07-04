"""
tools/api_china.py — A 股 & 港股报价数据统一接口
=================================================

v1.0.3 (2026-06-19, Phase 3.1) — china_* agent 私有接口
  - 新增 6 个函数 + 6 个 dataclass 给 china_capital_flow / china_policy /
        china_public_opinion / china_sector_rotation 四个 agent 用
  - get_northbound_flow / get_margin_trading / get_main_capital_flow
        (china_capital_flow)
  - get_public_opinion (china_policy + china_public_opinion)
  - get_sector_performance / get_stock_sector_info (china_sector_rotation)

v1.0.2 (2026-06-19, Phase 3 Step 1) — agent 兼容接口
  - 新增 get_prices / get_financial_metrics / get_company_news /
        get_market_cap / get_price_data
  - 实现基于 AKShare (A 股) + hk_news (港股新闻优先) 双路径
  - 返回 src.data.models 的 Pydantic 类型, 与 api_bridge 路由兼容
  - 字段缺失 / API 失败 → 返回空列表 / None, agent 退化为"数据不足", 不 500

v1.0.1 (2026-06-18, Phase 2 Step 0 集成)

v1.0.1 变更:
  - 删除内部 _normalize() / _infer_a_market() — 改用 markets.ticker.parse_ticker
  - 单一真理源:所有 ticker 规范化经由 parse_ticker(),消除重复逻辑
  - sandbox 兜底:markets.ticker 不可达时退化为最小本地实现,只为 self-test 服务

Fallback chain (基于 2026-06-17 真实环境探测,见 0617 总结 §3):
  1. tencent_qt        qt.gtimg.cn                 (首选,最快,GBK 编码)
  2. sina_hq           hq.sinajs.cn                (备用,需 Referer header)
  3. eastmoney_spot    82.push2.eastmoney.com      (兜底,spot 全表查找)

⚠️ DO NOT 使用 push2.eastmoney.com/api/qt/stock/get —
   在 Alex 网络环境下 RemoteDisconnected,已知不可用。

注意:本模块当前只暴露 quote() / batch_quote() 实时报价接口.
agent 兼容接口 (get_prices/get_financial_metrics/get_company_news/
get_market_cap/get_price_data/search_line_items) 留 Phase 3 实施,
届时基于 AKShare 字段实测调通.
"""

from __future__ import annotations

import re
import time
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional

import requests

__version__ = "1.0.3"

# ---------------------------------------------------------------------------
# Phase 1 依赖软导入 (sandbox 自测时回落到 mock)
# ---------------------------------------------------------------------------

try:
    from markets.proxy import ensure_no_proxy
except ImportError:
    try:
        from src.markets.proxy import ensure_no_proxy  # type: ignore
    except ImportError:
        def ensure_no_proxy() -> None:  # noqa: D401
            """sandbox/self-test 兜底, production 永远走真模块."""
            return None

try:
    from tools.data_fallback import call_with_fallback, NoSourceAvailable
except ImportError:
    try:
        from src.tools.data_fallback import call_with_fallback, NoSourceAvailable  # type: ignore
    except ImportError:
        # sandbox 兜底:复刻 Phase 1 的最小 API,language 完全一致
        class NoSourceAvailable(Exception):
            """所有 fallback 数据源均失败时抛出."""

        def call_with_fallback(chain, *args, **kwargs):
            """
            chain: list[tuple[name: str, fn: Callable]]
            返回 (实际使用的源 name, fn 的返回值)
            全失败时 raise NoSourceAvailable.
            """
            errors: list[tuple[str, str]] = []
            for name, fn in chain:
                try:
                    return name, fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    errors.append((name, f"{type(exc).__name__}: {exc}"))
            raise NoSourceAvailable(
                "all sources failed: " + "; ".join(f"[{n}] {e}" for n, e in errors)
            )


# v1.0.1: ticker 规范化统一走 markets.ticker.parse_ticker
try:
    from markets.ticker import parse_ticker as _parse_ticker, MarketType  # type: ignore
    _HAVE_REAL_TICKER = True
except ImportError:
    try:
        from src.markets.ticker import parse_ticker as _parse_ticker, MarketType  # type: ignore
        _HAVE_REAL_TICKER = True
    except ImportError:
        # sandbox / 单独运行 self-test 时的最小兜底实现.
        # 真生产时必须用 markets.ticker.parse_ticker.
        _HAVE_REAL_TICKER = False

        class _MarketStub:
            value: str
            def __init__(self, v): self.value = v
            @property
            def is_china(self): return self.value in ("SH", "SZ", "BJ")
            @property
            def is_hk(self): return self.value == "HK"

        class _TickerInfoStub:
            __slots__ = ("full_ticker", "code", "market_value")
            def __init__(self, full_ticker: str, code: str, market_value: str):
                self.full_ticker = full_ticker
                self.code = code
                self.market_value = market_value

        _STUB_RE_DOTTED = re.compile(r"^(\d{4,6})\.(SH|SZ|BJ|HK)$", re.IGNORECASE)
        _STUB_RE_PREFIXED = re.compile(r"^(sh|sz|bj|hk)(\d{4,6})$", re.IGNORECASE)

        def _stub_infer_a_market(code: str) -> str:
            if code[:3] == "688" or code[0] == "6":
                return "SH"
            if code[:3] in {"000", "001", "002", "003"} or code[:3] in {"300", "301"}:
                return "SZ"
            if code[:2] in {"43", "83", "87", "88"}:
                return "BJ"
            return "SH"

        def _parse_ticker(raw: str):  # type: ignore[no-redef]
            if not raw or not isinstance(raw, str):
                raise ValueError(f"bad ticker: {raw!r}")
            r = raw.strip().upper()
            m = _STUB_RE_DOTTED.match(r)
            if m:
                code, mkt = m.group(1), m.group(2).upper()
                code = code.zfill(5 if mkt == "HK" else 6)
                full = f"{code}.{mkt}"
                return _TickerInfoStub(full, code, mkt)
            m = _STUB_RE_PREFIXED.match(r)
            if m:
                mkt = m.group(1).upper()
                code = m.group(2).zfill(5 if mkt == "HK" else 6)
                return _TickerInfoStub(f"{code}.{mkt}", code, mkt)
            if r.isdigit():
                if len(r) in (4, 5):
                    code = r.zfill(5)
                    return _TickerInfoStub(f"{code}.HK", code, "HK")
                if len(r) == 6:
                    mkt = _stub_infer_a_market(r)
                    return _TickerInfoStub(f"{r}.{mkt}", r, mkt)
            raise ValueError(f"cannot parse ticker: {raw!r}")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_TIMEOUT = 10
_TIMEOUT_SPOT = 15
_SPOT_CACHE_TTL_SEC = 30


# ---------------------------------------------------------------------------
# 数据类型
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    """统一报价对象,所有 source 解析后都收敛到这里."""

    ticker: str            # 内部规范形式,例如 "600519.SH" / "00700.HK"
    name: str
    price: float           # 最新成交价
    prev_close: float
    open: float
    high: float
    low: float
    volume: int            # 成交量 (股,不是手)
    amount: float          # 成交额 (元)
    change: float          # 涨跌额
    change_pct: float      # 涨跌幅 %
    market: str            # "SH" / "SZ" / "BJ" / "HK"
    timestamp: str         # ISO-ish 时间,源可能略有差异
    source: str            # "tencent_qt" / "sina_hq" / "eastmoney_spot"

    def to_dict(self) -> dict:
        return asdict(self)


class TickerParseError(ValueError):
    """ticker 字符串无法识别成 market 时抛出."""


class NoDataSourceAvailable(NoSourceAvailable):
    """对外别名,语义更明确."""


# ---------------------------------------------------------------------------
# Ticker 规范化 (v1.0.1: 委托给 markets.ticker.parse_ticker)
# ---------------------------------------------------------------------------

def _normalize(ticker: str) -> tuple[str, str]:
    """ticker → (规范形式, market).

    v1.0.1: 单一真理源 — 委托给 markets.ticker.parse_ticker.
    保留这个内部函数仅为兼容 v1.0.0 的内部调用点 (本模块下文 + tests).

    Returns:
        (full_ticker, market_suffix)  例如 ("600519.SH", "SH")
    """
    try:
        info = _parse_ticker(ticker)
    except (ValueError, TypeError, AttributeError) as exc:
        # AttributeError: None / non-string input → .strip() 失败
        # TypeError: 类型异常
        # ValueError: ticker.py 主动 raise
        raise TickerParseError(str(exc) or f"bad ticker: {ticker!r}") from exc
    # 兼容真 ticker.py (有 .market.exchange_suffix) 和 stub (有 .market_value)
    full = info.full_ticker
    if hasattr(info, "market_value"):
        market = info.market_value
    else:
        market = info.market.exchange_suffix  # type: ignore[union-attr]
    return full, market


def _to_tencent_code(norm: str) -> str:
    """ '600519.SH' → 'sh600519' ; '00700.HK' → 'hk00700'."""
    code, market = norm.split(".")
    return f"{market.lower()}{code}"


def _to_sina_code(norm: str) -> str:
    """新浪与腾讯前缀一致."""
    return _to_tencent_code(norm)


# ---------------------------------------------------------------------------
# Source #1 — 腾讯财经 qt.gtimg.cn
# ---------------------------------------------------------------------------

_RE_TENCENT_PAYLOAD = re.compile(r'v_\w+="([^"]*)"', re.DOTALL)


def _parse_tencent_qt(text: str, ticker_norm: str) -> Quote:
    """解析腾讯 qt 返回文本.

    腾讯字段索引 (经验值,各市场略有差异,以 A 股为主):
        0  type
        1  name
        2  code
        3  price (现价)
        4  prev_close (昨收)
        5  open
        6  volume (手)
       30  date_time   YYYYMMDDHHMMSS
       31  change
       32  change_pct
       33  high
       34  low
       37  amount (万元)
    """
    m = _RE_TENCENT_PAYLOAD.search(text)
    if not m:
        raise ValueError(f"tencent_qt 返回格式异常: {text[:200]!r}")
    payload = m.group(1)
    if not payload or "~" not in payload:
        raise ValueError(f"tencent_qt 返回空 payload (ticker 不存在?): {payload!r}")

    f = payload.split("~")
    if len(f) < 10:
        raise ValueError(f"tencent_qt 字段不足 10 个: got {len(f)}")

    def _fnum(idx: int, default: float = 0.0) -> float:
        if idx >= len(f):
            return default
        s = f[idx].strip()
        if not s or s == "-":
            return default
        try:
            return float(s)
        except ValueError:
            return default

    def _fint(idx: int, default: int = 0) -> int:
        return int(_fnum(idx, default))

    name = f[1] if len(f) > 1 else ""
    price = _fnum(3)
    prev_close = _fnum(4)
    open_ = _fnum(5)
    volume_shou = _fint(6)
    change = _fnum(31)
    change_pct = _fnum(32)
    high = _fnum(33)
    low = _fnum(34)
    amount_wan = _fnum(37)  # 万元
    ts_raw = f[30] if len(f) > 30 else ""

    # 时间格式化
    if len(ts_raw) >= 14 and ts_raw.isdigit():
        timestamp = (
            f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} "
            f"{ts_raw[8:10]}:{ts_raw[10:12]}:{ts_raw[12:14]}"
        )
    else:
        timestamp = ts_raw

    # 如果腾讯字段 31/32 没填 (港股有时如此),由 prev_close 反算
    if not change and prev_close and price:
        change = round(price - prev_close, 4)
    if not change_pct and prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 4)

    market = ticker_norm.split(".")[-1]

    return Quote(
        ticker=ticker_norm,
        name=name,
        price=price,
        prev_close=prev_close,
        open=open_,
        high=high,
        low=low,
        volume=volume_shou * 100,        # 手 → 股
        amount=amount_wan * 10000,        # 万元 → 元
        change=change,
        change_pct=change_pct,
        market=market,
        timestamp=timestamp,
        source="tencent_qt",
    )


def _quote_from_tencent_qt(ticker_norm: str) -> Quote:
    url = f"http://qt.gtimg.cn/q={_to_tencent_code(ticker_norm)}"
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.encoding = "gbk"
    resp.raise_for_status()
    return _parse_tencent_qt(resp.text, ticker_norm)


# ---------------------------------------------------------------------------
# Source #2 — 新浪财经 hq.sinajs.cn
# ---------------------------------------------------------------------------

_RE_SINA_PAYLOAD = re.compile(r'hq_str_\w+="([^"]*)"', re.DOTALL)


def _parse_sina_a_share(fields: list[str], ticker_norm: str) -> Quote:
    """新浪 A 股字段 (32 项):
       0 name, 1 today_open, 2 prev_close, 3 price,
       4 high, 5 low, 6 bid1, 7 ask1,
       8 volume(股), 9 amount(元),
       ..., 30 date (YYYY-MM-DD), 31 time (HH:MM:SS)
    """
    if len(fields) < 10:
        raise ValueError(f"sina_a_share 字段不足: {len(fields)}")

    def _fnum(idx: int, default: float = 0.0) -> float:
        if idx >= len(fields):
            return default
        s = fields[idx].strip()
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            return default

    name = fields[0]
    open_ = _fnum(1)
    prev_close = _fnum(2)
    price = _fnum(3)
    high = _fnum(4)
    low = _fnum(5)
    volume = int(_fnum(8))
    amount = _fnum(9)
    date = fields[30] if len(fields) > 30 else ""
    tt = fields[31] if len(fields) > 31 else ""
    timestamp = (date + " " + tt).strip()

    change = round(price - prev_close, 4) if prev_close else 0.0
    change_pct = round(change / prev_close * 100, 4) if prev_close else 0.0

    return Quote(
        ticker=ticker_norm, name=name, price=price, prev_close=prev_close,
        open=open_, high=high, low=low, volume=volume, amount=amount,
        change=change, change_pct=change_pct,
        market=ticker_norm.split(".")[-1], timestamp=timestamp, source="sina_hq",
    )


def _parse_sina_hk_share(fields: list[str], ticker_norm: str) -> Quote:
    """新浪港股字段 (经验 19 项左右):
       0 en_name, 1 cn_name, 2 today_open, 3 prev_close, 4 high, 5 low,
       6 price, 7 change, 8 change_pct, 9 bid1, 10 ask1,
       11 volume_amount_str?, 12 volume_shares?, ...,
       17 date (YYYY/MM/DD), 18 time (HH:MM:SS)

    港股字段不像 A 股那么稳定,best-effort 解析,空值返回 0.
    """
    if len(fields) < 10:
        raise ValueError(f"sina_hk_share 字段不足: {len(fields)}")

    def _fnum(idx: int, default: float = 0.0) -> float:
        if idx >= len(fields):
            return default
        s = fields[idx].strip()
        if not s:
            return default
        try:
            return float(s)
        except ValueError:
            return default

    cn_name = fields[1].strip() or fields[0].strip()
    open_ = _fnum(2)
    prev_close = _fnum(3)
    high = _fnum(4)
    low = _fnum(5)
    price = _fnum(6)
    change = _fnum(7)
    change_pct = _fnum(8)
    volume = int(_fnum(12))
    amount = _fnum(11)
    date = fields[17] if len(fields) > 17 else ""
    tt = fields[18] if len(fields) > 18 else ""
    timestamp = (date.replace("/", "-") + " " + tt).strip()

    if not change and prev_close and price:
        change = round(price - prev_close, 4)
    if not change_pct and prev_close:
        change_pct = round((price - prev_close) / prev_close * 100, 4)

    return Quote(
        ticker=ticker_norm, name=cn_name, price=price, prev_close=prev_close,
        open=open_, high=high, low=low, volume=volume, amount=amount,
        change=change, change_pct=change_pct,
        market="HK", timestamp=timestamp, source="sina_hq",
    )


def _parse_sina_hq(text: str, ticker_norm: str) -> Quote:
    m = _RE_SINA_PAYLOAD.search(text)
    if not m:
        raise ValueError(f"sina_hq 返回格式异常: {text[:200]!r}")
    payload = m.group(1)
    if not payload:
        raise ValueError(
            f"sina_hq 返回空 payload (ticker 不存在 / Referer 被拒): {text[:200]!r}"
        )
    fields = payload.split(",")
    market = ticker_norm.split(".")[-1]
    if market in ("SH", "SZ", "BJ"):
        return _parse_sina_a_share(fields, ticker_norm)
    if market == "HK":
        return _parse_sina_hk_share(fields, ticker_norm)
    raise ValueError(f"sina_hq 不支持的市场: {market}")


def _quote_from_sina_hq(ticker_norm: str) -> Quote:
    url = f"http://hq.sinajs.cn/list={_to_sina_code(ticker_norm)}"
    resp = requests.get(
        url,
        headers={
            "User-Agent": _UA,
            "Referer": "https://finance.sina.com.cn",  # 必须,否则返回空 payload
        },
        timeout=_TIMEOUT,
    )
    resp.encoding = "gbk"
    resp.raise_for_status()
    return _parse_sina_hq(resp.text, ticker_norm)


# ---------------------------------------------------------------------------
# Source #3 — 东方财富 spot 全市场表查找
# ---------------------------------------------------------------------------

# 东财字段索引: f12 code, f14 name, f2 price, f3 change_pct, f4 change,
#               f5 volume(手), f6 amount(元), f15 high, f16 low,
#               f17 open, f18 prev_close

_EM_FIELDS_QUERY = "f2,f3,f4,f5,f6,f7,f8,f12,f14,f15,f16,f17,f18"

_EM_FS_BY_MARKET = {
    "A":  "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",      # 沪深主板 + 创业板 + 科创板
    "BJ": "m:0+t:81+s:2048",                         # 北交所
    "HK": "m:128+t:1,m:128+t:2,m:128+t:3,m:128+t:4", # 港股 (主板 + 创业板等)
}


# spot 表内存缓存 — batch_quote 多只查询场景下显著省请求
_spot_cache_lock = threading.Lock()
_spot_cache: dict[str, tuple[float, dict[str, dict]]] = {}
# key:  "A" / "BJ" / "HK"
# val:  (timestamp, {code: row})


def _fetch_em_spot_table(fs_key: str) -> dict[str, dict]:
    """拉一次东财 spot 全表,返回 {code: row}.带 30s 缓存."""
    now = time.time()
    with _spot_cache_lock:
        cached = _spot_cache.get(fs_key)
        if cached and (now - cached[0]) < _SPOT_CACHE_TTL_SEC:
            return cached[1]

    fs = _EM_FS_BY_MARKET[fs_key]
    url = (
        "http://82.push2.eastmoney.com/api/qt/clist/get"
        "?pn=1&pz=10000&po=1&np=1&fltt=2&invt=2&fid=f3"
        f"&fs={fs}&fields={_EM_FIELDS_QUERY}"
    )
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT_SPOT)
    resp.raise_for_status()
    data = resp.json()
    diff = (data.get("data") or {}).get("diff") or []
    if not diff:
        raise ValueError(f"eastmoney_spot 返回空表 (fs={fs_key})")

    indexed = {str(row.get("f12", "")).zfill(6 if fs_key != "HK" else 5): row
               for row in diff}
    with _spot_cache_lock:
        _spot_cache[fs_key] = (now, indexed)
    return indexed


def _market_to_em_fs_key(market: str) -> str:
    if market == "BJ":
        return "BJ"
    if market == "HK":
        return "HK"
    return "A"  # SH / SZ 都归 A 表


def _parse_em_row(row: dict, ticker_norm: str) -> Quote:
    def _fnum(key: str, default: float = 0.0) -> float:
        v = row.get(key)
        if v in (None, "", "-"):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    market = ticker_norm.split(".")[-1]
    return Quote(
        ticker=ticker_norm,
        name=row.get("f14", "") or "",
        price=_fnum("f2"),
        prev_close=_fnum("f18"),
        open=_fnum("f17"),
        high=_fnum("f15"),
        low=_fnum("f16"),
        volume=int(_fnum("f5")) * 100,    # 手 → 股
        amount=_fnum("f6"),
        change=_fnum("f4"),
        change_pct=_fnum("f3"),
        market=market,
        timestamp="",  # spot endpoint 不带 timestamp,留空
        source="eastmoney_spot",
    )


def _quote_from_em_spot_lookup(ticker_norm: str) -> Quote:
    code, market = ticker_norm.split(".")
    table = _fetch_em_spot_table(_market_to_em_fs_key(market))
    code_key = code.zfill(5 if market == "HK" else 6)
    row = table.get(code_key)
    if not row:
        raise ValueError(
            f"eastmoney_spot 未在 spot 表中找到 {ticker_norm} "
            f"(spot 表 {len(table)} 行,样本 code: {list(table)[:3]})"
        )
    return _parse_em_row(row, ticker_norm)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

# 各市场的 fallback chain
# A 股: 3 源齐全
# 港股: spot 表也有港股分支,作为兜底
_CHAIN: list[tuple[str, Callable[[str], Quote]]] = [
    ("tencent_qt",     _quote_from_tencent_qt),
    ("sina_hq",        _quote_from_sina_hq),
    ("eastmoney_spot", _quote_from_em_spot_lookup),
]


def quote(ticker: str) -> Quote:
    """单只报价 (自动 fallback chain).

    Args:
        ticker: 任意常见形式 ("600519" / "600519.SH" / "sh600519" / "00700.HK" / ...).

    Returns:
        Quote 对象,内含真实使用的 source.

    Raises:
        TickerParseError: ticker 无法识别.
        NoDataSourceAvailable: 三个源全部失败.
    """
    ensure_no_proxy()                          # 进程级 NO_PROXY (Phase 1 保证)
    norm, _market = _normalize(ticker)
    source, q = call_with_fallback(_CHAIN, norm)
    # 注意: source 已经写到 q.source 内,这里 source 与 q.source 必然一致
    if q.source != source:
        # fallback 返回 (name, value) 与 Quote.source 不一致,说明逻辑出 bug
        q.source = source  # 强制对齐
    return q


def batch_quote(tickers: list[str]) -> list[Quote]:
    """批量报价 (内部 fallback 同上).

    简单串行版本,腾讯 / 新浪本身已经支持多 ticker 拼接,
    但首版先保持简单可读,后续如发现性能瓶颈再升级.

    保证返回顺序与 input 一致;单只失败 → 该位置抛出在 partial_failures 列表,
    其他继续.设计折中:不让"一只挂全队挂",但保留个体诊断.

    Returns:
        list[Quote] 中 Quote.ticker 字段对应到 input 顺序.
        失败的位置会用 Quote(ticker=norm, source="<error>...")  填充,
        ticker 字段保留规范化值,source 字段携带错误摘要.
    """
    ensure_no_proxy()
    out: list[Quote] = []
    for raw in tickers:
        try:
            out.append(quote(raw))
        except (TickerParseError, NoDataSourceAvailable) as exc:
            norm = raw  # 解析失败时保留原值
            try:
                norm, _ = _normalize(raw)
            except TickerParseError:
                pass
            out.append(Quote(
                ticker=norm, name="", price=0.0, prev_close=0.0,
                open=0.0, high=0.0, low=0.0, volume=0, amount=0.0,
                change=0.0, change_pct=0.0, market="",
                timestamp="", source=f"<error>{type(exc).__name__}: {exc}",
            ))
    return out


# ---------------------------------------------------------------------------
# Self-test (sandbox 安全 — 仅 mock parser,不发真请求)
# ---------------------------------------------------------------------------

# ===========================================================================
# === v1.0.2: Agent 兼容接口 (Phase 3) ====================================
# ===========================================================================
#
# 实现 src.tools.api 同名 5 函数,签名一致,返回 src.data.models Pydantic 类型.
# 数据源: A 股 AKShare, 港股 AKShare + hk_news 优先.
# search_line_items 见 tools/line_items_china.py.
# fail-soft: 任何失败 → 空, 不让 agent 500.

import logging as _logging
_logger = _logging.getLogger(__name__)

try:
    import akshare as _ak  # type: ignore
    _HAVE_AK = True
except ImportError:
    _ak = None  # type: ignore
    _HAVE_AK = False

try:
    import pandas as _pd  # type: ignore
    _HAVE_PD = True
except ImportError:
    _pd = None  # type: ignore
    _HAVE_PD = False

# 软导入 Pydantic 数据模型
try:
    from data.models import (  # type: ignore
        Price as _Price, FinancialMetrics as _FinancialMetrics,
        LineItem as _LineItem, CompanyNews as _CompanyNews,
    )
    _HAVE_REAL_MODELS = True
except ImportError:
    try:
        from src.data.models import (  # type: ignore
            Price as _Price, FinancialMetrics as _FinancialMetrics,
            LineItem as _LineItem, CompanyNews as _CompanyNews,
        )
        _HAVE_REAL_MODELS = True
    except ImportError:
        # sandbox 兜底 dataclass (字段从 warren_buffett.py 反推)
        from dataclasses import dataclass as _dc, asdict as _asdict
        _HAVE_REAL_MODELS = False

        @_dc
        class _Price:  # type: ignore
            time: str = ""
            open: float = 0.0
            close: float = 0.0
            high: float = 0.0
            low: float = 0.0
            volume: int = 0
            def model_dump(self): return _asdict(self)

        @_dc
        class _FinancialMetrics:  # type: ignore
            ticker: str = ""
            report_period: str = ""
            period: str = "ttm"
            currency: str = "CNY"
            market_cap: Optional[float] = None
            return_on_equity: Optional[float] = None
            debt_to_equity: Optional[float] = None
            debt_to_assets: Optional[float] = None
            gross_margin: Optional[float] = None
            operating_margin: Optional[float] = None
            net_margin: Optional[float] = None
            current_ratio: Optional[float] = None
            quick_ratio: Optional[float] = None
            asset_turnover: Optional[float] = None
            inventory_turnover: Optional[float] = None
            receivables_turnover: Optional[float] = None
            earnings_per_share: Optional[float] = None
            book_value_per_share: Optional[float] = None
            def model_dump(self): return _asdict(self)

        @_dc
        class _LineItem:  # type: ignore
            ticker: str = ""
            report_period: str = ""
            period: str = "ttm"
            currency: str = "CNY"
            revenue: Optional[float] = None
            gross_profit: Optional[float] = None
            net_income: Optional[float] = None
            free_cash_flow: Optional[float] = None
            capital_expenditure: Optional[float] = None
            depreciation_and_amortization: Optional[float] = None
            outstanding_shares: Optional[float] = None
            total_assets: Optional[float] = None
            total_liabilities: Optional[float] = None
            shareholders_equity: Optional[float] = None
            dividends_and_other_cash_distributions: Optional[float] = None
            issuance_or_purchase_of_equity_shares: Optional[float] = None
            def model_dump(self): return _asdict(self)

        @_dc
        class _CompanyNews:  # type: ignore
            ticker: str = ""
            title: str = ""
            author: str = ""
            source: str = ""
            date: str = ""
            url: str = ""
            sentiment: str = ""
            def model_dump(self): return _asdict(self)


class _NullCache:
    def get_prices(self, *a, **kw): return None
    def set_prices(self, *a, **kw): return None
    def get_financial_metrics(self, *a, **kw): return None
    def set_financial_metrics(self, *a, **kw): return None
    def get_company_news(self, *a, **kw): return None
    def set_company_news(self, *a, **kw): return None


try:
    from data.cache import get_cache as _get_cache  # type: ignore
    _cache_v2 = _get_cache()
except ImportError:
    try:
        from src.data.cache import get_cache as _get_cache  # type: ignore
        _cache_v2 = _get_cache()
    except ImportError:
        _cache_v2 = _NullCache()

# hk_news 软导入 (港股新闻优先用本地存档)
_hk_news_store = None
try:
    from hk_news.storage import HKNewsStorage as _HKNewsStorage  # type: ignore
    try: _hk_news_store = _HKNewsStorage()
    except Exception: pass
except ImportError:
    try:
        from src.hk_news.storage import HKNewsStorage as _HKNewsStorage  # type: ignore
        try: _hk_news_store = _HKNewsStorage()
        except Exception: pass
    except ImportError:
        pass


# ── 工具函数 ──────────────────────────────────────────────────────────

from datetime import datetime as _datetime

def _ak_a_symbol(norm: str) -> str:
    """ '600519.SH' → '600519' (AKShare A 股用裸 6 位)."""
    return norm.split(".")[0]

def _ak_hk_symbol(norm: str) -> str:
    """ '00700.HK' → '00700' (AKShare 港股用裸 5 位)."""
    return norm.split(".")[0].zfill(5)

def _to_ak_date(yyyy_mm_dd: str) -> str:
    """'2026-06-18' → '20260618'."""
    s = (yyyy_mm_dd or "").strip()
    if len(s) == 8 and s.isdigit():
        return s
    return s.replace("-", "").replace("/", "")[:8]

def _from_ak_date(s) -> str:
    """AKShare date → ISO 'YYYY-MM-DD'."""
    if s is None: return ""
    s = str(s)
    if len(s) >= 10 and s[4] == "-": return s[:10]
    c = s.replace("/", "").replace("-", "")[:8]
    if len(c) == 8 and c.isdigit():
        return f"{c[:4]}-{c[4:6]}-{c[6:8]}"
    return s

def _row_get(row, *keys, default=None):
    for k in keys:
        try: v = row[k]
        except (KeyError, IndexError, TypeError): continue
        if v is None: continue
        try:
            if _HAVE_PD and _pd.isna(v): continue  # type: ignore
        except Exception: pass
        return v
    return default

def _to_float(v, default=None):
    if v is None or v == "": return default
    try:
        f = float(v)
        if _HAVE_PD and _pd.isna(f): return default  # type: ignore
        return f
    except (TypeError, ValueError): return default

def _to_int(v, default=0):
    f = _to_float(v, None)
    return int(f) if f is not None else default

def _safe_construct(cls, **kwargs):
    """Pydantic v2 / dataclass / mock 都能用的安全构造."""
    try:
        return cls(**kwargs)
    except Exception:
        try:
            import inspect
            sig = inspect.signature(cls)
            accepted = set(sig.parameters)
            filtered = {k: v for k, v in kwargs.items() if k in accepted}
            return cls(**filtered)
        except Exception as exc:
            _logger.debug("_safe_construct(%s) 失败: %s", cls.__name__, exc)
            return None


# ===========================================================================
# Agent 兼容接口 (v1.0.2): get_prices / get_financial_metrics /
#                          get_company_news / get_market_cap / get_price_data
# ===========================================================================

def _get_prices_df_with_fallback(norm: str, market: str, ak_start: str, ak_end: str):
    """K线抓取：东财主源 → 新浪兜底。

    - 新浪 A 股 qfq 对部分次新股会在 akshare 内部抛 KeyError 'date'，自动降级到不复权。
    - 新浪可能把 date 作为 index 而非列，统一 reset 成列供下游解析。
    - AIHF_PRICE_SOURCE=sina 时新浪优先（海外/东财不可达时避免每次等东财超时）。
    任一源返回非空即用，全失败返回 None。
    """
    def _em():
        if market == "HK":
            return _ak.stock_hk_hist(symbol=_ak_hk_symbol(norm), period="daily",
                                     start_date=ak_start, end_date=ak_end, adjust="qfq")
        return _ak.stock_zh_a_hist(symbol=_ak_a_symbol(norm), period="daily",
                                   start_date=ak_start, end_date=ak_end, adjust="qfq")

    def _sina():
        if market == "HK":
            df = _ak.stock_hk_daily(symbol=_ak_hk_symbol(norm), adjust="qfq")
        else:
            try:
                df = _ak.stock_zh_a_daily(symbol=_to_sina_code(norm),
                                          start_date=ak_start, end_date=ak_end, adjust="qfq")
            except Exception:
                # qfq 路径在部分股上抛 'date' 等 KeyError → 降级到不复权
                df = _ak.stock_zh_a_daily(symbol=_to_sina_code(norm),
                                          start_date=ak_start, end_date=ak_end)
        if df is None or len(df) == 0:
            return df
        # date 可能是 index 而非列 → 重置为列，供 _row_get 找到
        try:
            cols = list(getattr(df, "columns", []))
            if "date" not in cols and "日期" not in cols:
                df = df.reset_index()
        except Exception:
            pass
        # HK 全历史按区间裁剪
        if market == "HK":
            try:
                if "date" in df.columns:
                    s, e = _from_ak_date(ak_start), _from_ak_date(ak_end)
                    ds = df["date"].astype(str).str.slice(0, 10)
                    df = df[(ds >= s) & (ds <= e)]
            except Exception:
                pass
        return df

    if market not in ("HK", "SH", "SZ", "BJ"):
        return None

    import os as _os
    prefer = (_os.environ.get("AIHF_PRICE_SOURCE", "") or "").strip().lower()
    chain = ([("sina", _sina), ("eastmoney", _em)] if prefer == "sina"
             else [("eastmoney", _em), ("sina", _sina)])

    # marker: TUSHARE_HK_PRICE_V1 — HK 历史价首选 tushare hk_daily
    # (token 化 REST,机制不同于东财/新浪 web JSON;两者 2026-07-03 双双失守
    #  致 current_price=0 决策短路)。无 token/无权限 → available()=False 自动跳过。
    if market == "HK":
        def _tushare_hk():
            try:
                from src.tools import tushare_data as _tsd
            except ImportError:
                import tushare_data as _tsd  # type: ignore
            if not _tsd.available():
                # marker: TUSHARE_HK_PRICE_V2 — 无声路径现形
                _logger.warning("tushare_hk 跳过: available()=False (token/tushare包缺失)")
                return None
            return _tsd.get_hk_prices_df(norm, ak_start, ak_end)
        chain = [("tushare_hk", _tushare_hk)] + chain

    for name, fn in chain:
        try:
            df = fn()
        except Exception as exc:
            _logger.warning("get_prices source=%s 失败: %s", name, str(exc)[:160])
            continue
        if df is not None and len(df) > 0:
            if name != chain[0][0]:
                _logger.info("get_prices(%s) 回退到 source=%s rows=%d", norm, name, len(df))
            return df
    return None


def get_prices(ticker: str, start_date: str, end_date: str,
               api_key: Optional[str] = None) -> list:
    """A 股 / 港股 K 线 → list[Price]. api_bridge.py 路由兼容."""
    if not _HAVE_AK:
        return []
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return []

    cache_key = f"{norm}_{start_date}_{end_date}"
    cached = _cache_v2.get_prices(cache_key)
    if cached:
        try:
            return [x for x in (_safe_construct(_Price, **p) for p in cached) if x]
        except Exception:
            pass

    ensure_no_proxy()
    ak_start = _to_ak_date(start_date)
    ak_end = _to_ak_date(end_date)

    df = _get_prices_df_with_fallback(norm, market, ak_start, ak_end)
    if df is None or len(df) == 0:
        return []

    out: list = []
    for _, row in df.iterrows():
        p = _safe_construct(
            _Price,
            time=_from_ak_date(_row_get(row, "日期", "date")),
            open=_to_float(_row_get(row, "开盘", "open"), 0.0),
            close=_to_float(_row_get(row, "收盘", "close"), 0.0),
            high=_to_float(_row_get(row, "最高", "high"), 0.0),
            low=_to_float(_row_get(row, "最低", "low"), 0.0),
            volume=_to_int(_row_get(row, "成交量", "volume"), 0),
        )
        if p:
            out.append(p)

    if out:
        try:
            _cache_v2.set_prices(cache_key, [p.model_dump() for p in out])
        except Exception:
            pass
    return out


def get_price_data(ticker: str, start_date: str, end_date: str,
                    api_key: Optional[str] = None):
    """K 线 → DataFrame (与 src.tools.api.get_price_data 同接口)."""
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    if not _HAVE_PD:
        return None
    df = _pd.DataFrame([p.model_dump() for p in prices])  # type: ignore
    if df.empty:
        return df
    df["Date"] = _pd.to_datetime(df["time"])  # type: ignore
    df.set_index("Date", inplace=True)
    for col in ("open", "close", "high", "low", "volume"):
        if col in df.columns:
            df[col] = _pd.to_numeric(df[col], errors="coerce")  # type: ignore
    df.sort_index(inplace=True)
    return df


# 同花顺财务摘要中文指标 → FinancialMetrics 字段映射
_THS_METRIC_MAP = {
    "净资产收益率": "return_on_equity",
    "净资产收益率(摊薄)": "return_on_equity",
    "ROE": "return_on_equity",
    "毛利率": "gross_margin",
    "销售毛利率": "gross_margin",
    "净利率": "net_margin",
    "销售净利率": "net_margin",
    "营业利润率": "operating_margin",
    "资产负债率": "debt_to_assets",
    "流动比率": "current_ratio",
    "速动比率": "quick_ratio",
    "存货周转率": "inventory_turnover",
    "应收账款周转率": "receivables_turnover",
    "总资产周转率": "asset_turnover",
    "每股收益": "earnings_per_share",
    "基本每股收益": "earnings_per_share",
    "每股净资产": "book_value_per_share",
}


def _parse_ths_value(raw) -> Optional[float]:
    """同花顺值带 '%' / '亿' / '万' 后缀清洗."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("--", "-", "None", "nan", "NaN"):
        return None
    s = s.replace(",", "")
    mult = 1.0
    if s.endswith("%"):
        mult = 0.01
        s = s[:-1]
    elif s.endswith("亿"):
        mult = 1e8
        s = s[:-1]
    elif s.endswith("万"):
        mult = 1e4
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _fetch_a_share_metrics(norm: str) -> list:
    """A 股财务摘要 (同花顺 按报告期降序)."""
    try:
        df = _ak.stock_financial_abstract_ths(symbol=_ak_a_symbol(norm), indicator="按报告期")
    except Exception as exc:
        _logger.warning("stock_financial_abstract_ths(%s) 失败: %s", norm, exc)
        return []
    if df is None or len(df) == 0:
        return []

    date_col = None
    for cand in ("报告期", "报告时间", "时间", "日期"):
        if cand in df.columns:
            date_col = cand
            break

    out = []
    for _, row in df.iterrows():
        rep = str(_row_get(row, date_col, default="")) if date_col else ""
        if not rep:
            continue
        rec = {"report_period": _from_ak_date(rep)}
        for col in df.columns:
            mapped = _THS_METRIC_MAP.get(col)
            if not mapped:
                continue
            val = _parse_ths_value(row[col])
            if val is not None and rec.get(mapped) is None:
                rec[mapped] = val
        out.append(rec)

    out.sort(key=lambda r: r.get("report_period", ""), reverse=True)
    return out


def _fetch_hk_metrics(norm: str) -> list:
    """港股财务指标 (东财年度利润表)."""
    try:
        df = _ak.stock_financial_hk_report_em(
            stock=_ak_hk_symbol(norm), symbol="利润表", indicator="年度")
    except Exception as exc:
        _logger.warning("stock_financial_hk_report_em(%s) 失败: %s", norm, exc)
        return []
    if df is None or len(df) == 0:
        return []

    date_col = None
    for cand in ("REPORT_DATE", "报告期", "报告日期"):
        if cand in df.columns:
            date_col = cand
            break

    out = []
    for _, row in df.iterrows():
        rep = _from_ak_date(_row_get(row, date_col, default="")) if date_col else ""
        if not rep:
            continue
        revenue = _to_float(_row_get(row, "营业额", "营业收入", "REVENUE"))
        net_income = _to_float(_row_get(row, "股东应占溢利", "净利润", "NET_PROFIT"))
        gross_margin = _to_float(_row_get(row, "毛利率"))
        if gross_margin is not None and abs(gross_margin) > 1:
            gross_margin = gross_margin / 100.0
        net_margin = (net_income / revenue) if (revenue and net_income) else None
        out.append({
            "report_period": rep,
            "gross_margin": gross_margin,
            "net_margin": net_margin,
        })
    out.sort(key=lambda r: r.get("report_period", ""), reverse=True)
    return out


def get_financial_metrics(ticker: str, end_date: str, period: str = "ttm",
                           limit: int = 10, api_key: Optional[str] = None) -> list:
    """财务指标 (按报告期降序) → list[FinancialMetrics]."""
    if not _HAVE_AK:
        return []
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return []

    cache_key = f"{norm}_{period}_{end_date}_{limit}"
    cached = _cache_v2.get_financial_metrics(cache_key)
    if cached:
        try:
            out = [x for x in (_safe_construct(_FinancialMetrics, **m) for m in cached) if x]
            if out:
                return out
        except Exception:
            pass

    ensure_no_proxy()
    if market in ("SH", "SZ", "BJ"):
        records = _fetch_a_share_metrics(norm)
    elif market == "HK":
        records = _fetch_hk_metrics(norm)
    else:
        return []

    if end_date:
        records = [r for r in records if r.get("report_period", "9999") <= end_date]
    records = records[:limit]

    cap = get_market_cap(norm, end_date or _datetime.now().strftime("%Y-%m-%d"),
                          api_key=api_key)

    out = []
    for rec in records:
        rec.setdefault("ticker", norm)
        rec.setdefault("period", period)
        rec.setdefault("currency", "HKD" if market == "HK" else "CNY")
        if cap is not None and rec.get("market_cap") is None:
            rec["market_cap"] = cap
        m = _safe_construct(_FinancialMetrics, **rec)
        if m:
            out.append(m)

    if out:
        try:
            _cache_v2.set_financial_metrics(cache_key, [m.model_dump() for m in out])
        except Exception:
            pass
    return out


def get_company_news(ticker: str, end_date: str,
                      start_date: Optional[str] = None, limit: int = 1000,
                      api_key: Optional[str] = None) -> list:
    """公司新闻 → list[CompanyNews]. 港股优先用本地 hk_news 存档."""
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return []

    cache_key = f"{norm}_{start_date or 'none'}_{end_date}_{limit}"
    cached = _cache_v2.get_company_news(cache_key)
    if cached:
        try:
            out = [x for x in (_safe_construct(_CompanyNews, **n) for n in cached) if x]
            if out:
                return out
        except Exception:
            pass

    out = []

    # 港股优先 hk_news 存档
    if market == "HK" and _hk_news_store is not None:
        try:
            snaps = _hk_news_store.list_snapshots(norm)
            if snaps:
                snap = _hk_news_store.get(snaps[0]["snapshot_id"])
                for n in snap.news[:limit]:
                    pub = n.published_at.isoformat() if n.published_at else ""
                    news = _safe_construct(
                        _CompanyNews,
                        ticker=norm, title=n.title, author="",
                        source=n.source, date=pub, url=n.url,
                        sentiment=n.sentiment.value if n.sentiment else "",
                    )
                    if news:
                        out.append(news)
        except Exception as exc:
            _logger.debug("hk_news 读 %s 失败: %s", norm, exc)

    # 退化: AKShare news
    if not out and _HAVE_AK:
        ensure_no_proxy()
        try:
            sym = _ak_a_symbol(norm) if market != "HK" else _ak_hk_symbol(norm)
            df = _ak.stock_news_em(symbol=sym)
        except Exception as exc:
            _logger.warning("stock_news_em(%s) 失败: %s", norm, exc)
            df = None

        if df is not None and len(df) > 0:
            for _, row in df.iterrows():
                date_str = str(_row_get(row, "发布时间", "date", default=""))
                if start_date and date_str[:10] < start_date:
                    continue
                if end_date and date_str[:10] > end_date:
                    continue
                news = _safe_construct(
                    _CompanyNews,
                    ticker=norm,
                    title=str(_row_get(row, "新闻标题", "title", default="")),
                    author="",
                    source=str(_row_get(row, "文章来源", "source", default="")),
                    date=date_str,
                    url=str(_row_get(row, "新闻链接", "url", default="")),
                    sentiment="",
                )
                if news:
                    out.append(news)
                if len(out) >= limit:
                    break

    if out:
        try:
            _cache_v2.set_company_news(cache_key, [n.model_dump() for n in out])
        except Exception:
            pass
    return out


def get_market_cap(ticker: str, end_date: str,
                    api_key: Optional[str] = None) -> Optional[float]:
    """市值 → float | None."""
    if not _HAVE_AK:
        return None
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return None

    ensure_no_proxy()

    if market in ("SH", "SZ", "BJ"):
        try:
            df = _ak.stock_individual_info_em(symbol=_ak_a_symbol(norm))
        except Exception as exc:
            _logger.warning("stock_individual_info_em(%s) 失败: %s", norm, exc)
            return None
        if df is None or len(df) == 0:
            return None
        try:
            for _, row in df.iterrows():
                item = str(_row_get(row, "item", default=""))
                if "总市值" in item:
                    return _to_float(_row_get(row, "value"))
        except Exception:
            return None
        return None

    if market == "HK":
        try:
            df = _ak.stock_hk_spot_em()
        except Exception:
            return None
        if df is None or len(df) == 0:
            return None
        try:
            code = _ak_hk_symbol(norm)
            hit = df[df["代码"] == code]
            if hit is not None and len(hit) > 0:
                return _to_float(hit.iloc[0].get("总市值"))
        except Exception:
            return None
    return None


def _selftest() -> None:
    print(f"[api_china v{__version__}] self-test (mock-based, no network)")
    failures: list[str] = []

    # -----------------------------------------------------------------------
    # T1: ticker 规范化
    # -----------------------------------------------------------------------
    cases = [
        ("600519",     ("600519.SH", "SH")),
        ("600519.SH",  ("600519.SH", "SH")),
        ("sh600519",   ("600519.SH", "SH")),
        ("SH600519",   ("600519.SH", "SH")),
        ("000001",     ("000001.SZ", "SZ")),
        ("000001.SZ",  ("000001.SZ", "SZ")),
        ("sz000001",   ("000001.SZ", "SZ")),
        ("300750",     ("300750.SZ", "SZ")),
        ("688008",     ("688008.SH", "SH")),
        ("00700.HK",   ("00700.HK",  "HK")),
        ("0700.HK",    ("00700.HK",  "HK")),  # 4 位补齐
        ("hk00700",    ("00700.HK",  "HK")),
        ("00700",      ("00700.HK",  "HK")),  # 纯 5 位 → 港股
        ("430047",     ("430047.BJ", "BJ")),
        ("830799",     ("830799.BJ", "BJ")),
    ]
    for ticker_in, expected in cases:
        got = _normalize(ticker_in)
        if got != expected:
            failures.append(f"T1 normalize({ticker_in!r}) → {got} ≠ {expected}")
    if not any(f.startswith("T1") for f in failures):
        print(f"[T1] ticker normalize ({len(cases)} cases): PASS")

    # T1 negative: 应拒
    # 注意: markets.ticker 接受 'abc' (US) 和 '12' (zfill 到 HK 5 位短码),这是其设计.
    # 这里只测明确无效的输入.
    for bad in ["", "12345678", "@invalid@", None]:
        try:
            _normalize(bad)  # type: ignore[arg-type]
            failures.append(f"T1.neg _normalize({bad!r}) 应抛 TickerParseError 却没抛")
        except TickerParseError:
            pass
        except Exception as exc:
            failures.append(f"T1.neg _normalize({bad!r}) 抛错类型不对: {type(exc).__name__}")
    if not any(f.startswith("T1.neg") for f in failures):
        print("[T1.neg] bad tickers reject correctly: PASS")

    # -----------------------------------------------------------------------
    # T2: 腾讯 qt parser
    # -----------------------------------------------------------------------
    # 构造一个 ~50 字段的腾讯响应 (索引覆盖 0..40)
    tencent_fields = ([""] * 50)
    tencent_fields[0]  = "1"
    tencent_fields[1]  = "贵州茅台"
    tencent_fields[2]  = "600519"
    tencent_fields[3]  = "1240.00"
    tencent_fields[4]  = "1258.00"
    tencent_fields[5]  = "1260.00"
    tencent_fields[6]  = "48276"           # 手
    tencent_fields[30] = "20260617150000"
    tencent_fields[31] = "-18.00"
    tencent_fields[32] = "-1.43"
    tencent_fields[33] = "1268.00"
    tencent_fields[34] = "1238.00"
    tencent_fields[37] = "598589.7456"     # 万元
    mock_tencent = f'v_sh600519="{"~".join(tencent_fields)}";'

    q = _parse_tencent_qt(mock_tencent, "600519.SH")
    checks = [
        ("name", q.name, "贵州茅台"),
        ("price", q.price, 1240.00),
        ("prev_close", q.prev_close, 1258.00),
        ("open", q.open, 1260.00),
        ("high", q.high, 1268.00),
        ("low", q.low, 1238.00),
        ("volume", q.volume, 48276 * 100),     # 手 → 股
        ("change", q.change, -18.00),
        ("change_pct", q.change_pct, -1.43),
        ("source", q.source, "tencent_qt"),
        ("market", q.market, "SH"),
        ("timestamp", q.timestamp, "2026-06-17 15:00:00"),
    ]
    for field, got, want in checks:
        if got != want:
            failures.append(f"T2 tencent.{field}: got {got!r} ≠ {want!r}")
    # amount 浮点比较
    if abs(q.amount - 598589.7456 * 10000) > 0.01:
        failures.append(f"T2 tencent.amount: got {q.amount} ≠ 5985897456")
    if not any(f.startswith("T2") for f in failures):
        print(f"[T2] tencent_qt parser: PASS ({q.name} @ {q.price}, src={q.source})")

    # T2.neg: 空 payload
    try:
        _parse_tencent_qt('v_sh999999="";', "999999.SH")
        failures.append("T2.neg 空 payload 应抛错")
    except ValueError:
        print("[T2.neg] tencent empty payload rejects: PASS")

    # -----------------------------------------------------------------------
    # T3: 新浪 A 股 parser
    # -----------------------------------------------------------------------
    sina_a_fields = (
        ["贵州茅台", "1260.000", "1258.000", "1240.000", "1268.000", "1238.000",
         "1240.000", "1240.001", "4827600", "5985897456.000"]
        + [""] * 20
        + ["2026-06-17", "15:00:00"]
    )
    mock_sina_a = f'var hq_str_sh600519="{",".join(sina_a_fields)}";'
    q = _parse_sina_hq(mock_sina_a, "600519.SH")
    if q.name != "贵州茅台":
        failures.append(f"T3 sina.name: {q.name!r}")
    if abs(q.price - 1240.0) > 0.001:
        failures.append(f"T3 sina.price: {q.price}")
    if abs(q.prev_close - 1258.0) > 0.001:
        failures.append(f"T3 sina.prev_close: {q.prev_close}")
    # change/change_pct 由 prev_close 反算
    if abs(q.change - (-18.0)) > 0.001:
        failures.append(f"T3 sina.change: {q.change}")
    if abs(q.change_pct - (-1.4308)) > 0.01:
        failures.append(f"T3 sina.change_pct: {q.change_pct}")
    if q.source != "sina_hq":
        failures.append(f"T3 sina.source: {q.source}")
    if not any(f.startswith("T3") for f in failures):
        print(f"[T3] sina_hq A-share parser: PASS ({q.name} @ {q.price}, src={q.source})")

    # T3.neg: 空 payload (新浪缺 Referer 时的典型表现)
    try:
        _parse_sina_hq('var hq_str_sh600519="";', "600519.SH")
        failures.append("T3.neg 空 payload 应抛错")
    except ValueError:
        print("[T3.neg] sina empty payload rejects: PASS")

    # -----------------------------------------------------------------------
    # T4: 东财 spot parser
    # -----------------------------------------------------------------------
    em_row = {
        "f12": "600519", "f14": "贵州茅台",
        "f2": 1240.0, "f3": -1.43, "f4": -18.0,
        "f5": 48276, "f6": 5985897456.0,
        "f15": 1268.0, "f16": 1238.0, "f17": 1260.0, "f18": 1258.0,
    }
    q = _parse_em_row(em_row, "600519.SH")
    checks = [
        ("name", q.name, "贵州茅台"),
        ("price", q.price, 1240.0),
        ("change_pct", q.change_pct, -1.43),
        ("change", q.change, -18.0),
        ("high", q.high, 1268.0),
        ("low", q.low, 1238.0),
        ("open", q.open, 1260.0),
        ("prev_close", q.prev_close, 1258.0),
        ("volume", q.volume, 48276 * 100),
        ("amount", q.amount, 5985897456.0),
        ("source", q.source, "eastmoney_spot"),
    ]
    for field, got, want in checks:
        if got != want:
            failures.append(f"T4 em.{field}: got {got!r} ≠ {want!r}")
    if not any(f.startswith("T4") for f in failures):
        print(f"[T4] eastmoney_spot parser: PASS ({q.name} @ {q.price}, src={q.source})")

    # -----------------------------------------------------------------------
    # T5: fallback chain — 第一源挂,第二源成功
    # -----------------------------------------------------------------------
    def _fail_tencent(_norm: str) -> Quote:
        raise ConnectionError("simulated tencent down")

    def _ok_sina(norm: str) -> Quote:
        return Quote(
            ticker=norm, name="模拟新浪", price=999.99, prev_close=998.00,
            open=998.50, high=1001.00, low=997.50, volume=12345, amount=12345678,
            change=1.99, change_pct=0.199, market=norm.split(".")[-1],
            timestamp="2026-06-18 09:30:00", source="sina_hq",
        )

    chain = [("tencent_qt", _fail_tencent), ("sina_hq", _ok_sina)]
    used, q = call_with_fallback(chain, "600519.SH")
    if used != "sina_hq" or q.source != "sina_hq":
        failures.append(f"T5 fallback: expected sina_hq, got used={used} src={q.source}")
    else:
        print(f"[T5] fallback chain (tencent down → sina ok): PASS (used={used})")

    # T5.neg: 全部失败
    def _fail_any(_norm: str) -> Quote:
        raise ConnectionError("simulated all down")
    chain_all_fail = [("tencent_qt", _fail_any), ("sina_hq", _fail_any), ("em_spot", _fail_any)]
    try:
        call_with_fallback(chain_all_fail, "600519.SH")
        failures.append("T5.neg 全失败时应抛 NoSourceAvailable")
    except NoSourceAvailable as exc:
        # 错误消息应同时包含全部 source name (审计要求)
        msg = str(exc)
        if not all(s in msg for s in ["tencent_qt", "sina_hq", "em_spot"]):
            failures.append(f"T5.neg 错误消息未含全部 source: {msg}")
        else:
            print("[T5.neg] all sources fail raises NoSourceAvailable with full audit: PASS")

    # -----------------------------------------------------------------------
    # T6: batch_quote 部分失败不影响其他
    # -----------------------------------------------------------------------
    # mock quote() 内部 chain
    orig_chain = _CHAIN.copy()
    try:
        _CHAIN.clear()
        _CHAIN.append(("tencent_qt", _ok_sina))  # 复用 _ok_sina,简单返回成功

        results = batch_quote(["600519", "bad_ticker", "000001"])
        if len(results) != 3:
            failures.append(f"T6 batch 长度: {len(results)} ≠ 3")
        # chain key 强制覆盖 source (审计 invariant):mock fn 自报 "sina_hq",
        # 但 chain 注册名是 "tencent_qt",最终 q.source 必须是 "tencent_qt"
        if results[0].source != "tencent_qt":
            failures.append(f"T6 batch[0].source: {results[0].source} (chain key 覆盖未生效)")
        if not results[1].source.startswith("<error>"):
            failures.append(f"T6 batch[1] 应为 error: {results[1].source}")
        if results[2].source != "tencent_qt":
            failures.append(f"T6 batch[2].source: {results[2].source}")
        if not any(f.startswith("T6") for f in failures):
            print("[T6] batch_quote partial failure isolated + chain-key-override: PASS")
    finally:
        _CHAIN.clear()
        _CHAIN.extend(orig_chain)

    # -----------------------------------------------------------------------
    # T7: HK 港股 ticker 路径走得通 (规范 + EM fs_key)
    # -----------------------------------------------------------------------
    norm_hk, m_hk = _normalize("00700.HK")
    if norm_hk != "00700.HK" or m_hk != "HK":
        failures.append(f"T7 HK normalize: {norm_hk}/{m_hk}")
    if _to_tencent_code("00700.HK") != "hk00700":
        failures.append(f"T7 HK tencent code: {_to_tencent_code('00700.HK')}")
    if _market_to_em_fs_key("HK") != "HK":
        failures.append("T7 HK fs_key")
    if not any(f.startswith("T7") for f in failures):
        print("[T7] HK ticker pipeline (normalize → tencent code → em fs_key): PASS")

    # -----------------------------------------------------------------------
    # 汇总
    # -----------------------------------------------------------------------
    print()
    if failures:
        print(f"[api_china] self-test FAILED ({len(failures)} 项):")
        for fmsg in failures:
            print(f"  ✗ {fmsg}")
        raise SystemExit(1)
    print(f"[api_china v{__version__}] self-test PASS (7 groups + 4 negative checks)")


# ===========================================================================
# v1.0.2 Agent 接口 self-test (mock AKShare)
# ===========================================================================

def _selftest_v102() -> None:
    print(f"\n[api_china v{__version__}] v1.0.2 agent 接口 self-test")
    failures: list[str] = []
    global _ak, _HAVE_AK, _hk_news_store
    real_ak, real_have_ak, real_hk_store = _ak, _HAVE_AK, _hk_news_store

    try:
        class _FakeAK:
            @staticmethod
            def stock_zh_a_hist(symbol, period, start_date, end_date, adjust):
                return _pd.DataFrame([
                    {"日期": "2026-06-17", "开盘": 1258.0, "收盘": 1240.0,
                     "最高": 1268.0, "最低": 1238.0, "成交量": 48276},
                    {"日期": "2026-06-18", "开盘": 1240.0, "收盘": 1245.0,
                     "最高": 1250.0, "最低": 1235.0, "成交量": 52341},
                ])
            @staticmethod
            def stock_hk_hist(symbol, period, start_date, end_date, adjust):
                return _pd.DataFrame([
                    {"日期": "2026-06-18", "开盘": 615.0, "收盘": 617.5,
                     "最高": 620.0, "最低": 612.0, "成交量": 12345678},
                ])
            @staticmethod
            def stock_financial_abstract_ths(symbol, indicator):
                return _pd.DataFrame([
                    {"报告期": "2025-12-31", "净资产收益率": "32.5%",
                     "销售毛利率": "91.8%", "销售净利率": "53.2%",
                     "营业利润率": "67.5%", "资产负债率": "18.3%"},
                    {"报告期": "2024-12-31", "净资产收益率": "30.1%",
                     "销售毛利率": "91.5%", "销售净利率": "52.0%",
                     "营业利润率": "66.0%", "资产负债率": "19.0%"},
                ])
            @staticmethod
            def stock_news_em(symbol):
                return _pd.DataFrame([
                    {"新闻标题": "茅台 Q1 业绩超预期",
                     "发布时间": "2026-06-15 10:30:00",
                     "文章来源": "财华社", "新闻链接": "https://example.com/n1"},
                    {"新闻标题": "白酒板块全线走强",
                     "发布时间": "2026-06-14 14:20:00",
                     "文章来源": "新浪", "新闻链接": "https://example.com/n2"},
                ])
            @staticmethod
            def stock_individual_info_em(symbol):
                return _pd.DataFrame([
                    {"item": "股票代码", "value": "600519"},
                    {"item": "股票简称", "value": "贵州茅台"},
                    {"item": "总市值", "value": 1558000000000.0},
                ])

        _ak = _FakeAK()
        _HAVE_AK = True
        _hk_news_store = None  # 走 AKShare 退化路径

        # T1: A 股 K 线
        prices = get_prices("600519", "2026-06-01", "2026-06-18")
        if len(prices) != 2 or abs(prices[0].close - 1240.0) > 0.01:
            failures.append(f"T1 A 股 prices: {len(prices)} / {prices[0].close if prices else 'n/a'}")
        else:
            print(f"[T1] get_prices A 股: PASS ({len(prices)} bars, close={prices[0].close})")

        # T2: 港股 K 线
        prices_hk = get_prices("00700.HK", "2026-06-01", "2026-06-18")
        if len(prices_hk) != 1 or abs(prices_hk[0].close - 617.5) > 0.01:
            failures.append(f"T2 HK prices: {prices_hk}")
        else:
            print(f"[T2] get_prices HK: PASS (close={prices_hk[0].close})")

        # T3: A 股财务 (% 清洗)
        metrics = get_financial_metrics("600519", "2025-12-31", period="ttm", limit=5)
        if not metrics:
            failures.append("T3 metrics: empty")
        else:
            m = metrics[0]
            if m.return_on_equity is None or abs(m.return_on_equity - 0.325) > 0.001:
                failures.append(f"T3 ROE: {m.return_on_equity}")
            elif m.gross_margin is None or abs(m.gross_margin - 0.918) > 0.001:
                failures.append(f"T3 gross_margin: {m.gross_margin}")
            elif m.market_cap is None or abs(m.market_cap - 1.558e12) > 1e9:
                failures.append(f"T3 market_cap: {m.market_cap}")
            else:
                print(f"[T3] get_financial_metrics A 股 (% 清洗): PASS "
                      f"(ROE={m.return_on_equity:.1%}, cap={m.market_cap/1e8:.0f}亿)")

        # T4: 新闻
        news = get_company_news("600519", "2026-06-18", limit=10)
        if len(news) != 2 or "Q1" not in news[0].title:
            failures.append(f"T4 news: {len(news)}")
        else:
            print(f"[T4] get_company_news A 股: PASS ({len(news)} items)")

        # T5: 市值
        cap = get_market_cap("600519", "2026-06-18")
        if cap is None or abs(cap - 1.558e12) > 1e9:
            failures.append(f"T5 market_cap: {cap}")
        else:
            print(f"[T5] get_market_cap A 股: PASS ({cap/1e8:.0f}亿)")

        # T6: get_price_data DataFrame
        df = get_price_data("600519", "2026-06-01", "2026-06-18")
        if df is None or len(df) != 2 or "close" not in df.columns:
            failures.append(f"T6 df: {df}")
        else:
            print(f"[T6] get_price_data: PASS (DataFrame {len(df)}x{len(df.columns)})")

        # T7: 非法 ticker → [] (无 raise)
        if get_prices("totally_bad_xyz", "2026-06-01", "2026-06-18") != []:
            failures.append("T7 bad ticker")
        else:
            print("[T7] get_prices(bad) → [] no raise: PASS")

        # T8: AKShare 不可用 → fail-soft
        _HAVE_AK = False
        if (get_prices("600519", "2026-06-01", "2026-06-18") != [] or
            get_market_cap("600519", "2026-06-18") is not None or
            get_financial_metrics("600519", "2025-12-31") != []):
            failures.append("T8 fail-soft 不工作")
        else:
            print("[T8] AKShare 不可用 → fail-soft 空返回: PASS")

    finally:
        _ak = real_ak
        _HAVE_AK = real_have_ak
        _hk_news_store = real_hk_store

    if failures:
        print(f"\n[api_china v{__version__}] v1.0.2 FAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[api_china v{__version__}] v1.0.2 agent 接口 self-test PASS (8 groups)")


# ===========================================================================
# === v1.0.3: china_* agent 私有接口 (Phase 3.1) ============================
# ===========================================================================
#
# 4 个 china agent 直接 import 的函数 + 字段访问模式 (从源码反推):
#
#   china_capital_flow.py:
#     get_northbound_flow(limit=30)        → list 含 .total_net_buy
#     get_margin_trading(limit=30)         → list 含 .margin_balance
#     get_main_capital_flow(ticker, limit) → list 含 .main_net_inflow
#
#   china_policy.py + china_public_opinion.py:
#     get_public_opinion(ticker=None, limit=N) → list 含
#         .title / .content / .source / .date
#
#   china_sector_rotation.py:
#     get_sector_performance(limit=50)     → list 含
#         .sector_name / .change_pct / .turnover_rate
#     get_stock_sector_info(ticker)        → 单个含 .industry / .concepts
#
# 数据源 (AKShare 1.18+):
#   北向资金:   stock_hsgt_hist_em(symbol="北向资金")
#   融资融券:   stock_margin_account_info (沪深合计)
#   主力资金:   stock_individual_fund_flow
#   公开舆情:   stock_news_em (个股) + stock_news_main_cx (全市场财新)
#   板块行情:   stock_board_industry_name_em
#   个股板块:   stock_individual_info_em (行业字段)
#
# fail-soft: 失败返回空列表 / None,绝不让 agent 500.

from dataclasses import dataclass as _v103_dc, field as _v103_field, asdict as _v103_asdict


@_v103_dc
class NorthboundFlow:
    """北向资金日度净流入. 单位:亿元."""
    date: str = ""
    total_net_buy: Optional[float] = None
    sh_net_buy: Optional[float] = None       # 沪股通
    sz_net_buy: Optional[float] = None       # 深股通
    def model_dump(self): return _v103_asdict(self)


@_v103_dc
class MarginTrading:
    """融资融券日度余额. 单位:亿元."""
    date: str = ""
    margin_balance: Optional[float] = None         # 融资余额
    short_balance: Optional[float] = None          # 融券余额
    total_balance: Optional[float] = None          # 两融总余额
    def model_dump(self): return _v103_asdict(self)


@_v103_dc
class MainCapitalFlow:
    """单只票主力资金净流入 (日度). 单位:元."""
    date: str = ""
    ticker: str = ""
    main_net_inflow: Optional[float] = None        # 主力净流入
    super_large_net_inflow: Optional[float] = None # 超大单
    large_net_inflow: Optional[float] = None       # 大单
    close: Optional[float] = None                  # 当日收盘
    change_pct: Optional[float] = None             # 当日涨跌幅
    def model_dump(self): return _v103_asdict(self)


@_v103_dc
class PublicOpinionItem:
    """新闻/舆情条目 (用于 policy + public_opinion agent)."""
    title: str = ""
    content: str = ""
    source: str = ""
    date: str = ""
    url: str = ""
    sentiment: str = ""
    def model_dump(self): return _v103_asdict(self)


@_v103_dc
class SectorPerformance:
    """板块行情 (东财行业板块)."""
    sector_name: str = ""
    change_pct: Optional[float] = None
    turnover_rate: Optional[float] = None
    leader_stock: str = ""        # 领涨股
    sector_code: str = ""
    def model_dump(self): return _v103_asdict(self)


@_v103_dc
class StockSectorInfo:
    """个股所属行业 + 概念."""
    ticker: str = ""
    industry: str = ""             # 申万 / 东财行业
    concepts: list = _v103_field(default_factory=list)
    name: str = ""                 # 股票简称
    def model_dump(self): return _v103_asdict(self)


# ---------------------------------------------------------------------------
# 1. get_northbound_flow — 北向资金
# ---------------------------------------------------------------------------

def get_northbound_flow(limit: int = 30) -> list:
    """北向资金日度净流入 (最近 limit 天).

    AKShare: stock_hsgt_hist_em(symbol="北向资金")
    返回 DataFrame 列 (实测变体): "日期", "当日成交净买额", "当日资金流入" 等.
    单位: 亿元 (AKShare 已经是亿元).

    Returns:
        list[NorthboundFlow], 按日期升序 (旧→新), 方便 agent 用 [-N:] 取近期.
    """
    if not _HAVE_AK:
        return []
    ensure_no_proxy()
    try:
        df = _ak.stock_hsgt_hist_em(symbol="北向资金")
    except Exception as exc:
        _logger.warning("stock_hsgt_hist_em 失败: %s", exc)
        return []
    if df is None or len(df) == 0:
        return []

    # 找日期列
    date_col = None
    for cand in ("日期", "date", "DATE"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        return []

    # 取最后 limit 行
    try:
        tail = df.tail(limit)
    except Exception:
        tail = df

    out: list = []
    for _, row in tail.iterrows():
        d = _from_ak_date(_row_get(row, date_col, default=""))
        if not d:
            continue
        # 当日成交净买额 / 当日资金流入 都可能, 取第一个有值的
        net = _to_float(_row_get(
            row,
            "当日成交净买额", "当日资金流入", "净买额",
            "成交净买额", "net_buy",
        ))
        out.append(NorthboundFlow(
            date=d,
            total_net_buy=net,
            sh_net_buy=_to_float(_row_get(row, "沪股通", "沪股通净买额")),
            sz_net_buy=_to_float(_row_get(row, "深股通", "深股通净买额")),
        ))
    # 按日期升序 (agent 用 [-10:] 取最近)
    out.sort(key=lambda x: x.date)
    return out


# ---------------------------------------------------------------------------
# 2. get_margin_trading — 融资融券
# ---------------------------------------------------------------------------

def get_margin_trading(limit: int = 30) -> list:
    """融资融券余额时序 (沪深合计).

    AKShare: stock_margin_account_info()
    返回 DataFrame 列: 信用账户数 / 融资余额 / 融券余额 / 两融余额 等 (按日期).
    """
    if not _HAVE_AK:
        return []
    ensure_no_proxy()
    try:
        df = _ak.stock_margin_account_info()
    except Exception as exc:
        _logger.warning("stock_margin_account_info 失败: %s", exc)
        return []
    if df is None or len(df) == 0:
        return []

    date_col = None
    for cand in ("日期", "date", "交易日期", "DATE"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        return []

    try:
        tail = df.tail(limit)
    except Exception:
        tail = df

    out: list = []
    for _, row in tail.iterrows():
        d = _from_ak_date(_row_get(row, date_col, default=""))
        if not d:
            continue
        # 融资余额单位通常是亿元 (AKShare 视版本可能是元), 我们原值返回, agent 自适应
        out.append(MarginTrading(
            date=d,
            margin_balance=_to_float(_row_get(
                row, "融资余额", "financing_balance", "融资余额(亿元)",
            )),
            short_balance=_to_float(_row_get(
                row, "融券余额", "short_balance", "融券余额(亿元)",
            )),
            total_balance=_to_float(_row_get(
                row, "两融余额", "融资融券余额", "total_balance",
            )),
        ))
    out.sort(key=lambda x: x.date)
    return out


# ---------------------------------------------------------------------------
# 东财类接口的间歇性连接 drop 重试(病根#1:TLS 反爬偶发 RemoteDisconnected,
# 与 IP 无关、非确定性)。仅对连接级错误有限重试+指数退避;数据级错误/空结果不碰。
# 最坏情况:重试耗尽仍由各调用方 except → 返回 [](与改前一致,不回归)。
# ---------------------------------------------------------------------------
def _retry_conn(fn, *, tries: int = 3, base_delay: float = 0.6):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            s = str(exc)
            transient = (
                "Connection aborted" in s or "RemoteDisconnected" in s
                or "Connection reset" in s or "timed out" in s.lower()
                or "ConnectionError" in type(exc).__name__
            )
            if not transient or attempt == tries - 1:
                raise
            last = exc
            time.sleep(base_delay * (2 ** attempt))
    if last:
        raise last


# ---------------------------------------------------------------------------
# 3. get_main_capital_flow — 个股主力资金
# ---------------------------------------------------------------------------

def get_main_capital_flow(ticker: str, limit: int = 20) -> list:
    """个股主力资金日度净流入.

    AKShare: stock_individual_fund_flow(stock="600519", market="sh")
    返回 DataFrame 列 (实测): 日期/收盘价/涨跌幅/主力净流入-净额/...
    """
    if not _HAVE_AK:
        return []
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return []
    if market not in ("SH", "SZ", "BJ"):
        return []  # 港股该 API 不适用

    ensure_no_proxy()
    market_arg = market.lower()  # 'sh' / 'sz' / 'bj'
    try:
        df = _retry_conn(lambda: _ak.stock_individual_fund_flow(
            stock=_ak_a_symbol(norm), market=market_arg,
        ))
    except Exception as exc:
        _logger.warning("stock_individual_fund_flow(%s) 失败: %s", ticker, exc)
        return []
    if df is None or len(df) == 0:
        return []

    date_col = None
    for cand in ("日期", "date"):
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        return []

    try:
        tail = df.tail(limit)
    except Exception:
        tail = df

    out: list = []
    for _, row in tail.iterrows():
        d = _from_ak_date(_row_get(row, date_col, default=""))
        if not d:
            continue
        out.append(MainCapitalFlow(
            date=d,
            ticker=norm,
            main_net_inflow=_to_float(_row_get(
                row, "主力净流入-净额", "主力净流入", "main_net_inflow",
            )),
            super_large_net_inflow=_to_float(_row_get(
                row, "超大单净流入-净额", "超大单净流入",
            )),
            large_net_inflow=_to_float(_row_get(
                row, "大单净流入-净额", "大单净流入",
            )),
            close=_to_float(_row_get(row, "收盘价", "close")),
            change_pct=_to_float(_row_get(row, "涨跌幅")),
        ))
    out.sort(key=lambda x: x.date)
    return out


# ---------------------------------------------------------------------------
# 4. get_public_opinion — 全市场 / 个股新闻 + 舆情
# ---------------------------------------------------------------------------

def get_public_opinion(ticker: Optional[str] = None,
                        limit: int = 30) -> list:
    """全市场新闻 (ticker=None) 或个股舆情 (ticker=...) 聚合.

    全市场: ak.stock_news_main_cx() (财新网首页快讯)
    个股:   ak.stock_news_em (复用 get_company_news 的数据路径)

    与 get_company_news 区别:
      - get_company_news 返回 CompanyNews 模型 (用于 agent_bridge 路由)
      - get_public_opinion 返回 PublicOpinionItem (china_policy / public_opinion 私有)
    """
    if not _HAVE_AK:
        return []
    ensure_no_proxy()

    if ticker is None:
        # 全市场: 财新网快讯
        try:
            df = _ak.stock_news_main_cx()
        except Exception as exc:
            _logger.warning("stock_news_main_cx 失败: %s", exc)
            return []
        if df is None or len(df) == 0:
            return []
        try:
            head = df.head(limit)
        except Exception:
            head = df
        out: list = []
        for _, row in head.iterrows():
            out.append(PublicOpinionItem(
                title=str(_row_get(row, "summary", "标题", "新闻标题", default="")),
                content=str(_row_get(row, "interval_time", "content", "内容", default="")),
                source=str(_row_get(row, "tag", "source", "来源", default="财新网")),
                date=str(_row_get(row, "pub_time", "date", "发布时间", default="")),
                url=str(_row_get(row, "url", "链接", default="")),
            ))
        return out

    # 个股: 复用 stock_news_em
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return []
    sym = _ak_a_symbol(norm) if market != "HK" else _ak_hk_symbol(norm)
    try:
        df = _ak.stock_news_em(symbol=sym)
    except Exception as exc:
        _logger.warning("stock_news_em(%s) 失败: %s", ticker, exc)
        return []
    if df is None or len(df) == 0:
        return []

    try:
        head = df.head(limit)
    except Exception:
        head = df

    out = []
    for _, row in head.iterrows():
        out.append(PublicOpinionItem(
            title=str(_row_get(row, "新闻标题", "title", default="")),
            content=str(_row_get(row, "新闻内容", "content", default="")),
            source=str(_row_get(row, "文章来源", "source", default="")),
            date=str(_row_get(row, "发布时间", "date", default="")),
            url=str(_row_get(row, "新闻链接", "url", default="")),
        ))
    return out


# ---------------------------------------------------------------------------
# 5. get_sector_performance — 板块行情
# ---------------------------------------------------------------------------

def get_sector_performance(limit: int = 50) -> list:
    """东财行业板块行情, 按涨跌幅降序.

    AKShare: stock_board_industry_name_em()
    返回 DataFrame 列: 板块名称 / 涨跌幅 / 换手率 / 领涨股 等.
    """
    if not _HAVE_AK:
        return []
    ensure_no_proxy()
    try:
        df = _retry_conn(lambda: _ak.stock_board_industry_name_em())
    except Exception as exc:
        _logger.warning("stock_board_industry_name_em 失败: %s", exc)
        return []
    if df is None or len(df) == 0:
        return []

    out: list = []
    for _, row in df.iterrows():
        name = str(_row_get(row, "板块名称", "name", default=""))
        if not name:
            continue
        out.append(SectorPerformance(
            sector_name=name,
            change_pct=_to_float(_row_get(row, "涨跌幅", "change_pct")),
            turnover_rate=_to_float(_row_get(row, "换手率", "turnover_rate")),
            leader_stock=str(_row_get(row, "领涨股票", "领涨股", "leader", default="")),
            sector_code=str(_row_get(row, "板块代码", "code", default="")),
        ))
    # agent 自己排序, 我们不强制
    return out[:limit]


# ---------------------------------------------------------------------------
# 6. get_stock_sector_info — 个股所属行业 + 概念
# ---------------------------------------------------------------------------

def get_stock_sector_info(ticker: str) -> Optional["StockSectorInfo"]:
    """个股所属东财行业 + 简称.

    AKShare: stock_individual_info_em 返回中含 "行业" / "股票简称" 等 item.
    概念字段 AKShare 在该 endpoint 没有, 留空 list (agent 已经有 if check).
    """
    if not _HAVE_AK:
        return None
    try:
        norm, market = _normalize(ticker)
    except TickerParseError:
        return None
    if market not in ("SH", "SZ", "BJ"):
        return None  # 港股该 endpoint 不适用

    ensure_no_proxy()
    try:
        df = _ak.stock_individual_info_em(symbol=_ak_a_symbol(norm))
    except Exception as exc:
        _logger.warning("stock_individual_info_em(%s) 失败: %s", ticker, exc)
        return None
    if df is None or len(df) == 0:
        return None

    info = StockSectorInfo(ticker=norm)
    try:
        for _, row in df.iterrows():
            item = str(_row_get(row, "item", default=""))
            val = _row_get(row, "value", default="")
            if "行业" in item and not info.industry:
                info.industry = str(val) if val is not None else ""
            elif ("简称" in item or "股票简称" in item) and not info.name:
                info.name = str(val) if val is not None else ""
    except Exception:
        pass
    return info


# ===========================================================================
# v1.0.3 self-test (mock AKShare)
# ===========================================================================

def _selftest_v103() -> None:
    print(f"\n[api_china v{__version__}] v1.0.3 china_* agent 接口 self-test")
    failures: list = []
    global _ak, _HAVE_AK
    real_ak, real_have_ak = _ak, _HAVE_AK

    try:
        class _FakeAK103:
            @staticmethod
            def stock_hsgt_hist_em(symbol):
                return _pd.DataFrame([
                    {"日期": "2026-06-16", "当日成交净买额": 35.2},
                    {"日期": "2026-06-17", "当日成交净买额": 52.8},
                    {"日期": "2026-06-18", "当日成交净买额": -28.5},
                ])

            @staticmethod
            def stock_margin_account_info():
                return _pd.DataFrame([
                    {"日期": "2026-06-16", "融资余额": 18250.5, "融券余额": 825.2, "两融余额": 19075.7},
                    {"日期": "2026-06-17", "融资余额": 18380.2, "融券余额": 832.1, "两融余额": 19212.3},
                    {"日期": "2026-06-18", "融资余额": 18510.6, "融券余额": 840.5, "两融余额": 19351.1},
                ])

            @staticmethod
            def stock_individual_fund_flow(stock, market):
                return _pd.DataFrame([
                    {"日期": "2026-06-17", "收盘价": 1258, "涨跌幅": 0.5,
                     "主力净流入-净额": 1200000000, "超大单净流入-净额": 800000000,
                     "大单净流入-净额": 400000000},
                    {"日期": "2026-06-18", "收盘价": 1240, "涨跌幅": -1.43,
                     "主力净流入-净额": -350000000, "超大单净流入-净额": -200000000,
                     "大单净流入-净额": -150000000},
                ])

            @staticmethod
            def stock_news_main_cx():
                return _pd.DataFrame([
                    {"summary": "央行降准 0.5 个百分点", "tag": "财新网",
                     "pub_time": "2026-06-18 09:30", "url": "https://x.com/1"},
                    {"summary": "证监会发布退市新规", "tag": "财新网",
                     "pub_time": "2026-06-18 11:00", "url": "https://x.com/2"},
                    {"summary": "白酒板块全线走强", "tag": "财新网",
                     "pub_time": "2026-06-18 14:20", "url": "https://x.com/3"},
                ])

            @staticmethod
            def stock_news_em(symbol):
                return _pd.DataFrame([
                    {"新闻标题": "茅台业绩超预期", "新闻内容": "Q1 净利同比+12%",
                     "文章来源": "财华社", "发布时间": "2026-06-15",
                     "新闻链接": "https://x.com/n1"},
                ])

            @staticmethod
            def stock_board_industry_name_em():
                return _pd.DataFrame([
                    {"板块名称": "半导体", "涨跌幅": 3.84, "换手率": 4.2,
                     "领涨股票": "中芯国际", "板块代码": "BK0481"},
                    {"板块名称": "光模块", "涨跌幅": 5.12, "换手率": 6.8,
                     "领涨股票": "中际旭创", "板块代码": "BK1051"},
                    {"板块名称": "白酒", "涨跌幅": -1.32, "换手率": 1.1,
                     "领涨股票": "贵州茅台", "板块代码": "BK0438"},
                    {"板块名称": "煤炭", "涨跌幅": -2.85, "换手率": 0.8,
                     "领涨股票": "中国神华", "板块代码": "BK0437"},
                ])

            @staticmethod
            def stock_individual_info_em(symbol):
                return _pd.DataFrame([
                    {"item": "股票代码", "value": "600519"},
                    {"item": "股票简称", "value": "贵州茅台"},
                    {"item": "行业", "value": "白酒"},
                    {"item": "总市值", "value": 1558000000000.0},
                ])

        _ak = _FakeAK103()
        _HAVE_AK = True

        # T1: 北向资金
        nb = get_northbound_flow(limit=30)
        if len(nb) != 3:
            failures.append(f"T1 count: {len(nb)}")
        elif nb[-1].total_net_buy is None or abs(nb[-1].total_net_buy - (-28.5)) > 0.01:
            failures.append(f"T1 latest: {nb[-1].total_net_buy}")
        elif nb[0].date > nb[-1].date:
            failures.append("T1: 应升序排列")
        else:
            print(f"[T1] get_northbound_flow: PASS ({len(nb)} days, latest={nb[-1].total_net_buy}亿)")

        # T2: 融资融券
        mt = get_margin_trading(limit=30)
        if len(mt) != 3 or mt[-1].margin_balance is None or abs(mt[-1].margin_balance - 18510.6) > 0.01:
            failures.append(f"T2: {mt}")
        else:
            print(f"[T2] get_margin_trading: PASS ({len(mt)} days, latest={mt[-1].margin_balance}亿)")

        # T3: 主力资金 (单只票)
        flow = get_main_capital_flow("600519", limit=20)
        if len(flow) != 2:
            failures.append(f"T3 count: {len(flow)}")
        elif flow[-1].main_net_inflow is None or abs(flow[-1].main_net_inflow - (-350000000)) > 100:
            failures.append(f"T3 main: {flow[-1].main_net_inflow}")
        elif flow[-1].ticker != "600519.SH":
            failures.append(f"T3 ticker: {flow[-1].ticker}")
        else:
            print(f"[T3] get_main_capital_flow: PASS ({len(flow)} days, "
                  f"latest={flow[-1].main_net_inflow/1e8:.1f}亿)")

        # T3.HK: 港股该接口不适用 → 返回 []
        if get_main_capital_flow("00700.HK") != []:
            failures.append("T3.HK 应返回 []")
        else:
            print("[T3.HK] get_main_capital_flow(HK) → []: PASS")

        # T4: 全市场舆情
        op = get_public_opinion(ticker=None, limit=50)
        if len(op) != 3:
            failures.append(f"T4 count: {len(op)}")
        elif "降准" not in op[0].title:
            failures.append(f"T4 first title: {op[0].title}")
        elif op[0].source != "财新网":
            failures.append(f"T4 source: {op[0].source}")
        else:
            print(f"[T4] get_public_opinion (全市场): PASS ({len(op)} items)")

        # T4.stock: 个股舆情
        op_stock = get_public_opinion(ticker="600519", limit=30)
        if len(op_stock) != 1 or "茅台" not in op_stock[0].title:
            failures.append(f"T4.stock: {op_stock}")
        else:
            print(f"[T4.stock] get_public_opinion(600519): PASS ({len(op_stock)} items)")

        # T5: 板块行情
        sectors = get_sector_performance(limit=50)
        if len(sectors) != 4:
            failures.append(f"T5 count: {len(sectors)}")
        elif sectors[0].sector_name != "半导体":
            failures.append(f"T5 first: {sectors[0].sector_name}")
        elif sectors[0].change_pct is None or abs(sectors[0].change_pct - 3.84) > 0.01:
            failures.append(f"T5 change: {sectors[0].change_pct}")
        else:
            print(f"[T5] get_sector_performance: PASS ({len(sectors)} sectors, "
                  f"first={sectors[0].sector_name} {sectors[0].change_pct}%)")

        # T6: 个股板块
        info = get_stock_sector_info("600519")
        if info is None:
            failures.append("T6: 返回 None")
        elif info.industry != "白酒":
            failures.append(f"T6 industry: {info.industry}")
        elif info.name != "贵州茅台":
            failures.append(f"T6 name: {info.name}")
        else:
            print(f"[T6] get_stock_sector_info: PASS ({info.name} → {info.industry})")

        # T7: AKShare 不可用 → fail-soft
        _HAVE_AK = False
        if (get_northbound_flow() != [] or
            get_margin_trading() != [] or
            get_main_capital_flow("600519") != [] or
            get_public_opinion() != [] or
            get_sector_performance() != [] or
            get_stock_sector_info("600519") is not None):
            failures.append("T7: fail-soft 不工作")
        else:
            print("[T7] AKShare 不可用 → 6 函数全 fail-soft: PASS")

    finally:
        _ak = real_ak
        _HAVE_AK = real_have_ak

    if failures:
        print(f"\n[api_china v{__version__}] v1.0.3 FAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[api_china v{__version__}] v1.0.3 china_* agent 接口 self-test PASS (7 groups)")


if __name__ == "__main__":
    _selftest()
    _selftest_v102()
    _selftest_v103()


# =====================================================================
# Baostock primary layer (v2.0.0, appended 2026-06-22)  [marker:baostock_v2]
# ---------------------------------------------------------------------
# A 股 get_prices / get_financial_metrics / get_market_cap 改 Baostock 优先,
# 原 akshare/sina 路径降级为兜底(Baostock 查无此票时,如次新股 605788)。
# 港股/美股完全不变,走原函数。捕获原函数为 _orig_* 作兜底。
# 幂等:marker 已存在则不重复安装。
# =====================================================================
try:
    from tools import baostock_data as _bsd  # type: ignore
except ImportError:
    try:
        from src.tools import baostock_data as _bsd  # type: ignore
    except ImportError:
        _bsd = None  # type: ignore

if _bsd is not None and getattr(_bsd, "available", lambda: False)() \
        and not globals().get("_BAOSTOCK_V2_INSTALLED"):
    globals()["_BAOSTOCK_V2_INSTALLED"] = True

    _orig_get_prices = get_prices
    _orig_get_financial_metrics = get_financial_metrics
    _orig_get_market_cap = get_market_cap

    def get_prices(ticker, start_date, end_date, api_key=None):
        """A 股 Baostock 优先 → 空则原 akshare/sina 兜底。"""
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return []
        if market not in ("SH", "SZ", "BJ"):
            return _orig_get_prices(ticker, start_date, end_date, api_key=api_key)

        cache_key = f"{norm}_{start_date}_{end_date}"
        cached = _cache_v2.get_prices(cache_key)
        if cached:
            out = [x for x in (_safe_construct(_Price, **p) for p in cached) if x]
            if out:
                return out

        rows = _bsd.get_prices_dicts(norm, start_date, end_date)
        out = [x for x in (_safe_construct(_Price, **r) for r in rows) if x]
        if out:
            try:
                _cache_v2.set_prices(cache_key, [p.model_dump() for p in out])
            except Exception:
                pass
            return out

        # Baostock 查无此票(次新股未覆盖)→ 原 akshare/sina(含 sinafix 降级)
        return _orig_get_prices(ticker, start_date, end_date, api_key=api_key)

    def get_financial_metrics(ticker, end_date, period="ttm", limit=10, api_key=None):
        """A 股 Baostock 6 表反推 → 空则原路径兜底。"""
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return []
        if market not in ("SH", "SZ", "BJ"):
            return _orig_get_financial_metrics(ticker, end_date, period=period,
                                               limit=limit, api_key=api_key)

        cache_key = f"{norm}_{period}_{end_date}_{limit}"
        cached = _cache_v2.get_financial_metrics(cache_key)
        if cached:
            out = [x for x in (_safe_construct(_FinancialMetrics, **m) for m in cached) if x]
            if out:
                return out

        asof = end_date or _datetime.now().strftime("%Y-%m-%d")
        quarters = _bsd.get_quarters(norm, asof, limit=limit)
        if not quarters:
            return _orig_get_financial_metrics(ticker, end_date, period=period,
                                               limit=limit, api_key=api_key)

        val = _bsd.latest_valuation(norm, asof)
        cap = _bsd.market_cap(norm, asof)
        out = []
        for i, blk in enumerate(quarters):
            rec = _bsd.metrics_from_block(blk)
            rec["ticker"] = norm
            rec["report_period"] = blk.get("statDate", "")
            rec["period"] = period
            rec["currency"] = "CNY"
            if i == 0:  # 最新一期附加日频估值 + 市值
                rec["price_to_earnings_ratio"] = val.get("pe")
                rec["price_to_book_ratio"] = val.get("pb")
                rec["price_to_sales_ratio"] = val.get("ps")
                rec["market_cap"] = cap
                close, pb = val.get("close"), val.get("pb")
                rec["book_value_per_share"] = (close / pb) if (close and pb) else None
                eg, pe = rec.get("earnings_growth"), val.get("pe")
                rec["peg_ratio"] = (pe / (eg * 100)) if (pe and eg and eg > 0) else None
            m = _safe_construct(_FinancialMetrics, **rec)
            if m:
                out.append(m)
        if out:
            try:
                _cache_v2.set_financial_metrics(cache_key, [m.model_dump() for m in out])
            except Exception:
                pass
        return out

    def get_market_cap(ticker, end_date, api_key=None):
        """A 股市值 = totalShare × 最近收盘价(Baostock)→ 空则原路径兜底。"""
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return None
        if market in ("SH", "SZ", "BJ"):
            cap = _bsd.market_cap(norm, end_date or _datetime.now().strftime("%Y-%m-%d"))
            if cap is not None:
                return cap
        return _orig_get_market_cap(ticker, end_date, api_key=api_key)

    _logger.info("[api_china] Baostock v2.0 primary layer 已安装 (A股价格/财报/市值)")


# =====================================================================
# DataSource 路由层 (Phase 1 Part B, v1.0.0, 2026-06-22)  [marker:datasource_v1]
# ---------------------------------------------------------------------
# 把 get_prices/get_financial_metrics/get_market_cap 改为经 DataSourceRouter
# 多源失效转移(baostock 主 + akshare 兜底),带熔断 + 健康观测。
# 消除单点:baostock 熔断/不可用时,akshare 自动顶上,整个 run 不中断。
# 港股/美股仍走原 akshare。幂等:marker 已存在则跳过。
# =====================================================================
try:
    from tools import datasource as _ds
except ImportError:
    try:
        from src.tools import datasource as _ds
    except ImportError:
        _ds = None

if _ds is not None and not globals().get("_DATASOURCE_V1_INSTALLED"):
    globals()["_DATASOURCE_V1_INSTALLED"] = True
    from datetime import datetime as _dt_ds
    _FG = _ds.FieldGroup
    _TIER_A = _ds.base.TIER_A
    _TIER_C = _ds.base.TIER_C

    # 纯 akshare 原函数(Phase 0 捕获的 _orig_*;若 Phase 0 没跑,则当前 get_* 即纯版)
    _AK_prices = globals().get("_orig_get_prices", get_prices)
    _AK_fm = globals().get("_orig_get_financial_metrics", get_financial_metrics)
    _AK_mc = globals().get("_orig_get_market_cap", get_market_cap)

    def _now_ds():
        return _dt_ds.now().strftime("%Y-%m-%d")

    # ---------- baostock provider funcs(返回 dict）----------
    def _bs_prices(norm, start_date, end_date, **kw):
        return _bsd.get_prices_dicts(norm, start_date, end_date)

    def _bs_valuation(norm, end_date, **kw):
        asof = end_date or _now_ds()
        val = dict(_bsd.latest_valuation(norm, asof) or {})
        if val:
            # 内联市值(避免 market_cap() 重复跑 get_quarters)
            qs = _bsd.get_quarters(norm, asof, limit=1)
            ts = None
            if qs:
                try:
                    ts = float(qs[0].get("profit", {}).get("totalShare") or 0) or None
                except Exception:
                    ts = None
            close = val.get("close")
            val["market_cap"] = (ts * close) if (ts and close) else None
        return val

    def _bs_financials(norm, end_date, period="ttm", limit=10, **kw):
        asof = end_date or _now_ds()
        quarters = _bsd.get_quarters(norm, asof, limit=limit)
        if not quarters:
            return []
        val = _bsd.latest_valuation(norm, asof) or {}
        ts = None
        try:
            ts = float(quarters[0].get("profit", {}).get("totalShare") or 0) or None
        except Exception:
            ts = None
        close = val.get("close")
        cap = (ts * close) if (ts and close) else None
        out = []
        for i, blk in enumerate(quarters):
            rec = _bsd.metrics_from_block(blk)
            rec["ticker"] = norm
            rec["report_period"] = blk.get("statDate", "")
            rec["period"] = period
            rec["currency"] = "CNY"
            if i == 0:
                rec["price_to_earnings_ratio"] = val.get("pe")
                rec["price_to_book_ratio"] = val.get("pb")
                rec["price_to_sales_ratio"] = val.get("ps")
                rec["market_cap"] = cap
                cl, pb = val.get("close"), val.get("pb")
                rec["book_value_per_share"] = (cl / pb) if (cl and pb) else None
                eg, pe = rec.get("earnings_growth"), val.get("pe")
                rec["peg_ratio"] = (pe / (eg * 100)) if (pe and eg and eg > 0) else None
            out.append(rec)
        return out

    # ---------- akshare provider funcs(原函数 → model_dump dict）----------
    def _ak_prices_d(norm, start_date, end_date, ticker=None, **kw):
        res = _AK_prices(ticker or norm, start_date, end_date)
        return [p.model_dump() for p in res] if res else []

    def _ak_financials_d(norm, end_date, period="ttm", limit=10, ticker=None, **kw):
        res = _AK_fm(ticker or norm, end_date, period=period, limit=limit)
        return [m.model_dump() for m in res] if res else []

    def _ak_valuation_d(norm, end_date, ticker=None, **kw):
        mc = _AK_mc(ticker or norm, end_date)
        return {"market_cap": mc} if mc else {}

    # ---------- 组装 Router ----------
    _router = _ds.DataSourceRouter(
        chains={
            _FG.PRICE:       ["baostock", "akshare"],
            _FG.VALUATION:   ["baostock", "akshare"],
            _FG.RATIO_FIN:   ["baostock", "akshare"],
            _FG.ABS_BALANCE: ["baostock", "akshare"],
        },
        is_cn_ip=lambda: True,
    )
    _router.register(_ds.CallableProvider(
        "baostock", _TIER_A,
        [_FG.PRICE, _FG.VALUATION, _FG.RATIO_FIN, _FG.ABS_BALANCE],
        {"prices": _bs_prices, "valuation": _bs_valuation, "financials": _bs_financials},
        available_fn=_bsd.available))
    _router.register(_ds.CallableProvider(
        "akshare", _TIER_C,
        [_FG.PRICE, _FG.VALUATION, _FG.RATIO_FIN, _FG.ABS_BALANCE],
        {"prices": _ak_prices_d, "valuation": _ak_valuation_d, "financials": _ak_financials_d}))

    # 暴露给外部排障/飞书
    def datasource_status():
        return _router.status()

    # ---------- shell:三个对外函数改为经 Router ----------
    def get_prices(ticker, start_date, end_date, api_key=None):
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return []
        if market not in ("SH", "SZ", "BJ"):
            return _AK_prices(ticker, start_date, end_date)
        cache_key = f"{norm}_{start_date}_{end_date}"
        cached = _cache_v2.get_prices(cache_key)
        if cached:
            out = [x for x in (_safe_construct(_Price, **p) for p in cached) if x]
            if out:
                return out
        dicts, _src = _router.resolve(_FG.PRICE, norm=norm, ticker=ticker,
                                      start_date=start_date, end_date=end_date)
        if not dicts:
            return []
        out = [x for x in (_safe_construct(_Price, **d) for d in dicts) if x]
        if out:
            try:
                _cache_v2.set_prices(cache_key, [p.model_dump() for p in out])
            except Exception:
                pass
        return out

    def get_financial_metrics(ticker, end_date, period="ttm", limit=10, api_key=None):
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return []
        if market not in ("SH", "SZ", "BJ"):
            return _AK_fm(ticker, end_date, period=period, limit=limit)
        cache_key = f"{norm}_{period}_{end_date}_{limit}"
        cached = _cache_v2.get_financial_metrics(cache_key)
        if cached:
            out = [x for x in (_safe_construct(_FinancialMetrics, **m) for m in cached) if x]
            if out:
                return out
        dicts, _src = _router.resolve(_FG.RATIO_FIN, norm=norm, ticker=ticker,
                                      end_date=end_date, period=period, limit=limit)
        if not dicts:
            return []
        out = [x for x in (_safe_construct(_FinancialMetrics, **d) for d in dicts) if x]
        if out:
            try:
                _cache_v2.set_financial_metrics(cache_key, [m.model_dump() for m in out])
            except Exception:
                pass
        return out

    def get_market_cap(ticker, end_date, api_key=None):
        try:
            norm, market = _normalize(ticker)
        except TickerParseError:
            return None
        if market not in ("SH", "SZ", "BJ"):
            return _AK_mc(ticker, end_date)
        val, _src = _router.resolve(_FG.VALUATION, norm=norm, ticker=ticker, end_date=end_date)
        if val and val.get("market_cap") is not None:
            return val["market_cap"]
        return _AK_mc(ticker, end_date)

    _logger.info("[api_china] DataSource v1.0 路由层已安装 (baostock+akshare 失效转移+熔断)")


# =====================================================================
# 飞书告警接线 (Phase 1, v1.0.0)  [marker:feishu_alert_v1]
# 把飞书 webhook 注入已构建的 HealthReporter;未配 AIHF_FEISHU_WEBHOOK 则静默。
# =====================================================================
if globals().get("_router") is not None and not globals().get("_FEISHU_ALERT_INSTALLED"):
    try:
        try:
            from tools.datasource import feishu_alert as _fa_mod
        except ImportError:
            from src.tools.datasource import feishu_alert as _fa_mod
        _fa_wh = _fa_mod.make_feishu_webhook()
        if _fa_wh is not None:
            _router.health._webhook = _fa_wh
            globals()["_FEISHU_ALERT_INSTALLED"] = True
            _logger.info("[api_china] 飞书告警已接入 HealthReporter")
        else:
            _logger.info("[api_china] 未配置 AIHF_FEISHU_WEBHOOK,飞书告警静默")
    except Exception as _e:
        _logger.warning("[api_china] 飞书告警接线失败: %s", _e)


# mootdx/pytdx 价格源 (Phase 3, pytdx 直连 TCP, v2.0.0, 2026-06-23)  [marker:mootdx_v3]
# ---------------------------------------------------------------------
# 在既有 DataSourceRouter(datasource_v1)的 PRICE 链里,把 pytdx 直连源插到
# baostock 之后、akshare 之前 → baostock → mootdx → akshare。
# 只服务 PRICE;needs_cn_ip=True(海外 Router 自动跳过);不复权,仅兜底。
# 复用 datasource_v1 已构建的 _router(不重定义 get_prices —— 它已经走 _router.resolve)。
# 幂等:marker 已存在则不重复安装;无 datasource_v1 / 未装 pytdx → 惰性跳过。
# =====================================================================
try:
    from tools import mootdx_data as _mdx
except ImportError:
    try:
        from src.tools import mootdx_data as _mdx
    except ImportError:
        _mdx = None

if (_mdx is not None
        and globals().get("_DATASOURCE_V1_INSTALLED")
        and "_router" in globals()
        and not globals().get("_MOOTDX_V3_INSTALLED")):
    globals()["_MOOTDX_V3_INSTALLED"] = True
    _FG_m = globals()["_FG"]
    _ds_m = globals()["_ds"]
    _TIER_A_m = globals().get("_TIER_A", _ds_m.base.TIER_A)

    def _mootdx_prices(norm, start_date, end_date, **kw):
        # 连接级故障 → 上抛(Router 记熔断隔离本源);查无/不支持 → [](Router 转下一源)
        return _mdx.get_prices_dicts(norm, start_date, end_date)

    _router.register(_ds_m.CallableProvider(
        "mootdx", _TIER_A_m,
        [_FG_m.PRICE],
        {"prices": _mootdx_prices},
        available_fn=_mdx.available,
        needs_cn_ip=True))

    # 把 mootdx 插到 baostock 之后(akshare 永远兜底在最后)
    _cur_chain = list(_router.chains.get(_FG_m.PRICE, ["baostock", "akshare"]))
    if "mootdx" not in _cur_chain:
        _new_chain = []
        for _nm in _cur_chain:
            _new_chain.append(_nm)
            if _nm == "baostock":
                _new_chain.append("mootdx")
        if "mootdx" not in _new_chain:        # 链里本来没 baostock 的兜底情形
            _new_chain.insert(0, "mootdx")
        _router.set_chain(_FG_m.PRICE, _new_chain)

