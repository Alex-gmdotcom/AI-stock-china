"""
tools/api_china.py — A 股 & 港股报价数据统一接口
=================================================

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

__version__ = "1.0.2"

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

    try:
        if market == "HK":
            df = _ak.stock_hk_hist(symbol=_ak_hk_symbol(norm), period="daily",
                                    start_date=ak_start, end_date=ak_end, adjust="qfq")
        elif market in ("SH", "SZ", "BJ"):
            df = _ak.stock_zh_a_hist(symbol=_ak_a_symbol(norm), period="daily",
                                      start_date=ak_start, end_date=ak_end, adjust="qfq")
        else:
            return []
    except Exception as exc:
        _logger.warning("get_prices(%s) AKShare 失败: %s", ticker, exc)
        return []

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


if __name__ == "__main__":
    _selftest()
    _selftest_v102()
