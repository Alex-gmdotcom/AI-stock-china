# -*- coding: utf-8 -*-
"""
datasource/health.py — 源健康观测 + 告警 (Phase 1)
=================================================
每次 resolve 上报 (source, group, ok, latency_ms, note/err)。
- 滚动窗口内的成功率/次数(内存)。
- 可选 JSONL 落盘(默认 ~/.ai-hedge-fund/datasource_health.jsonl)。
- 可选 webhook 告警(飞书等):成功率跌破阈值 → 调一次 webhook(带冷却)。
  webhook 完全可插拔——本模块不假设飞书接口,由上层注入 callable(payload:dict)。
线程安全。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Callable, Optional


class HealthReporter:
    def __init__(
        self,
        jsonl_path: Optional[str] = None,
        webhook: Optional[Callable[[dict], None]] = None,
        window_sec: float = 3600.0,
        max_events: int = 5000,
        clock: Callable[[], float] = time.time,
    ):
        self._clock = clock
        self.window_sec = window_sec
        self._lock = threading.RLock()
        # source → deque[(ts, ok)]
        self._events: dict[str, deque] = {}
        self._max_events = max_events
        self._webhook = webhook
        # 告警冷却:source → last_alert_ts
        self._alert_ts: dict[str, float] = {}
        self.alert_rate_threshold = 0.5   # 成功率低于此 → 告警
        self.alert_min_samples = 5        # 窗口内样本数达到才评估
        self.alert_cooldown = 600.0       # 同一源两次告警最小间隔(秒)

        if jsonl_path is None:
            jsonl_path = os.path.join(
                os.path.expanduser("~"), ".ai-hedge-fund", "datasource_health.jsonl")
        self.jsonl_path = jsonl_path
        try:
            os.makedirs(os.path.dirname(self.jsonl_path), exist_ok=True)
        except Exception:
            self.jsonl_path = None  # 落盘失败不影响主流程

    def report(self, source: str, group, ok: bool,
               latency_ms: Optional[float] = None,
               note: Optional[str] = None) -> None:
        now = self._clock()
        gval = getattr(group, "value", str(group))
        with self._lock:
            dq = self._events.setdefault(source, deque(maxlen=self._max_events))
            dq.append((now, bool(ok)))
            self._maybe_alert(source, now)
        # 落盘(锁外)
        if self.jsonl_path:
            rec = {"ts": round(now, 3), "source": source, "group": gval,
                   "ok": bool(ok), "latency_ms": latency_ms, "note": note}
            try:
                with open(self.jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def _recent(self, source: str, now: float) -> list:
        dq = self._events.get(source)
        if not dq:
            return []
        lo = now - self.window_sec
        return [ok for (ts, ok) in dq if ts >= lo]

    def success_rate(self, source: str) -> Optional[float]:
        with self._lock:
            recent = self._recent(source, self._clock())
        if not recent:
            return None
        return sum(1 for ok in recent if ok) / len(recent)

    def _maybe_alert(self, source: str, now: float) -> None:
        if self._webhook is None:
            return
        recent = self._recent(source, now)
        if len(recent) < self.alert_min_samples:
            return
        rate = sum(1 for ok in recent if ok) / len(recent)
        if rate >= self.alert_rate_threshold:
            return
        last = self._alert_ts.get(source, 0.0)
        if now - last < self.alert_cooldown:
            return
        self._alert_ts[source] = now
        payload = {"type": "datasource_degraded", "source": source,
                   "success_rate": round(rate, 3), "samples": len(recent),
                   "window_sec": self.window_sec, "ts": round(now, 3)}
        try:
            self._webhook(payload)
        except Exception:
            pass

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            out = {}
            for src in self._events:
                recent = self._recent(src, now)
                n = len(recent)
                out[src] = {
                    "samples": n,
                    "success_rate": (round(sum(1 for ok in recent if ok) / n, 3)
                                     if n else None),
                }
            return out
