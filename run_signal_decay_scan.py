# -*- coding: utf-8 -*-
"""
run_signal_decay_scan.py — 18c 信号衰减生命周期扫描器 v1
================================================================
marker: SIGNAL_DECAY_SCAN_V1 (Vibe-Trading 借鉴#2, 2026-07-15 Alex 批)

思想: 信号不是永久资产。每类迁移信号(S1/S2/S3)对成熟触发样本做滚动命中率
体检, 按状态机标注生命周期; 只标注不禁用(I4.3 同源纪律: 亮灯供人裁决)。

数据流:
  ~/.ai-hedge-fund/migration_signals_log.jsonl (跑批器追加, 灰灯不入)
    → record_id 去重(重复触发合并只算首日)
    → 成熟样本 = 信号日后已满 OUTCOME_H 个交易日
    → outcome = OUTCOME_H 日相对沪深300超额收益 vs 预期方向(S1+/S3+/S2−)
    → 每类信号近 ROLLING_N 个成熟样本滚动命中率 → 状态机
产物(~/.ai-hedge-fund/):
  migration_signals_decay.json   各信号类型生命周期状态(人工 disabled 位保留)
  migration_signals_decay_report.txt  人读报告(utf-8)

状态机(v1 参数, 待样本累积后校准):
  n < MIN_N(5)              → active     (样本不足, 只记不判)
  n≥5 且 hit ≥ 55%          → active
  n≥5 且 40% ≤ hit < 55%    → monitoring (关注但不动)
  n≥8 且 hit < 40%          → decayed    (建议人工复核该类阈值)
  disabled                  → 人工在 decay.json 置 true, 扫描永不改写

运行: poetry run python run_signal_decay_scan.py
纪律: 价格/日历 fetcher 可注入 → 沙箱确定性单测零网络;
      每条静默退出路径必打 WARNING 面包屑(I1.1)。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, ".")

try:                                   # 与其它入口一致加载 .env(无 dotenv 不阻塞)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_logger = logging.getLogger("decay_scan")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

HOME = Path.home() / ".ai-hedge-fund"
LOG_F = HOME / "migration_signals_log.jsonl"
DECAY_F = HOME / "migration_signals_decay.json"
REPORT_F = HOME / "migration_signals_decay_report.txt"

OUTCOME_H = 20          # 前瞻交易日(与 harness H=20 对齐)
ROLLING_N = 12          # 滚动窗口: 每类信号近 N 个成熟样本
MIN_N = 5               # 低于此样本数不判定
HIT_ACTIVE = 0.55       # ≥55% → active
HIT_DECAYED = 0.40      # <40%(且 n≥8) → decayed
MIN_N_DECAYED = 8
EXPECTED_DIR = {"S1": 1.0, "S3": 1.0, "S2": -1.0}   # 预期20日超额方向


# ---------------------------------------------------------------
def load_log_entries(path: Path) -> list[dict]:
    """读 JSONL; record_id 去重取首个 run_date(重复触发合并只算首日)。"""
    if not path.exists():
        _logger.warning("信号日志不存在: %s", path)
        return []
    seen: dict = {}
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception as exc:
            _logger.warning("日志第 %d 行解析失败(跳过): %s", i + 1, str(exc)[:80])
            continue
        rid = rec.get("record_id") or f"_noid_{i}"
        if rid not in seen:
            seen[rid] = rec
    return sorted(seen.values(), key=lambda r: str(r.get("run_date", "")))


def _default_fetch_market_data(entries: list[dict]):
    """默认真实数据链: A股 baostock 共享会话 + 港股 api_china 链 + 沪深300基准。
    返回 (closes_by_ticker, bench_closes, trade_dates)。任一票失败→缺席(跳过, I10.6)。"""
    import pandas as pd
    from src.eval.data import (BaostockPriceSource, baostock_session,
                               hk_closes_via_api_china)
    dates = sorted(str(r.get("run_date")) for r in entries if r.get("run_date"))
    start = dates[0]
    end = (pd.to_datetime(dates[-1]) + pd.Timedelta(days=OUTCOME_H * 2 + 15)).strftime("%Y-%m-%d")
    tickers = sorted({str(r.get("ticker")) for r in entries if r.get("ticker")})
    a_sh = [t for t in tickers if not t.upper().endswith(".HK")]
    hk = [t for t in tickers if t.upper().endswith(".HK")]
    closes: dict = {}
    with baostock_session() as bs:
        src = BaostockPriceSource(bs)
        for t in a_sh:
            try:
                s = src.get_closes(t, start, end)
                if len(s):
                    closes[t] = s
                else:
                    _logger.warning("decay: %s 收盘序列为空(跳过)", t)
            except Exception as exc:
                _logger.warning("decay: %s 价格失败(跳过): %s", t, str(exc)[:100])
        bench = src.get_benchmark_closes(start, end)
        trade_dates = src.get_trade_dates(start, end)
    for t in hk:
        s = hk_closes_via_api_china(t, start, end)
        if len(s):
            closes[t] = s
        else:
            _logger.warning("decay: %s 港股价格链取不到(跳过)", t)
    return closes, bench, trade_dates


def compute_outcomes(entries: list[dict], closes: dict, bench, trade_dates: list[str]) -> list[dict]:
    """成熟样本 → outcome 记录。港股按自身日历取第 OUTCOME_H 个交易日, 基准腿按 A 股日历。"""
    from src.eval.data import nth_trading_day_in_series, nth_trading_day_on_or_after
    from src.eval import metrics
    out = []
    for rec in entries:
        tkr, sig, d0 = rec.get("ticker"), rec.get("signal"), str(rec.get("run_date"))
        exp = EXPECTED_DIR.get(sig)
        if not tkr or exp is None or not d0:
            _logger.warning("decay: 记录字段不全(跳过): %s", str(rec)[:100])
            continue
        px = closes.get(tkr)
        if px is None or d0 not in px.index:
            out.append({**_base(rec), "status": "no_price"})
            continue
        bench_exit = nth_trading_day_on_or_after(d0, OUTCOME_H, trade_dates)
        exit_date = (nth_trading_day_in_series(px, d0, OUTCOME_H)
                     if tkr.upper().endswith(".HK") else bench_exit)
        if exit_date is None or exit_date not in px.index or bench_exit is None:
            out.append({**_base(rec), "status": "pending"})   # 未成熟: 日历还没走满
            continue
        fwd = metrics.forward_return(float(px[d0]), float(px[exit_date]))
        b_fwd = metrics.forward_return(
            float(bench[d0]) if d0 in bench.index else None,
            float(bench[bench_exit]) if bench_exit in bench.index else None)
        exc = metrics.excess_return(fwd, b_fwd)
        if exc != exc:                                        # NaN
            out.append({**_base(rec), "status": "no_price"})
            continue
        hit = (exc > 0) == (exp > 0)
        out.append({**_base(rec), "status": "mature", "excess_20d": round(exc, 4),
                    "expected": "+" if exp > 0 else "-", "hit": bool(hit)})
    return out


def _base(rec: dict) -> dict:
    return {"record_id": rec.get("record_id"), "run_date": rec.get("run_date"),
            "ticker": rec.get("ticker"), "signal": rec.get("signal"),
            "strength": rec.get("strength")}


def lifecycle(outcomes: list[dict], prev_state: dict | None) -> dict:
    """每类信号(S1/S2/S3)状态机。人工 disabled 位从旧状态保留, 扫描永不改写。"""
    prev = (prev_state or {}).get("by_signal", {})
    by_sig: dict = {}
    for sig in ("S1", "S2", "S3"):
        mature = [o for o in outcomes if o["signal"] == sig and o["status"] == "mature"]
        mature.sort(key=lambda o: str(o["run_date"]))
        window = mature[-ROLLING_N:]
        n = len(window)
        hits = sum(1 for o in window if o["hit"])
        rate = (hits / n) if n else None
        if n < MIN_N:
            state, why = "active", f"样本不足(n={n}<{MIN_N}), 只记不判"
        elif n >= MIN_N_DECAYED and rate < HIT_DECAYED:
            state, why = "decayed", f"滚动命中 {rate:.0%} < {HIT_DECAYED:.0%}(n={n}), 建议人工复核阈值"
        elif rate >= HIT_ACTIVE:
            state, why = "active", f"滚动命中 {rate:.0%} ≥ {HIT_ACTIVE:.0%}(n={n})"
        else:
            state, why = "monitoring", f"滚动命中 {rate:.0%} 落入 {HIT_DECAYED:.0%}-{HIT_ACTIVE:.0%}(n={n})"
        disabled = bool(prev.get(sig, {}).get("disabled", False))
        by_sig[sig] = {"state": state, "disabled": disabled, "reason": why,
                       "rolling_n": n, "rolling_hits": hits,
                       "rolling_hit_rate": None if rate is None else round(rate, 4),
                       "pending": sum(1 for o in outcomes
                                      if o["signal"] == sig and o["status"] == "pending"),
                       "no_price": sum(1 for o in outcomes
                                       if o["signal"] == sig and o["status"] == "no_price")}
    return {"scanned_at": date.today().isoformat(),
            "params": {"outcome_h": OUTCOME_H, "rolling_n": ROLLING_N, "min_n": MIN_N,
                       "hit_active": HIT_ACTIVE, "hit_decayed": HIT_DECAYED,
                       "min_n_decayed": MIN_N_DECAYED},
            "by_signal": by_sig}


def scan(log_path: Path = LOG_F, fetch_market_data=None,
         prev_state: dict | None = None) -> tuple[dict, list[dict]]:
    """核心编排(fetcher 可注入 → 沙箱确定性单测)。返回 (decay_state, outcomes)。"""
    entries = load_log_entries(log_path)
    if not entries:
        empty = lifecycle([], prev_state)
        empty["note"] = "信号日志为空(尚无红/黄触发) —— 此即结论, 不是错误"
        return empty, []
    fetch = fetch_market_data or _default_fetch_market_data
    closes, bench, trade_dates = fetch(entries)
    outcomes = compute_outcomes(entries, closes, bench, trade_dates)
    return lifecycle(outcomes, prev_state), outcomes


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    prev = None
    if DECAY_F.exists():
        try:
            prev = json.loads(DECAY_F.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[decay] WARN 旧状态不可读, 冷启动: {exc}")
    state, outcomes = scan(prev_state=prev)
    _atomic_write(DECAY_F, json.dumps(state, ensure_ascii=False, indent=2))

    lines = [f"18c 信号衰减生命周期 · {state['scanned_at']} · H={OUTCOME_H}交易日 vs 沪深300",
             "=" * 56]
    if state.get("note"):
        lines.append(state["note"])
    for sig in ("S1", "S2", "S3"):
        b = state["by_signal"][sig]
        flag = "[禁用中·人工]" if b["disabled"] else {"active": "[活跃]",
                                                     "monitoring": "[观察]",
                                                     "decayed": "[衰减]"}[b["state"]]
        lines.append(f"{flag} {sig}  {b['reason']}  "
                     f"(未成熟 {b['pending']} / 缺价 {b['no_price']})")
    mature = [o for o in outcomes if o["status"] == "mature"]
    if mature:
        lines.append("-" * 56)
        for o in mature[-20:]:
            lines.append(f"  {o['run_date']} {o['ticker']:<10} {o['signal']} "
                         f"{o['strength']} 预期{o['expected']} 超额{o['excess_20d']:+.2%} "
                         f"{'命中' if o['hit'] else '未中'}")
    lines.append("-" * 56)
    lines.append("提示: 状态只标注不禁用; disabled 需人工在 decay.json 置 true, 扫描永不改写。")
    _atomic_write(REPORT_F, "\n".join(lines))

    n_m = len(mature)
    print(f"[decay] entries(dedup)={len(load_log_entries(LOG_F))} mature={n_m}")
    for sig in ("S1", "S2", "S3"):
        b = state["by_signal"][sig]
        print(f"[decay] {sig}: {b['state']} n={b['rolling_n']} hit={b['rolling_hit_rate']}")
    print(f"[decay] report -> {REPORT_F}")


if __name__ == "__main__":
    main()
