"""
v3.2 腾讯行情数据源（qt.gtimg.cn）— 个股兜底 + 全球指数温度计。

腾讯行情对海外 IP 远比东财友好，承担两个角色：
1. 东财(AKShare)失败时的个股实时兜底（v3.1 起）
2. 全球指数温度计：A股核心指数 + 恒指/恒生科技 + 美股三大（v3.2 新增，
   对齐 openclaw 早报的"隔夜美股映射"段落）

能力边界：实时快照（现价/昨收/当日涨跌幅/量比[仅A股个股]），无历史K线。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import requests

TIMEOUT = 8
_URL = "https://qt.gtimg.cn/q="
_HEADERS = {"Referer": "https://gu.qq.com/"}

# 全球指数温度计默认清单（顺序即展示顺序）
DEFAULT_INDICES = [
    "sh000001",   # 上证指数
    "sz399001",   # 深证成指
    "sz399006",   # 创业板指
    "sh000688",   # 科创50
    "hkHSI",      # 恒生指数
    "hkHSTECH",   # 恒生科技
    "usDJI",      # 道琼斯（隔夜）
    "usIXIC",     # 纳斯达克（隔夜）
    "usINX",      # 标普500（隔夜）
]


@dataclass
class TencentQuote:
    ticker: str
    name: str
    price: float | None
    prev_close: float | None
    change_pct: float | None       # 当日涨跌幅（%）
    volume_ratio: float | None     # 量比（仅A股个股提供）
    source: str = "qt.gtimg.cn"


def _to_tencent_code(ticker: str) -> str | None:
    """600519/600519.SH→sh600519; 000858→sz000858; 00148.HK→hk00148"""
    t = ticker.upper().strip()
    if t.endswith(".HK"):
        return "hk" + t[:-3].zfill(5)
    code = t.split(".")[0]
    if not code.isdigit() or len(code) != 6:
        return None
    if t.endswith(".SH") or code[0] in ("6", "9", "5"):
        return "sh" + code
    if t.endswith(".SZ") or code[0] in ("0", "1", "2", "3"):
        return "sz" + code
    return None  # 北交所等暂不支持


def _num(fields: list[str], i: int) -> float | None:
    try:
        return float(fields[i])
    except (IndexError, ValueError, TypeError):
        return None


def _fetch_raw(codes: list[str]) -> dict[str, list[str]]:
    """批量拉取并按 v_<code> 切分字段。取不到的 code 不在结果中。"""
    if not codes:
        return {}
    resp = requests.get(_URL + ",".join(codes), timeout=TIMEOUT, headers=_HEADERS)
    resp.raise_for_status()
    resp.encoding = "gbk"
    out = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', resp.text):
        f = m.group(2).split("~")
        if len(f) >= 33:
            out[m.group(1)] = f
    return out


def _parse_quote(code: str, f: list[str], ticker: str) -> TencentQuote | None:
    price = _num(f, 3)
    if not price:               # 0 或 None 均视为无效（停牌/无数据）
        return None
    prev = _num(f, 4)
    chg = _num(f, 32)
    if chg is None and prev:    # 个别指数 32 位缺失时自行计算
        chg = (price - prev) / prev * 100
    vol_ratio = None
    if code.startswith(("sh6", "sh9", "sh5", "sz0", "sz1", "sz2", "sz3")):
        vol_ratio = _num(f, 49)
        if vol_ratio == 0:
            vol_ratio = None
    return TencentQuote(
        ticker=ticker, name=f[1], price=price,
        prev_close=prev, change_pct=chg, volume_ratio=vol_ratio,
    )


def fetch_tencent_quotes(tickers: list[str]) -> dict[str, TencentQuote]:
    """个股批量实时行情。返回 {原始ticker: TencentQuote}。"""
    codes, mapping = [], {}
    for t in tickers:
        c = _to_tencent_code(t)
        if c:
            codes.append(c)
            mapping[c] = t
    raw = _fetch_raw(codes)
    out = {}
    for code, f in raw.items():
        q = _parse_quote(code, f, mapping.get(code, code))
        if q:
            out[q.ticker] = q
    return out


def fetch_tencent_indices(codes: list[str] | None = None) -> dict[str, TencentQuote]:
    """全球指数温度计。返回 {code: TencentQuote}，按入参顺序可重排展示。

    注意：美股三大为前一交易日收盘（A股盘前视角＝隔夜美股），盘中为实时。
    """
    codes = codes or DEFAULT_INDICES
    raw = _fetch_raw(codes)
    out = {}
    for code in codes:               # 保持清单顺序
        f = raw.get(code)
        if not f:
            continue
        q = _parse_quote(code, f, code)
        if q and q.change_pct is not None:
            out[code] = q
    return out


if __name__ == "__main__":
    # 自测: poetry run python src/tools/quotes_fallback.py
    from src.tools import proxy_guard  # noqa: F401
    print("── 个股 ──")
    for tk, q in fetch_tencent_quotes(["600519", "300308", "00148.HK"]).items():
        print(f"{tk} {q.name}: 现价{q.price} 涨跌{q.change_pct:+.2f}% 量比{q.volume_ratio}")
    print("── 指数 ──")
    for code, q in fetch_tencent_indices().items():
        print(f"{code} {q.name}: {q.price:,.0f} ({q.change_pct:+.2f}%)")
