# -*- coding: utf-8 -*-
"""
migration_signals.py — Step 18c 迁移信号引擎(核心,纯计算)
================================================================
规格: TECH §10.2 + 阈值审批单 v1(2026-07-13 Alex 批) + 裁决⑦(S2=融资余额口径)
设计纪律:
  - 纯函数核心: evaluate_pool(pool, data, state, run_date) -> (signals, new_state)
    不做任何 IO/网络; 数据由调用方注入 -> 沙箱可确定性单测
  - 只亮灯不操作(I4.3): 输出 MigrationSignal 列表供 UI 红黄灯, 永不改池
  - 灰灯语义(I1.1): 数据依赖缺失 -> strength="gray" + missing 列表,
    禁止无灯冒充"无信号"
  - 冷却/迟滞/重复触发合并/红灯高亮上限: 引擎语义表(审批单 §2)
状态持久化(冷却与迟滞跨 run): 由 IO 包装层读写
  ~/.ai-hedge-fund/migration_signals_state.json (核心不落盘)
"""
from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field, asdict

__version__ = "18c-core-v1.2"   # v1.2: S3-B 未盈利强制营收口径(2026-07-15 Alex 批)

# ---------------------------------------------------------------
# 阈值常量表 — single source of truth(审批单 v1 §1/§2, 已批)
# ---------------------------------------------------------------
S1_YELLOW = 0.15          # V→T: 5日累涨 ≥15% 黄
S1_RED = 0.20             # ≥20% 红
S1_OFF = 0.12             # 迟滞: 回落 <12% 熄
S2_DECLINE_DAYS = 3       # T→V: 融资余额连续 3 日下降
S2_RANK_DROP = 5          # AND 板块排名 5 日下滑 >5 位
S2_RED_SIGMA = 2.0        # 红: 3日降幅合计 > 近20日日均变化 2σ
S2_SIGMA_WINDOW = 20      # σ 计算窗口(日度变化数)
S3_YOY_PROXY = 0.30       # N→T: 无一致预期时 净利YoY >30% 代理
S3_REV_PROXY = 0.30       # S3-B: 未盈利强制营收YoY >30%(2026-07-15 批); 净利YoY缺失同口径降级
S3_RET20_YELLOW = 0.10    # AND ret_20d >10% 黄
S3_RET20_RED = 0.20       # ret_20d >20% 红
COOLDOWN_DAYS = 5         # 熄灯/事件触发后 5 个运行日冷却
MAX_RED_HIGHLIGHT = 3     # 每日最多高亮 3 个红灯, 其余折叠

SIGNAL_BY_CLASS = {"V": "S1", "T": "S2", "N": "S3"}
SIGNAL_ROUTE = {"S1": ("V", "T"), "S2": ("T", "V"), "S3": ("N", "T")}


@dataclass
class MigrationSignal:
    ticker: str
    signal: str                    # S1/S2/S3
    from_category: str
    to_category: str
    strength: str                  # red / yellow / gray
    evidence: dict = field(default_factory=dict)
    record_id: str = ""
    first_detected: str = ""
    last_updated: str = ""
    highlight: bool = False        # 红灯排序取 top MAX_RED_HIGHLIGHT
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------
# 各信号的条件求值: 返回 (level, evidence, off, missing)
#   level ∈ {None,"yellow","red"} / off: 迟滞熄灯条件 / missing: 缺失输入名
# ---------------------------------------------------------------
def _eval_s1(d: dict):
    r5 = d.get("ret_5d")
    if r5 is None:
        return None, {}, False, ["ret_5d"]
    ev = {"ret_5d": round(r5, 4), "rule": f"5日累涨 黄≥{S1_YELLOW:.0%} 红≥{S1_RED:.0%} 熄<{S1_OFF:.0%}",
          "score": r5}
    level = "red" if r5 >= S1_RED else ("yellow" if r5 >= S1_YELLOW else None)
    return level, ev, (r5 < S1_OFF), []


def _eval_s2(d: dict):
    missing = []
    mb = d.get("margin_balance")            # 升序日度余额序列
    rank = d.get("sector_rank_change_5d")   # 正数 = 排名下滑(变差)
    if not mb or len(mb) < S2_DECLINE_DAYS + 1:
        missing.append("margin_balance(≥%d日)" % (S2_DECLINE_DAYS + 1))
    if rank is None:
        missing.append("sector_rank_change_5d")
    if missing:
        return None, {}, False, missing
    changes = [b - a for a, b in zip(mb[:-1], mb[1:])]
    last3 = changes[-S2_DECLINE_DAYS:]
    decline_streak = all(c < 0 for c in last3)
    off = changes[-1] > 0                    # 转增 1 日即熄
    ev = {"margin_chg_3d": [round(c, 2) for c in last3],
          "sector_rank_drop_5d": rank,
          "rule": f"融资余额连降{S2_DECLINE_DAYS}日 AND 排名下滑>{S2_RANK_DROP}; 红:3日降幅>|20日日均变化|{S2_RED_SIGMA}σ"}
    level = None
    if decline_streak and rank > S2_RANK_DROP:
        level = "yellow"
        win = changes[-S2_SIGMA_WINDOW:]
        if len(win) >= 5:
            sigma = statistics.pstdev(win)
            drop3 = -sum(last3)
            ev["sigma20"] = round(sigma, 2)
            ev["drop_3d_sum"] = round(drop3, 2)
            ev["score"] = round(drop3 / sigma, 2) if sigma > 0 else 0.0
            if sigma > 0 and drop3 > S2_RED_SIGMA * sigma:
                level = "red"
        else:
            ev["sigma20"] = None
            ev["note"] = "σ窗口不足, 只评黄灯"
        ev.setdefault("score", 1.0)
    return level, ev, off, []


def _eval_s3(d: dict):
    """marker: S3B_FORCE_REV_V1 (2026-07-15 Alex 批, B 案)
    未盈利公司(net_profit_is_loss=True)强制走营收口径, 即使净利YoY可得——
    亏损收窄的净利YoY是负基数算术(亏1亿→亏0.6亿 = +40%), 不作叙事兑现证据;
    未盈利的叙事兑现证据 = 收入放量。盈利性不可判(None)保守沿用净利口径(原A行为)。
    未盈利且营收YoY缺失 → 业绩腿不可算 → 灰灯(I1.1)。"""
    missing = []
    yoy = d.get("net_profit_yoy")
    rev = d.get("revenue_yoy")
    ret20 = d.get("ret_20d")
    beat = d.get("consensus_beat")          # True/False/None(无一致预期)
    is_loss = d.get("net_profit_is_loss")   # True/False/None(不可判)
    if beat is None:
        if is_loss:
            if rev is None:
                missing.append("revenue_yoy(未盈利强制口径S3-B)")
        elif yoy is None and rev is None:
            missing.append("net_profit_yoy/consensus_beat/revenue_yoy")
    if ret20 is None:
        missing.append("ret_20d")
    if missing:
        return None, {}, False, missing
    proxy = None
    if beat is not None:
        earnings_ok = bool(beat)
    elif is_loss:
        earnings_ok = rev > S3_REV_PROXY    # S3-B: 未盈利强制营收口径
        proxy = "revenue_yoy(未盈利强制S3-B)"
    elif yoy is not None:
        earnings_ok = yoy > S3_YOY_PROXY    # 盈利(或不可判): 净利口径, 弱不降级(T10c)
    else:
        earnings_ok = rev > S3_REV_PROXY    # 净利YoY缺失: 营收YoY降级
        proxy = "revenue_yoy(净利YoY缺失降级)"
    ev = {"net_profit_yoy": None if yoy is None else round(yoy, 4),
          "revenue_yoy": None if rev is None else round(rev, 4),
          "net_profit_is_loss": is_loss,
          "consensus_beat": beat, "ret_20d": round(ret20, 4), "score": ret20,
          "rule": (f"业绩超预期(或净利YoY>{S3_YOY_PROXY:.0%}; 未盈利强制营收YoY>{S3_REV_PROXY:.0%}[S3-B], "
                   f"净利YoY缺失同口径降级) AND ret20 黄>{S3_RET20_YELLOW:.0%} 红>{S3_RET20_RED:.0%}; 事件型不迟滞")}
    if proxy:
        ev["proxy"] = proxy
    level = None
    if earnings_ok and ret20 > S3_RET20_YELLOW:
        level = "red" if ret20 > S3_RET20_RED else "yellow"
    return level, ev, False, []


_EVAL = {"S1": _eval_s1, "S2": _eval_s2, "S3": _eval_s3}
_EVENT_SIGNALS = {"S3"}          # 事件型: 触发即进冷却, 不驻留


def _rank(level):  # 强度序
    return {"red": 2, "yellow": 1}.get(level, 0)


# ---------------------------------------------------------------
# 主入口(纯函数)
# ---------------------------------------------------------------
def evaluate_pool(pool: dict, data: dict, state: dict | None, run_date: str):
    """
    pool: {ticker: "V"/"T"/"N"} — 只处理三分法池 15 只
    data: {ticker: {ret_5d, margin_balance, sector_rank_change_5d,
                    net_profit_yoy, consensus_beat, ret_20d, name?}}
    state: 上次返回的 new_state(None = 冷启动)
    run_date: "YYYY-MM-DD"
    返回 (signals: list[MigrationSignal], new_state: dict)
    幂等: 同一 run_date 重跑不重复扣冷却, 结果一致。
    """
    st = {"last_run_date": None, "records": {}} if not state else {
        "last_run_date": state.get("last_run_date"),
        "records": {k: dict(v) for k, v in state.get("records", {}).items()},
    }
    new_day = st["last_run_date"] != run_date
    if new_day:
        for rec in st["records"].values():          # 冷却按运行日递减
            if rec.get("status") == "cooldown" and rec.get("cooldown_remaining", 0) > 0:
                rec["cooldown_remaining"] -= 1
        st["records"] = {k: r for k, r in st["records"].items()
                         if not (r.get("status") == "cooldown" and r.get("cooldown_remaining", 0) <= 0)}
    st["last_run_date"] = run_date

    out: list[MigrationSignal] = []
    for ticker, cls in pool.items():
        sig = SIGNAL_BY_CLASS.get(cls)
        if not sig:
            continue
        frm, to = SIGNAL_ROUTE[sig]
        level, ev, off, missing = _EVAL[sig](data.get(ticker, {}) or {})
        key = f"{ticker}:{sig}"
        rec = st["records"].get(key)

        if missing:                                  # 灰灯: 不可算 ≠ 无信号
            out.append(MigrationSignal(ticker, sig, frm, to, "gray",
                       evidence={"missing": missing}, record_id="", first_detected="",
                       last_updated=run_date, note="信号不可算: " + ", ".join(missing)))
            continue

        if rec and rec.get("status") == "active":
            if off:                                  # 迟滞熄灯 -> 进冷却
                rec.update(status="cooldown", cooldown_remaining=COOLDOWN_DAYS,
                           last_updated=run_date)
                continue
            if _rank(level) > _rank(rec.get("strength")):   # 只升不降(降级走熄灯)
                rec["strength"] = level
            rec["evidence"] = ev or rec.get("evidence", {})
            rec["last_updated"] = run_date           # 重复触发合并: 不新增记录
            out.append(MigrationSignal(ticker, sig, frm, to, rec["strength"],
                       evidence=rec["evidence"], record_id=rec["record_id"],
                       first_detected=rec["first_detected"], last_updated=run_date))
            continue

        if rec and rec.get("status") == "cooldown":  # 冷却期内一律压制
            continue

        if level:                                    # 新触发
            rid = uuid.uuid4().hex[:8]
            if sig in _EVENT_SIGNALS:                # 事件型: 亮一次即进冷却
                st["records"][key] = {"status": "cooldown", "strength": level,
                                      "record_id": rid, "first_detected": run_date,
                                      "last_updated": run_date,
                                      "cooldown_remaining": COOLDOWN_DAYS, "evidence": ev}
            else:
                st["records"][key] = {"status": "active", "strength": level,
                                      "record_id": rid, "first_detected": run_date,
                                      "last_updated": run_date, "evidence": ev}
            out.append(MigrationSignal(ticker, sig, frm, to, level, evidence=ev,
                       record_id=rid, first_detected=run_date, last_updated=run_date))

    reds = sorted((s for s in out if s.strength == "red"),
                  key=lambda s: s.evidence.get("score", 0), reverse=True)
    for s in reds[:MAX_RED_HIGHLIGHT]:
        s.highlight = True                            # 其余红灯折叠展示
    return out, st
