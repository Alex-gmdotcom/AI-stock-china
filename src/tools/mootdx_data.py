# -*- coding: utf-8 -*-
"""
mootdx_data.py — pytdx 直连通达信 TCP 价格源 (Phase 3, PRICE 兜底)
==================================================================
设计:PRICE 字段组里 baostock 之后、akshare 之前的兜底源。
- 走 pytdx(TdxHq_API)纯 TCP 协议 → 不封 IP、零 httpx / py_mini_racer 依赖。
- 只服务 PRICE;needs_cn_ip=True(海外 Router 自动跳过)。
- 返回**不复权**日线(与 baostock 前复权口径不同 → 仅兜底,见模块尾注)。
- 失败语义对齐 Router 约定(datasource/router.py):
    * 连不上任何 TDX 服务器(连接级故障)→ raise(Router 记熔断,隔离本源)。
    * 查无数据 / 不支持的市场(HK/BJ)→ 返回 [](Router 转下一源,不算故障)。

依赖:pytdx(`poetry add pytdx`,零冲突)。未装则 available()=False,Router 跳过。

口径告警(必读):
  - vol 单位已实盘核对:pytdx 返回**手**,baostock 为**股**,差 100 倍。
    默认 _VOL_SCALE=100(手→股)对齐 baostock。实测 600519 2026-06-18:
    pytdx 57471 手 × 100 = 5,747,100 ≈ baostock 5,747,173 股(零头为 sub-手舍入)。
    如遇异常源单位,用环境变量 AIHF_TDX_VOL_SCALE 覆盖。
  - 价为不复权;baostock 主源是前复权。仅当 baostock 熔断/落空时 mootdx 才顶上,
    跨源拼接那一段价口径会跳,属已接受的兜底取舍。
"""
from __future__ import annotations

import os
import threading
from typing import Optional

try:
    from pytdx.hq import TdxHq_API
    _HAVE_PYTDX = True
except Exception:
    TdxHq_API = None  # type: ignore
    _HAVE_PYTDX = False

# 通达信日 K category(= TDXParams.KLINE_TYPE_RI_K)
_CAT_DAILY = 9

# vol 缩放:pytdx 返回手、baostock 为股 → 默认 100(手→股,已实盘核对)
_VOL_SCALE = float(os.environ.get("AIHF_TDX_VOL_SCALE", "100") or "100")

# TDX 行情服务器(可用 AIHF_TDX_HOSTS="ip:port,ip:port" 覆盖)
_DEFAULT_HOSTS: list[tuple[str, int]] = [
    ("119.147.212.81", 7709),
    ("60.12.136.250", 7709),
    ("218.108.98.244", 7709),
    ("123.125.108.14", 7709),
    ("180.153.18.170", 7709),
    ("180.153.18.171", 7709),
    ("202.108.253.130", 7709),
    ("60.191.117.167", 7709),
]

_CONNECT_TIMEOUT = float(os.environ.get("AIHF_TDX_TIMEOUT", "4") or "4")
_MAX_HOSTS_PER_CALL = int(os.environ.get("AIHF_TDX_MAX_HOSTS", "5") or "5")
_MAX_PAGES = 12          # 12 × 800 ≈ 9600 根 ≈ 38 年,足够任何回测窗口
_PAGE = 800              # pytdx 单次上限

# pytdx 每次用独立 api 实例(各自 socket),但仍串行化:
#   避免 9 路 agent 全跌到 mootdx 时对 TDX 服务器并发猛连被限。
#   mootdx 是稀有兜底、非热路径,串行可接受。
_LOCK = threading.RLock()


class _TdxUnavailable(RuntimeError):
    """连不上任何 TDX 服务器 —— 连接级故障,交给 Router 记熔断。"""


# ── 对外能力探测 ────────────────────────────────────────────────────────
def available() -> bool:
    return _HAVE_PYTDX


# ── 代码映射 ────────────────────────────────────────────────────────────
def to_tdx(norm: str) -> Optional[tuple[int, str]]:
    """'600519.SH' → (1,'600519');'000333.SZ' → (0,'000333');其它(HK/BJ)→ None。

    market: 1=上海, 0=深圳。BJ/北交所 pytdx 口径不稳 → 返回 None 交 akshare 兜底,
    与 baostock_data.to_bs_code 对 BJ 的短路策略一致。
    """
    try:
        code, suf = norm.split(".")
    except ValueError:
        return None
    suf = suf.upper()
    if suf == "SH":
        return (1, code)
    if suf == "SZ":
        return (0, code)
    return None  # HK / BJ / 其它


# ── 服务器 ──────────────────────────────────────────────────────────────
def _hosts() -> list[tuple[str, int]]:
    env = (os.environ.get("AIHF_TDX_HOSTS", "") or "").strip()
    if env:
        out: list[tuple[str, int]] = []
        for tok in env.split(","):
            tok = tok.strip()
            if not tok or ":" not in tok:
                continue
            ip, _, port = tok.partition(":")
            try:
                out.append((ip.strip(), int(port)))
            except ValueError:
                continue
        if out:
            return out
    return list(_DEFAULT_HOSTS)


def _connect_any(api) -> tuple[str, int]:
    """逐个试连,成功即返回 (ip,port);全失败 → _TdxUnavailable。"""
    last = None
    for ip, port in _hosts()[:_MAX_HOSTS_PER_CALL]:
        try:
            if api.connect(ip, port, time_out=_CONNECT_TIMEOUT):
                return ip, port
        except Exception as exc:  # noqa: BLE001
            last = exc
            try:
                api.disconnect()
            except Exception:
                pass
            continue
    raise _TdxUnavailable("all TDX hosts unreachable (last=%s)" % (last,))


# ── 取数 ────────────────────────────────────────────────────────────────
def _fetch_raw(market: int, code: str, start_date: str) -> list[dict]:
    """分页向前翻,取到覆盖 start_date 为止;返回原始 kline dict(已含 _date)。"""
    sd = start_date[:10]
    api = TdxHq_API(heartbeat=True, raise_exception=True)
    _connect_any(api)  # 连不上 → _TdxUnavailable 上抛(Router 熔断)
    try:
        by_date: dict[str, dict] = {}
        for page in range(_MAX_PAGES):
            bars = api.get_security_bars(_CAT_DAILY, market, code, page * _PAGE, _PAGE)
            if not bars:
                break
            page_oldest = None
            for b in bars:
                d = "%04d-%02d-%02d" % (int(b["year"]), int(b["month"]), int(b["day"]))
                b["_date"] = d
                by_date[d] = b  # 去重(跨页若有重叠,后写覆盖)
                if page_oldest is None or d < page_oldest:
                    page_oldest = d
            if page_oldest is not None and page_oldest <= sd:
                break
            if len(bars) < _PAGE:
                break
        return list(by_date.values())
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


def get_prices_dicts(norm: str, start_date: str, end_date: str) -> list[dict]:
    """→ list[{time,open,close,high,low,volume}],与 baostock_data.get_prices_dicts 同形。

    不支持的票(HK/BJ/解析失败)→ [];查无数据 → [];连接级故障 → raise。
    """
    if not _HAVE_PYTDX:
        return []
    mc = to_tdx(norm)
    if mc is None:
        return []
    market, code = mc
    sd, ed = start_date[:10], end_date[:10]
    with _LOCK:
        raw = _fetch_raw(market, code, start_date)
    out: list[dict] = []
    for b in raw:
        d = b.get("_date", "")
        if not (sd <= d <= ed):
            continue
        out.append({
            "time": d,
            "open": float(b.get("open") or 0.0),
            "close": float(b.get("close") or 0.0),
            "high": float(b.get("high") or 0.0),
            "low": float(b.get("low") or 0.0),
            "volume": int((float(b.get("vol") or 0.0)) * _VOL_SCALE),
        })
    out.sort(key=lambda x: x["time"])
    return out


# ── 自测 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[mootdx_data] available(pytdx installed):", available())
    print("[mootdx_data] to_tdx('600519.SH') =", to_tdx("600519.SH"))
    print("[mootdx_data] to_tdx('000333.SZ') =", to_tdx("000333.SZ"))
    print("[mootdx_data] to_tdx('09880.HK')  =", to_tdx("09880.HK"))
    print("[mootdx_data] to_tdx('430047.BJ') =", to_tdx("430047.BJ"))
    if available():
        try:
            rows = get_prices_dicts("600519.SH", "2026-06-01", "2026-06-18")
            print("[mootdx_data] 600519 rows:", len(rows))
            if rows:
                print("              first:", rows[0])
                print("              last :", rows[-1])
        except Exception as exc:  # noqa: BLE001
            print("[mootdx_data] live fetch failed (服务器/网络):", exc)
