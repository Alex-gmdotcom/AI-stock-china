# -*- coding: utf-8 -*-
"""
run_migration_signals.py — Step 18c 跑批器(CLI)
================================================================
流程: 加载三分法池 -> 采集输入 -> evaluate_pool -> 落盘 + 报告
运行: poetry run python run_migration_signals.py
产物(~/.ai-hedge-fund/):
  migration_signals_state.json   冷却/迟滞状态(原子写 .tmp+rename)
  migration_signals_latest.json  本次全部信号(UI 接线将读它)
  migration_signals_log.jsonl    追加式信号日志(审批单 §3: 供 outcome 回测)
  migration_signals_report.txt   人读报告(utf-8; 控制台只打 ASCII 防 GBK)
只亮灯不操作(I4.3): 本脚本不写池状态。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, ".")

from src.strategy.migration_signals import evaluate_pool, __version__ as ENGINE_VER
from src.strategy.signal_inputs import collect_signal_inputs
from src.strategy.three_categories import load_pool_state

HOME = Path.home() / ".ai-hedge-fund"
STATE_F = HOME / "migration_signals_state.json"
LATEST_F = HOME / "migration_signals_latest.json"
LOG_F = HOME / "migration_signals_log.jsonl"
REPORT_F = HOME / "migration_signals_report.txt"

STRENGTH_ICON = {"red": "[红]", "yellow": "[黄]", "gray": "[灰]"}


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main():
    HOME.mkdir(parents=True, exist_ok=True)
    run_date = date.today().isoformat()

    pool_state = load_pool_state()
    entries = list(pool_state.v_pool) + list(pool_state.t_pool) + list(pool_state.n_pool)
    pool = {e.ticker: e.category for e in entries}
    names = {e.ticker: f"{e.sub_id} {e.name}" for e in entries}

    print(f"[18c] pool={len(pool)} tickers, run_date={run_date}, engine={ENGINE_VER}")
    data = collect_signal_inputs(pool, run_date)

    state = None
    if STATE_F.exists():
        try:
            state = json.loads(STATE_F.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[18c] WARN state file unreadable, cold start: {exc}")

    signals, new_state = evaluate_pool(pool, data, state, run_date)

    _atomic_write(STATE_F, json.dumps(new_state, ensure_ascii=False, indent=2))
    payload = {"run_date": run_date, "engine": ENGINE_VER,
               "signals": [s.to_dict() for s in signals]}
    _atomic_write(LATEST_F, json.dumps(payload, ensure_ascii=False, indent=2))
    with LOG_F.open("a", encoding="utf-8") as fh:      # 追加式, 供回测
        for s in signals:
            if s.strength != "gray":                    # 灰灯不进回测日志
                fh.write(json.dumps({"run_date": run_date, **s.to_dict()},
                                    ensure_ascii=False) + "\n")

    lines = [f"Step 18c 迁移信号 · {run_date} · engine {ENGINE_VER}", "=" * 56]
    n_red = n_yel = n_gray = 0
    for s in sorted(signals, key=lambda x: ({"red": 0, "yellow": 1, "gray": 2}[x.strength])):
        n_red += s.strength == "red"
        n_yel += s.strength == "yellow"
        n_gray += s.strength == "gray"
        tag = STRENGTH_ICON[s.strength] + ("★" if s.highlight else " ")
        lines.append(f"{tag} {names.get(s.ticker, s.ticker):<16} {s.signal} "
                     f"{s.from_category}->{s.to_category}  {s.note or ''}")
        if s.strength != "gray":
            lines.append(f"      evidence: {json.dumps(s.evidence, ensure_ascii=False)}")
    if not signals:
        lines.append("(无信号, 也无灰灯 —— 池空?)")
    lines.append("-" * 56)
    lines.append(f"红 {n_red} | 黄 {n_yel} | 灰(不可算) {n_gray} | "
                 f"红灯高亮上限 3, 冷却 5 运行日")
    lines.append("提示: 灰灯=数据依赖缺失(缺项见各行), 不是无信号; "
                 "红黄灯仅供人工迁移决策参考, 引擎永不自动迁移(I4.3)。")
    _atomic_write(REPORT_F, "\n".join(lines))

    print(f"[18c] red={n_red} yellow={n_yel} gray={n_gray}")
    print(f"[18c] report -> {REPORT_F}")
    print(f"[18c] latest -> {LATEST_F}")


if __name__ == "__main__":
    main()
