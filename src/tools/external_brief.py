"""
v3.3 外部信源简报桥 — openclaw → ai-hedge-fund 的松耦合通道。

背景：本系统的信源边界是 AKShare（行情+东财新闻+电报快讯），缺少 openclaw
具备的跨平台搜索能力（agent-reach: 微博/微信文章/Twitter/Reddit/雪球等）。
与其让 Python 侧脆弱地调用 openclaw 的 CLI，不如用文件桥解耦：

    openclaw 定时任务（taskflow + agent-reach）
        └─ 每个交易日 07:45 / 17:30 编写外部信源简报
        └─ 写入 ~/.ai-hedge-fund/external_brief.md
    本模块在生成早晚报时读取该文件（仅当 18 小时内更新过），
    注入数据快照的【外部信源简报】段落。

文件不存在/过期/超长都安全降级 —— openclaw 不在线时系统照常运行，
只是少一个信源段落。openclaw 侧任务指令见 openclaw_task.md。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BRIEF_PATH = Path.home() / ".ai-hedge-fund" / "external_brief.md"
MAX_AGE_HOURS = 18      # 超过此时长视为过期，不注入
MAX_CHARS = 4000        # 控制注入 prompt 的 token 量


def read_external_brief(path: Path | None = None) -> str | None:
    """读取外部简报。不存在/过期/为空返回 None（调用方静默跳过）。"""
    p = path or BRIEF_PATH
    try:
        if not p.exists():
            return None
        age_h = (time.time() - p.stat().st_mtime) / 3600
        if age_h > MAX_AGE_HOURS:
            logger.info("外部简报已过期 %.1f 小时，跳过注入: %s", age_h, p)
            return None
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return None
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS] + "\n…（简报超长已截断）"
        stamp = time.strftime("%m-%d %H:%M", time.localtime(p.stat().st_mtime))
        return f"（openclaw 更新于 {stamp}）\n{text}"
    except Exception as ex:
        logger.warning("读取外部简报失败: %s", ex)
        return None


if __name__ == "__main__":
    # 自测: poetry run python src/tools/external_brief.py
    out = read_external_brief()
    print(out if out else f"无可用简报（路径: {BRIEF_PATH}）")
