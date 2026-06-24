"""
Ticker parsing, validation, and market detection.

v1.0.1 (2026-06-18, Phase 2 Step 0 — 加 CN_BJ 北交所支持)

Supports six market families:
  - CN_MAIN:   Shanghai main board (600xxx, 601xxx, 603xxx, 605xxx)
  - CN_SZ:     Shenzhen main board (000xxx, 001xxx, 002xxx, 003xxx)
  - CN_CHINEXT: ChiNext / 创业板 (300xxx, 301xxx)
  - CN_STAR:   STAR Market / 科创板 (688xxx, 689xxx)
  - CN_BJ:     Beijing Stock Exchange / 北交所 (43xxxx, 83xxxx, 87xxxx, 88xxxx)
  - HK:        Hong Kong (5-digit, e.g. 00700, 09988)
  - US:        US equities (alphabetic tickers, e.g. AAPL)

Ticker formats accepted:
  - "600519"       → auto-detect as CN_MAIN (SH)
  - "600519.SH"    → explicit Shanghai
  - "000858.SZ"    → explicit Shenzhen
  - "300750.SZ"    → ChiNext
  - "688981.SH"    → STAR Market
  - "430047.BJ"    → Beijing Stock Exchange
  - "00700.HK"     → Hong Kong
  - "AAPL"         → US
"""

from __future__ import annotations

import re
from enum import Enum
from dataclasses import dataclass

__version__ = "1.0.1"


class MarketType(str, Enum):
    """Enumeration of supported markets."""
    CN_MAIN = "cn_main"         # 沪深主板
    CN_SZ = "cn_sz"             # 深圳主板
    CN_CHINEXT = "cn_chinext"   # 创业板
    CN_STAR = "cn_star"         # 科创板
    CN_BJ = "cn_bj"             # 北交所 (v1.0.1)
    HK = "hk"                   # 港股
    US = "us"                   # 美股

    @property
    def is_china(self) -> bool:
        return self in (MarketType.CN_MAIN, MarketType.CN_SZ,
                        MarketType.CN_CHINEXT, MarketType.CN_STAR,
                        MarketType.CN_BJ)

    @property
    def is_hk(self) -> bool:
        return self == MarketType.HK

    @property
    def exchange_suffix(self) -> str:
        """Return the conventional exchange suffix."""
        return _MARKET_TO_SUFFIX.get(self, "")

    @property
    def display_name(self) -> str:
        return _MARKET_DISPLAY_NAMES.get(self, self.value)


_MARKET_DISPLAY_NAMES = {
    MarketType.CN_MAIN: "Shanghai Main Board (沪市主板)",
    MarketType.CN_SZ: "Shenzhen Main Board (深市主板)",
    MarketType.CN_CHINEXT: "ChiNext (创业板)",
    MarketType.CN_STAR: "STAR Market (科创板)",
    MarketType.CN_BJ: "Beijing Stock Exchange (北交所)",
    MarketType.HK: "HKEX (港股)",
    MarketType.US: "US Equities",
}

_MARKET_TO_SUFFIX = {
    MarketType.CN_MAIN: "SH",
    MarketType.CN_SZ: "SZ",
    MarketType.CN_CHINEXT: "SZ",
    MarketType.CN_STAR: "SH",
    MarketType.CN_BJ: "BJ",
    MarketType.HK: "HK",
    MarketType.US: "",
}

# Prefix → MarketType mapping for auto-detection
_CN_PREFIX_MAP: list[tuple[re.Pattern, MarketType]] = [
    (re.compile(r"^(600|601|603|605)\d{3}$"), MarketType.CN_MAIN),
    (re.compile(r"^(000|001|002|003)\d{3}$"), MarketType.CN_SZ),
    (re.compile(r"^(300|301)\d{3}$"), MarketType.CN_CHINEXT),
    (re.compile(r"^(688|689)\d{3}$"), MarketType.CN_STAR),
    # v1.0.1: 北交所 — 43xxxx / 83xxxx / 87xxxx / 88xxxx
    (re.compile(r"^(43|83|87|88)\d{4}$"), MarketType.CN_BJ),
]

# Suffix → Exchange mapping
_SUFFIX_TO_EXCHANGE = {
    "SH": "SH",
    "SS": "SH",   # Yahoo-style
    "SZ": "SZ",
    "BJ": "BJ",   # v1.0.1: 北交所
    "HK": "HK",
}


@dataclass(frozen=True)
class TickerInfo:
    """Parsed ticker with market metadata."""
    raw: str                # Original input string
    code: str               # Numeric/alpha code (e.g. "600519", "00700", "AAPL")
    market: MarketType      # Detected market
    exchange: str           # Exchange suffix (SH, SZ, HK, "")
    full_ticker: str        # Canonical form (e.g. "600519.SH", "00700.HK", "AAPL")

    @property
    def akshare_code(self) -> str:
        """Return the code format expected by AKShare functions."""
        if self.market.is_china:
            return self.code  # AKShare uses bare 6-digit code
        if self.market.is_hk:
            return self.code  # AKShare uses bare 5-digit code
        return self.code

    @property
    def display_name(self) -> str:
        return f"{self.full_ticker} ({self.market.display_name})"


def detect_market(code: str) -> MarketType | None:
    """Detect market from a bare numeric code (no suffix)."""
    for pattern, market in _CN_PREFIX_MAP:
        if pattern.match(code):
            return market
    # 5-digit zero-padded → likely HK
    if re.match(r"^\d{5}$", code):
        return MarketType.HK
    return None


def parse_ticker(raw: str) -> TickerInfo:
    """
    Parse a ticker string into a TickerInfo.

    Accepts formats:
      "600519"      → CN_MAIN, SH
      "600519.SH"   → CN_MAIN, SH
      "300750.SZ"   → CN_CHINEXT, SZ
      "00700.HK"    → HK, HK
      "AAPL"        → US, ""

    Raises ValueError if the ticker cannot be parsed.
    """
    raw = raw.strip().upper()

    # Try splitting on dot
    if "." in raw:
        parts = raw.split(".", 1)
        code = parts[0]
        suffix = parts[1]

        exchange = _SUFFIX_TO_EXCHANGE.get(suffix)
        if exchange is None:
            raise ValueError(
                f"Unknown exchange suffix '{suffix}' in ticker '{raw}'. "
                f"Supported: {', '.join(_SUFFIX_TO_EXCHANGE.keys())}"
            )

        # v1.0.1: 根据 suffix 自动补齐 (HK 5 位, 其他 6 位)
        if exchange == "HK":
            code = code.zfill(5)
        elif code.isdigit():
            code = code.zfill(6)

        # Detect market from code prefix
        market = detect_market(code)
        if market is None:
            raise ValueError(
                f"Cannot determine market for code '{code}' in ticker '{raw}'."
            )

        return TickerInfo(
            raw=raw,
            code=code,
            market=market,
            exchange=exchange,
            full_ticker=f"{code}.{exchange}",
        )

    # No dot — try auto-detection
    # Pure alphabetic → US
    if re.match(r"^[A-Z]{1,5}$", raw):
        return TickerInfo(
            raw=raw, code=raw, market=MarketType.US,
            exchange="", full_ticker=raw,
        )

    # v1.0.1: Prefixed form  "SH600519" / "sz000001" / "hk00700" / "bj430047"
    m = re.match(r"^(SH|SZ|BJ|HK)(\d{4,6})$", raw)
    if m:
        suffix = m.group(1)
        code = m.group(2)
        exchange = _SUFFIX_TO_EXCHANGE.get(suffix)
        if exchange is None:
            raise ValueError(f"Unknown exchange prefix '{suffix}' in ticker '{raw}'.")
        if exchange == "HK":
            code = code.zfill(5)
        else:
            code = code.zfill(6)
        market = detect_market(code)
        if market is None:
            raise ValueError(f"Cannot determine market for prefixed ticker '{raw}'.")
        return TickerInfo(
            raw=raw,
            code=code,
            market=market,
            exchange=exchange,
            full_ticker=f"{code}.{exchange}",
        )

    # Pure numeric → CN or HK
    if re.match(r"^\d+$", raw):
        # Pad to 6 digits for CN, 5 for HK
        if len(raw) <= 5:
            code = raw.zfill(5)
            market = detect_market(code)
            if market and market.is_hk:
                exchange = "HK"
                return TickerInfo(
                    raw=raw, code=code, market=market,
                    exchange=exchange, full_ticker=f"{code}.{exchange}",
                )
        if len(raw) <= 6:
            code = raw.zfill(6)
            market = detect_market(code)
            if market:
                exchange = _MARKET_TO_SUFFIX[market]
                return TickerInfo(
                    raw=raw, code=code, market=market,
                    exchange=exchange, full_ticker=f"{code}.{exchange}",
                )

    raise ValueError(
        f"Cannot parse ticker '{raw}'. "
        f"Expected formats: 600519, 600519.SH, 00700.HK, AAPL"
    )


def normalize_ticker(raw: str) -> str:
    """Return the canonical full_ticker string for a raw input."""
    return parse_ticker(raw).full_ticker


def is_china_ticker(raw: str) -> bool:
    """Check if a ticker belongs to any China A-share market."""
    try:
        return parse_ticker(raw).market.is_china
    except ValueError:
        return False


def is_hk_ticker(raw: str) -> bool:
    """Check if a ticker belongs to Hong Kong market."""
    try:
        return parse_ticker(raw).market.is_hk
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Self-test (v1.0.1)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[markets.ticker v{__version__}] self-test")
    failures: list[str] = []

    # 原有市场仍正确
    cases = [
        ("600519",     "600519.SH",  MarketType.CN_MAIN),
        ("600519.SH",  "600519.SH",  MarketType.CN_MAIN),
        ("000001.SZ",  "000001.SZ",  MarketType.CN_SZ),
        ("300750",     "300750.SZ",  MarketType.CN_CHINEXT),
        ("688008",     "688008.SH",  MarketType.CN_STAR),
        ("00700.HK",   "00700.HK",   MarketType.HK),
        ("AAPL",       "AAPL",       MarketType.US),
        # v1.0.1 新增 BJ
        ("430047",     "430047.BJ",  MarketType.CN_BJ),
        ("430047.BJ",  "430047.BJ",  MarketType.CN_BJ),
        ("830799",     "830799.BJ",  MarketType.CN_BJ),
        ("870866",     "870866.BJ",  MarketType.CN_BJ),
        ("889999",     "889999.BJ",  MarketType.CN_BJ),
        # v1.0.1 新增 prefix 形式 (sh600519 / SH600519 / hk00700 等)
        ("sh600519",   "600519.SH",  MarketType.CN_MAIN),
        ("SH600519",   "600519.SH",  MarketType.CN_MAIN),
        ("sz000001",   "000001.SZ",  MarketType.CN_SZ),
        ("hk00700",    "00700.HK",   MarketType.HK),
        ("bj430047",   "430047.BJ",  MarketType.CN_BJ),
        # v1.0.1 dotted form auto-pad
        ("0700.HK",    "00700.HK",   MarketType.HK),   # HK 4 位补到 5 位
        ("700.HK",     "00700.HK",   MarketType.HK),   # HK 3 位补到 5 位
    ]
    for raw, expected_full, expected_market in cases:
        try:
            info = parse_ticker(raw)
            if info.full_ticker != expected_full:
                failures.append(f"{raw!r} full_ticker {info.full_ticker} ≠ {expected_full}")
            if info.market != expected_market:
                failures.append(f"{raw!r} market {info.market} ≠ {expected_market}")
        except Exception as e:
            failures.append(f"{raw!r} 抛错: {type(e).__name__}: {e}")
    if not failures:
        print(f"[T1] parse_ticker ({len(cases)} cases incl. BJ): PASS")

    # BJ market helpers
    assert is_china_ticker("430047"), "BJ 应算 China"
    assert is_china_ticker("430047.BJ"), "BJ.BJ 应算 China"
    assert not is_hk_ticker("430047"), "BJ 不应算 HK"
    print("[T2] is_china_ticker / is_hk_ticker for BJ: PASS")

    # display name + suffix
    bj_info = parse_ticker("430047")
    assert "北交所" in bj_info.market.display_name, bj_info.market.display_name
    assert bj_info.market.exchange_suffix == "BJ"
    assert bj_info.market.is_china and not bj_info.market.is_hk
    print(f"[T3] BJ metadata: {bj_info.display_name}: PASS")

    if failures:
        print(f"\n[markets.ticker] self-test FAILED ({len(failures)} 项):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[markets.ticker v{__version__}] self-test PASS (3 groups, {len(cases)} ticker cases)")
