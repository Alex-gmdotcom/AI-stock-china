# -*- coding: utf-8 -*-
"""
datasource/feishu_alert.py — 飞书自定义机器人告警适配器 (Phase 1, v1.0.0)
========================================================================
把 HealthReporter 的告警 payload 推送到飞书群自定义机器人。
零第三方依赖(用标准库 urllib)。配置走环境变量,未配置则返回 None(静默不告警)。

配置:
  AIHF_FEISHU_WEBHOOK  飞书机器人 webhook 地址
                       (https://open.feishu.cn/open-apis/bot/v2/hook/xxxx)
  AIHF_FEISHU_SECRET   可选,开启"签名校验"安全设置时填(机器人的签名密钥)

用法:
  from datasource import feishu_alert, HealthReporter
  hr = HealthReporter(webhook=feishu_alert.make_feishu_webhook())
  # 或注入已有的 reporter: reporter._webhook = feishu_alert.make_feishu_webhook()

飞书若启用了"自定义关键词"安全设置,确保 keyword 命中(默认含"数据源告警")。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _gen_sign(timestamp: str, secret: str) -> str:
    """飞书签名:HMAC-SHA256(key=f'{ts}\\n{secret}', msg=空) → base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    h = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(h).decode("utf-8")


def format_alert(payload: dict, keyword: str = "数据源告警") -> str:
    """把告警 payload 格式化为飞书文本。"""
    if payload.get("type") == "datasource_degraded":
        ts = payload.get("ts", time.time())
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        win_min = int(payload.get("window_sec", 0) / 60) or 0
        return (f"【{keyword}】数据源降级\n"
                f"源: {payload.get('source')}\n"
                f"成功率: {payload.get('success_rate')} "
                f"({payload.get('samples')} 样本 / {win_min} 分钟窗口)\n"
                f"时间: {when}")
    return f"【{keyword}】{json.dumps(payload, ensure_ascii=False)}"


def _build_body(payload: dict, secret: Optional[str], keyword: str) -> dict:
    body = {"msg_type": "text", "content": {"text": format_alert(payload, keyword)}}
    if secret:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = _gen_sign(ts, secret)
    return body


def _post(url: str, body: dict, timeout: float) -> dict:
    """POST 到飞书,解析返回。飞书即使拒收也回 HTTP 200,错误在 body.code。

    返回 {ok, http, code, msg, raw}。code==0 才算真正成功。
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        http = resp.status
    try:
        j = json.loads(raw)
    except Exception:
        j = {}
    code = j.get("code", j.get("StatusCode"))
    msg = j.get("msg") or j.get("StatusMessage") or ""
    return {"ok": (code == 0), "http": http, "code": code, "msg": msg, "raw": raw[:300]}


def diagnose(payload: Optional[dict] = None, url: Optional[str] = None,
             secret: Optional[str] = None, keyword: str = "数据源告警",
             timeout: float = 5.0) -> dict:
    """显式发一条并返回飞书的真实响应(供部署/排障打印)。"""
    url = url or os.environ.get("AIHF_FEISHU_WEBHOOK")
    secret = secret or os.environ.get("AIHF_FEISHU_SECRET")
    if not url:
        return {"ok": False, "code": None, "msg": "未配置 AIHF_FEISHU_WEBHOOK", "raw": ""}
    if payload is None:
        payload = {"type": "datasource_degraded", "source": "__自检__",
                   "success_rate": 1.0, "samples": 1, "window_sec": 3600, "ts": time.time()}
    try:
        return _post(url, _build_body(payload, secret, keyword), timeout)
    except Exception as exc:
        return {"ok": False, "code": None, "msg": str(exc)[:160], "raw": ""}


def make_feishu_webhook(
    url: Optional[str] = None,
    secret: Optional[str] = None,
    keyword: str = "数据源告警",
    timeout: float = 5.0,
) -> Optional[Callable[[dict], bool]]:
    """构造一个 send(payload)->bool 回调;未配置 webhook → 返回 None(静默)。

    校验飞书返回 body.code==0 才算成功(HTTP 200 不代表送达)。
    可显式传 url/secret,否则读环境变量 AIHF_FEISHU_WEBHOOK / AIHF_FEISHU_SECRET。
    """
    url = url or os.environ.get("AIHF_FEISHU_WEBHOOK")
    secret = secret or os.environ.get("AIHF_FEISHU_SECRET")
    if not url:
        return None

    def send(payload: dict) -> bool:
        try:
            r = _post(url, _build_body(payload, secret, keyword), timeout)
        except Exception as exc:
            logger.warning("飞书告警发送失败: %s", str(exc)[:120])
            return False
        if not r["ok"]:
            logger.warning("飞书告警被拒: http=%s code=%s msg=%s",
                           r.get("http"), r.get("code"), r.get("msg"))
        return r["ok"]

    return send
