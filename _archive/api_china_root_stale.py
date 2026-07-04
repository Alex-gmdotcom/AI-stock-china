"""
tools/api_china.py — A 股 & 港股报价数据统一接口
=================================================

v1.0.0 (2026-06-18, Phase 2 Step 1)

Fallback chain (基于 2026-06-17 真实环境探测,见 0617 总结 §3):
  1. tencent_qt        qt.gtimg.cn                 (首选,最快,GBK 编码)
  2. sina_hq           hq.sinajs.cn                (备用,需 Referer header)
  3. eastmoney_spot    82.push2.eastmoney.com      (兜底,spot 全表查找)

⚠️ DO NOT 使用 push2.eastmoney.com/api/qt/stock/get —
   在 Alex 网络环境下 RemoteDisconnected,已知不可用。

设计原则:
  - 单一对外入口  quote(ticker)  /  batch_quote(tickers)
  - 自动 ticker 规范化 (A 股 / 港股 / ChiNext / STAR / 北交所)
  - 应用 markets.proxy 的 NO_PROXY 注入 (绕开 TUN-mode 系统代理)
  - 使用 tools.data_fallback 的 call_with_fallback 原语
  - fail-loud: 三源全失败抛 NoDataSourceAvailable
  - 可追溯: 每条 Quote 自带 source 字段 (审计 / 调试)
  - 内置 spot 表 30s 缓存 (batch_quote 优化)

不变量对齐 (引自 0617 总结):
  - I1.2  数据可追溯实际使用源 (Quote.source)
  - I6.4  代理配置不上传 (依赖 markets.proxy 的 ensure_no_proxy)
  - 兼容 strategy.three_categories 的 ticker 内部形式 "600519.SH"
"""

from __future__ import annotations

import re
import time
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional

import requests

__version__ = "1.0.0"

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
# Ticker 规范化
# ---------------------------------------------------------------------------

# 内部规范形式: "{code}.{market}"  例: "600519.SH" "00700.HK"
_RE_DOTTED = re.compile(r"^(\d{4,6})\.(SH|SZ|BJ|HK)$", re.IGNORECASE)
_RE_PREFIXED = re.compile(r"^(sh|sz|bj|hk)(\d{4,6})$", re.IGNORECASE)


def _infer_a_market(code6: str) -> str:
    """6 位 A 股代码 → 'SH' / 'SZ' / 'BJ'.

    规则参考 (A 股):
      - 6 开头:沪 A 主板         → SH
      - 688 开头:科创板          → SH
      - 000 / 001 / 002 / 003:深 A 主板 / 中小板  → SZ
      - 300 / 301:创业板         → SZ
      - 4 / 8 开头:北交所        → BJ
      - 9 开头:B 股 (此处归 SH/SZ 视尾码,简化全归 SH)
    """
    if not code6.isdigit() or len(code6) != 6:
        raise TickerParseError(f"A 股代码必须是 6 位数字: {code6!r}")
    head1 = code6[0]
    head3 = code6[:3]
    if head3 == "688":
        return "SH"
    if head1 == "6":
        return "SH"
    if head3 in {"000", "001", "002", "003"} or head3.startswith("00"):
        return "SZ"
    if head3 in {"300", "301"}:
        return "SZ"
    if head1 in {"4", "8"}:
        return "BJ"
    if head1 == "9":
        return "SH"
    raise TickerParseError(f"无法推断 A 股代码所属市场: {code6}")


def _normalize(ticker: str) -> tuple[str, str]:
    """ticker → (规范形式, market).

    支持所有常见输入:
      "600519"        → ("600519.SH", "SH")
      "600519.SH"     → ("600519.SH", "SH")
      "sh600519"      → ("600519.SH", "SH")
      "300750"        → ("300750.SZ", "SZ")
      "00700.HK"      → ("00700.HK",  "HK")
      "0700.HK"       → ("00700.HK",  "HK")    # 自动补齐 5 位
      "hk00700"       → ("00700.HK",  "HK")
      "430047"        → ("430047.BJ", "BJ")
    """
    if not ticker or not isinstance(ticker, str):
        raise TickerParseError(f"ticker 必须为非空字符串: {ticker!r}")
    raw = ticker.strip()

    # 形式 1: "600519.SH"
    m = _RE_DOTTED.match(raw)
    if m:
        code, market = m.group(1), m.group(2).upper()
        if market == "HK":
            code = code.zfill(5)
        else:
            code = code.zfill(6)
        return f"{code}.{market}", market

    # 形式 2: "sh600519"
    m = _RE_PREFIXED.match(raw)
    if m:
        market = m.group(1).upper()
        code = m.group(2)
        if market == "HK":
            code = code.zfill(5)
        else:
            code = code.zfill(6)
        return f"{code}.{market}", market

    # 形式 3: 纯数字
    if raw.isdigit():
        # 4-5 位 → 视为港股
        if len(raw) in (4, 5):
            code = raw.zfill(5)
            return f"{code}.HK", "HK"
        # 6 位 → A 股
        if len(raw) == 6:
            market = _infer_a_market(raw)
            return f"{raw}.{market}", market

    raise TickerParseError(f"无法识别的 ticker 格式: {ticker!r}")


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
    for bad in ["", "abc", "12", "12345678", None]:
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


if __name__ == "__main__":
    _selftest()
