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
    # marker: EVAL_BS_SHARED_SESSION_V1 — 复用 baostock_data 进程级会话 + 全局锁
    # 根因(2026-07-07 Step16 真机):本函数自建 login/logout 绕过 _BS_LOCK,与
    # api_china 并发时单 socket 串话(11列数据串进3列查询)+ logout 拆掉别人
    # 正在用的连接(WinError 10053/10038)。修类:进程内 baostock 会话唯一
    # 真相源 = tools.baostock_data;本函数持锁期间独占,退出不 logout。
    # marker: EVAL_BS_SHARED_SESSION_V2 — 已加载实例优先,根除双模块实例
    # 根因(2026-07-07 真机):V1 优先 `src.tools`,而 api_china 优先 `tools`;
    # `python src/web_app.py` 下两路径都可导 → 同一文件两个模块实例(两把锁
    # 两次 login),第二次 login 顶掉在用连接 → utf-8/zlib 收包垃圾。
    # 修类:进程里已加载哪个实例就加入哪个;都没有才按 api_china 同序导入。
    import baostock as bs
    import sys as _sys
    _bsd = (_sys.modules.get("tools.baostock_data")
            or _sys.modules.get("src.tools.baostock_data"))
    if _bsd is None:
        try:
            from tools import baostock_data as _bsd  # type: ignore
        except ImportError:
            try:
                from src.tools import baostock_data as _bsd  # type: ignore
            except ImportError:
                _bsd = None
    else:
        _a = _sys.modules.get("tools.baostock_data")
        _b = _sys.modules.get("src.tools.baostock_data")
        if _a is not None and _b is not None and _a is not _b:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "baostock_data 双模块实例并存(tools.* 与 src.tools.*),已加入 tools.* 会话;"
                "另一实例若被使用仍有串话风险,请排查导入路径")

    if _bsd is not None and getattr(_bsd, "_HAVE_BS", False):
        with _bsd._BS_LOCK:
            if not _bsd._ensure_login():
                raise RuntimeError("baostock 登录失败(共享会话)")
            yield bs   # 进程级会话:不 logout,退出由进程回收
        return
    # 兜底:独立环境(无 baostock_data)保留旧行为
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
        s = s.dropna()
        return s[s > 0]          # OHLC_SANITY_V1: 非正价在加载层丢弃

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
def nth_trading_day_in_series(px: pd.Series, date: str, n: int) -> str | None:
    """在该票自身价格索引(即其自身交易日历)上取从 date(含)起第 n 个交易日。
    marker: EVAL_HK_PRICE_V1 —— 港股不用 A 股日历(两市假期不同)。"""
    td = [d for d in px.index if d >= date]
    if len(td) <= n:
        return None
    return td[n]


def hk_closes_via_api_china(ticker: str, start: str, end: str) -> pd.Series:
    """港股收盘序列(marker: EVAL_HK_PRICE_V1)。
    复用 api_china 价格链(tushare_hk → 东财 → 新浪, qfq; 同一快照内比率一致)。
    基准仍 = 沪深300(F34 口径不变); 失败 → 空 Series(该票跳过, I10.6 不崩 run)。"""
    import logging
    try:
        try:
            from src.tools import api_china
        except ImportError:
            import api_china  # type: ignore
        prices = api_china.get_prices(ticker, start, end)
        if not prices:
            logging.getLogger(__name__).warning(
                "EVAL_HK_PRICE: %s 价格链返回空(%s..%s)", ticker, start, end)
            return pd.Series(dtype=float)
        s = pd.Series({str(p.time)[:10]: float(p.close)
                       for p in prices if getattr(p, "close", None)})
        s = s[s > 0].sort_index()               # OHLC_SANITY_V1: 非正价丢弃
        return s
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "EVAL_HK_PRICE: %s 失败: %s", ticker, str(exc)[:120])
        return pd.Series(dtype=float)


def nth_trading_day_on_or_after(date: str, n: int, trade_dates: list[str]) -> str | None:
    """从 date（含）起第 n 个交易日。n=0 表示 date 当天或之后最近的交易日。

    trade_dates 必须已排序。返回 None 表示日历不够长（收益要留 nan）。
    """
    td = [d for d in trade_dates if d >= date]
    if len(td) <= n:
        return None
    return td[n]
