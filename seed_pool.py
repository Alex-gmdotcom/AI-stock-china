#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed_pool.py — 用 Alex 的 V/T/N 自选股初始化三分法观察池（一次性）。

写入 ~/.ai-hedge-fund/pool_state.json（守 5+5+5 不变量）。
已有池会被 save_pool_state 自动备份。

用法: poetry run python seed_pool.py
"""
from __future__ import annotations

import sys
from datetime import datetime

sys.path.insert(0, ".")

try:
    from src.strategy.three_categories import (
        PoolState, PoolEntry, add_initial_entry, save_pool_state,
        load_pool_state, assert_pool_size_invariant, default_pool_dir,
    )
except Exception as e:
    print(f"[FAIL] 无法导入 three_categories: {e}")
    sys.exit(1)

# (ticker, name, category, rationale)
SEED = [
    # ── V 估值 ──
    ("002444.SZ", "巨星科技", "V", "手工具龙头，全球渠道+自有品牌，低估值高分红"),
    ("600660.SH", "福耀玻璃", "V", "汽车玻璃全球龙头，护城河深、ROE稳"),
    ("000333.SZ", "美的集团", "V", "白电龙头+ToB二曲线，估值合理现金流强"),
    ("000921.SZ", "海信家电", "V", "中央空调+冰洗，低估值、出海弹性"),
    ("600887.SH", "伊利股份", "V", "乳业双寡头，必选消费、分红稳"),
    # ── T 趋势 ──
    ("300308.SZ", "中际旭创", "T", "光模块龙头，AI算力高景气主升"),
    ("002008.SZ", "大族激光", "T", "激光设备平台，受益消费电子+新能源资本开支"),
    ("603228.SH", "景旺电子", "T", "PCB，AI服务器+汽车电子需求拉动"),
    ("300502.SZ", "新易盛", "T", "光模块第二极，800G放量趋势强"),
    ("688019.SH", "安集科技", "T", "半导体材料CMP抛光液，国产替代趋势"),
    # ── N 叙事 ──
    ("09880.HK", "优必选", "N", "人形机器人叙事龙头"),
    ("09660.HK", "地平线", "N", "智驾芯片，自动驾驶叙事"),
    ("601985.SH", "中国核电", "N", "核电+绿电，能源转型叙事"),
    ("002050.SZ", "三花智控", "N", "热管理，机器人执行器+新能源叙事"),
    ("002594.SZ", "比亚迪", "N", "新能源车+智驾+出海多重叙事"),  # A股
]


def main() -> None:
    pool_dir = default_pool_dir()
    print(f"目标池文件目录: {pool_dir}")

    # 展示既有状态（save_pool_state 会自动备份，不怕覆盖）
    try:
        old = load_pool_state()
        n_old = len(old.v_pool) + len(old.t_pool) + len(old.n_pool)
        if n_old:
            print(f"[INFO] 已有池含 {n_old} 只票，将被备份后覆盖")
    except Exception:
        print("[INFO] 当前无有效池，全新初始化")

    counts = {"V": 0, "T": 0, "N": 0}
    state = PoolState()
    now = datetime.now().isoformat()
    for ticker, name, cat, rationale in SEED:
        entry = PoolEntry(
            ticker=ticker, name=name, category=cat,
            sub_id="", rationale=rationale, added_at=now,
        )
        state = add_initial_entry(state, entry, persist=False)
        counts[cat] += 1

    print(f"构造完成: V={counts['V']} T={counts['T']} N={counts['N']}")
    assert_pool_size_invariant(state)   # 强校验 5+5+5
    save_pool_state(state)              # 写盘（自动备份旧文件）

    # 回读确认
    back = load_pool_state()
    total = len(back.v_pool) + len(back.t_pool) + len(back.n_pool)
    print(f"[OK] 已写盘并回读确认: 共 {total} 只 "
          f"(V{len(back.v_pool)}/T{len(back.t_pool)}/N{len(back.n_pool)})")
    print("[SUCCESS] 三分法池播种完成 — 刷新 web 控制台的观察池即可看到")


if __name__ == "__main__":
    main()
