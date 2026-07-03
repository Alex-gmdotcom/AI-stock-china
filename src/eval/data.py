"""
src/eval/data.py — 价格 / 交易日历 / 沪深300 基准（baostock）
======================================================================
⚠️ 沙箱盲区：Claude 够不到 baostock，这一层**必须国内机器实跑验证**。
   代码按 baostock 官方 API 写，接口设计成可被 mock（见 PriceSource 协议），
   所以 metrics 层能在沙箱里用假价格跑通。

覆盖范围：A 股（baostock 仅支持 A 股）。
   港股 09880.HK / 09660.HK **baostock 取不到**——需走 Sina/腾讯 fallback
   （项目已有的价格 fallback 源）。本文件先标 TODO，核心 13 只 A 股先跑通。

复权：用后复权(adjustflag="1")算前瞻收益（跨除权日更正确）。
基准：沪深300 = baostock "sh.000300"。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Protocol

import pandas as pd


# ----------------------------------------------------------------------
# ticker 后缀 → baostock code
# ----------------------------------------------------------------------
def to_bs_code(ticker: str) -> str:
    """'600660.SH' -> 'sh.600660'；'300308.SZ' -> 'sz.300308'。"""
    t = ticker.strip().upper()
    if t.endswith(".SH"):
        return "sh." + t[:-3]
    if t.endswith(".SZ"):
        return "sz." + t[:-3]
    if t.endswith(".HK"):
        raise ValueError(f"{ticker}: baostock 不支持港股，请走 Sina/腾讯 fallback")
    raise ValueError(f"无法识别的 ticker 后缀: {ticker}")


# ----------------------------------------------------------------------
# 价格源协议（便于 mock）
# ----------------------------------------------------------------------
class PriceSource(Protocol):
    def get_closes(self, ticker: str, start: str, end: str) -> pd.Series: ...
    def get_trade_dates(self, start: str, end: str) -> list[str]: ...


# ----------------------------------------------------------------------
# baostock 实现（国内机器实跑）
# ----------------------------------------------------------------------
@contextmanager
def baostock_session():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


class BaostockPriceSource:
    """真实价格源。用法：with baostock_session() as bs: src = BaostockPriceSource(bs)"""

    def __init__(self, bs):
        self.bs = bs

    def _query(self, code: str, start: str, end: str, fields: str) -> pd.DataFrame:
        rs = self.bs.query_history_k_data_plus(
            code, fields, start_date=start, end_date=end,
            frequency="d", adjustflag="1",   # 1 = 后复权
        )
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 查询失败 {code}: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return pd.DataFrame(rows, columns=rs.fields)

    def get_closes(self, ticker: str, start: str, end: str) -> pd.Series:
        code = to_bs_code(ticker)
        df = self._query(code, start, end, "date,close,tradestatus")
        if df.empty:
            return pd.Series(dtype=float)
        df = df[df["tradestatus"] == "1"]          # 只保留正常交易日
        s = pd.to_numeric(df["close"], errors="coerce")
        s.index = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return s.dropna()

    def get_benchmark_closes(self, start: str, end: str) -> pd.Series:
        """沪深300 收盘。指数 tradestatus 字段不同，单独处理。"""
        df = self._query("sh.000300", start, end, "date,close")
        if df.empty:
            return pd.Series(dtype=float)
        s = pd.to_numeric(df["close"], errors="coerce")
        s.index = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return s.dropna()

    def get_trade_dates(self, start: str, end: str) -> list[str]:
        rs = self.bs.query_trade_dates(start_date=start, end_date=end)
        if rs.error_code != "0":
            raise RuntimeError(f"baostock 交易日历失败: {rs.error_code} {rs.error_msg}")
        dates = []
        while rs.next():
            d, is_trade = rs.get_row_data()
            if is_trade == "1":
                dates.append(d)
        return sorted(dates)


# ----------------------------------------------------------------------
# 交易日历工具（不依赖数据源，纯逻辑，可沙箱验证）
# ----------------------------------------------------------------------
def nth_trading_day_on_or_after(date: str, n: int, trade_dates: list[str]) -> str | None:
    """从 date（含）起第 n 个交易日。n=0 表示 date 当天或之后最近的交易日。

    trade_dates 必须已排序。返回 None 表示日历不够长（收益要留 nan）。
    """
    td = [d for d in trade_dates if d >= date]
    if len(td) <= n:
        return None
    return td[n]
