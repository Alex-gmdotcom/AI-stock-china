# -*- coding: utf-8 -*-
"""
datasource/breaker.py — 熔断器 (Phase 1)
=======================================
某源连续失败 N 次 → 打开(隔离 M 秒)→ 半开探活 → 成功则关闭 / 失败则重新打开。
注意:只有"连接级异常"算 failure;"无数据"是正常结果,不进熔断。
线程安全(9 路并发)。
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class _State:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 3, open_seconds: float = 300.0,
                 clock: Callable[[], float] = time.time):
        self.fail_threshold = fail_threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._lock = threading.RLock()
        # name → dict(state, failures, opened_at)
        self._st: dict[str, dict] = {}

    def _get(self, name: str) -> dict:
        s = self._st.get(name)
        if s is None:
            s = {"state": _State.CLOSED, "failures": 0, "opened_at": 0.0}
            self._st[name] = s
        return s

    def is_open(self, name: str) -> bool:
        """True = 当前应跳过该源。半开探活时返回 False(放一个请求过去试)。"""
        with self._lock:
            s = self._get(name)
            if s["state"] == _State.OPEN:
                if self._clock() - s["opened_at"] >= self.open_seconds:
                    s["state"] = _State.HALF_OPEN   # 冷却到点 → 半开,放行探活
                    return False
                return True
            return False

    def record_success(self, name: str) -> None:
        with self._lock:
            s = self._get(name)
            s["failures"] = 0
            if s["state"] != _State.CLOSED:
                s["state"] = _State.CLOSED

    def record_failure(self, name: str) -> None:
        with self._lock:
            s = self._get(name)
            if s["state"] == _State.HALF_OPEN:
                # 探活又失败 → 立刻重新打开
                s["state"] = _State.OPEN
                s["opened_at"] = self._clock()
                return
            s["failures"] += 1
            if s["failures"] >= self.fail_threshold:
                s["state"] = _State.OPEN
                s["opened_at"] = self._clock()

    def state_of(self, name: str) -> str:
        with self._lock:
            return self._get(name)["state"]

    def snapshot(self) -> dict:
        with self._lock:
            return {n: dict(s) for n, s in self._st.items()}
