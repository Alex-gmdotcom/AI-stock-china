"""
src/eval/signals.py — 已 log 信号的加载适配层
======================================================================
这是整个 harness 唯一的"对接开放点"。核心数学(metrics.py)已确定性
验证；这里负责把**你的真实信号 log** 读成统一的 Signal 列表。

>>> 需要你确认的唯一事情 <<<
    你批跑后 log 出来的信号，到底是什么格式/落在哪个文件？
    - 是 run_Ashare.log 里的文本，还是有结构化 JSON/CSV 落盘？
    - portfolio_manager 最终 action 的字段名是什么？
    - 单 agent 的 {signal, confidence} 是否也想纳入 IC？

下面先按一个**明确约定的 schema** 实现一个能跑的 loader。等你贴一份
真实 log 样本，把 `load_signals_from_*` 里的字段映射对齐即可——不动
metrics/fees/data，改动被隔离在这一个文件。这符合"诊断→确认→实施、
不盲打补丁"：不确定的部分被围栏在单点，而不是散落全仓。
"""
from __future__ import annotations

import json
import csv
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import signal_to_score


# ----------------------------------------------------------------------
# 统一信号契约
# ----------------------------------------------------------------------
@dataclass
class Signal:
    date: str          # 信号生成日 "YYYY-MM-DD"（= 建仓基准日）
    ticker: str        # "300308.SZ" / "09880.HK"
    direction: str     # bullish|bearish|neutral（或中文，signal_to_score 会归一）
    confidence: float  # 0-100
    source: str = "portfolio_manager"   # 或某个 agent 名，便于分 agent 算 IC
    stock_class: str = ""               # V|T|N（可选，便于分类看 IC）
    extra: dict = field(default_factory=dict)  # 票数等附加信息（bull/bear/neutral）

    @property
    def score(self) -> float:
        return signal_to_score(self.direction, self.confidence)


# ----------------------------------------------------------------------
# 约定 schema A：结构化 JSON（推荐——让批跑额外落一份这个）
# ----------------------------------------------------------------------
# 期望形如：
# [
#   {"date":"2026-07-01","ticker":"300308.SZ","direction":"bullish",
#    "confidence":72,"source":"portfolio_manager","stock_class":"T"},
#   ...
# ]
def load_signals_from_json(path: str | Path) -> list[Signal]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for r in rows:
        out.append(Signal(
            date=str(r["date"]),
            ticker=str(r["ticker"]),
            direction=str(r.get("direction") or r.get("signal") or r.get("action")),
            confidence=float(r.get("confidence", 0) or 0),
            source=str(r.get("source", "portfolio_manager")),
            stock_class=str(r.get("stock_class", "")),
        ))
    return out


# ----------------------------------------------------------------------
# 约定 schema B：CSV（评分卡导出也能对上）
# ----------------------------------------------------------------------
def load_signals_from_csv(path: str | Path) -> list[Signal]:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out.append(Signal(
                date=str(r["date"]),
                ticker=str(r["ticker"]),
                direction=str(r.get("direction") or r.get("signal") or r.get("action")),
                confidence=float(r.get("confidence", 0) or 0),
                source=str(r.get("source", "portfolio_manager")),
                stock_class=str(r.get("stock_class", "")),
            ))
    return out


# ----------------------------------------------------------------------
# schema C：从 run_*.log 的 PORTFOLIO SUMMARY 表抽信号（已按真实格式实现）
# ----------------------------------------------------------------------
import re

# 自选池 V/T/N 分类（项目 canonical 配置，非日志内容）
WATCHLIST_CLASS = {
    "002444.SZ": "V", "600660.SH": "V", "000333.SZ": "V",
    "000921.SZ": "V", "600887.SH": "V",
    "300308.SZ": "T", "002008.SZ": "T", "603228.SH": "T",
    "300502.SZ": "T", "688019.SH": "T",
    "601985.SH": "N", "002050.SZ": "N", "002594.SZ": "N",
    "09880.HK": "N", "09660.HK": "N",
}

# Action → 方向。SHORT 与 SELL 同为看空。
_ACTION_DIR = {
    "BUY": "bullish", "COVER": "bullish",
    "SELL": "bearish", "SHORT": "bearish",
    "HOLD": "neutral",
}

# | 002444.SZ |   SELL   |    4934 |    6.0% |   1   |   3   |   5   |
_ROW = re.compile(
    r"^\|\s*([0-9]{4,6}\.(?:SZ|SH|HK))\s*\|\s*([A-Z]+)\s*\|\s*\d+\s*\|"
    r"\s*([\d.]+)%\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
)
_DATE = re.compile(r"Date range:\s*[\d-]+\s*\S*\s*([\d]{4}-[\d]{2}-[\d]{2})")


def load_signals_from_run_log(path: str | Path) -> list[Signal]:
    """从 main_china.py 批跑输出的 PORTFOLIO SUMMARY 表抽取最终信号。

    决策日 = "Date range: ... -> END" 的 END（批跑的 as-of 日）。
    direction/confidence 取自 portfolio_manager 最终 Action + Confidence。
    额外把 bull/bear/neutral 票数塞进 Signal.extra（供未来做票差分）。
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    dates = _DATE.findall(text)
    decision_date = dates[0] if dates else ""

    out: list[Signal] = []
    for line in text.splitlines():
        r = _ROW.match(line.strip())
        if not r:
            continue
        ticker, action, conf, bull, bear, neut = r.groups()
        direction = _ACTION_DIR.get(action)
        if direction is None:
            continue  # 未知 action 不臆造方向
        out.append(Signal(
            date=decision_date,
            ticker=ticker,
            direction=direction,
            confidence=float(conf),
            source="portfolio_manager",
            stock_class=WATCHLIST_CLASS.get(ticker, ""),
            extra={"bull": int(bull), "bear": int(bear), "neutral": int(neut),
                   "action": action},
        ))
    return out


def load_signals(path: str | Path) -> list[Signal]:
    """按扩展名自动分流。"""
    p = Path(path)
    if p.suffix == ".json":
        return load_signals_from_json(p)
    if p.suffix == ".csv":
        return load_signals_from_csv(p)
    if p.suffix in (".log", ".txt"):
        return load_signals_from_run_log(p)
    raise ValueError(f"未知信号文件类型: {p.suffix}（支持 .json/.csv，.log 待对接）")
