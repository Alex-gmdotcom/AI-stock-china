"""
hk_news/ingest.py — openclaw 港股新闻 JSON 入库

v1.0.0 (2026-06-18, Phase 2 Step 3)

入口:
  - ingest_snapshot(dict)         — 直接喂 dict
  - ingest_snapshot_text(str)     — 喂 JSON 字符串 (从 openclaw 粘贴)
  - ingest_snapshot_file(path)    — 从 .json / .txt 文件读

容错策略:
  - JSON 体内有 "Agent: xxx" / "model: yyy" 之类的元数据噪音 (像 2513_hk.txt
    样本里那样混在 JSON 体中间), 自动剥离再 parse.
  - 字段缺失 / 类型错配走 schema.py 的 .from_dict() 兜底, 不会挂.
  - ticker 字段规范化 (走 markets.ticker.parse_ticker 但软导入).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

try:
    from .schema import HKNewsSnapshot, HKNewsParseError
except ImportError:
    try:
        from hk_news.schema import HKNewsSnapshot, HKNewsParseError  # type: ignore
    except ImportError:
        from src.hk_news.schema import HKNewsSnapshot, HKNewsParseError  # type: ignore

# ticker 规范化软导入
try:
    from markets.ticker import parse_ticker as _parse_ticker  # type: ignore
except ImportError:
    try:
        from src.markets.ticker import parse_ticker as _parse_ticker  # type: ignore
    except ImportError:
        _parse_ticker = None  # type: ignore

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# JSON 文本预处理 (剥离 openclaw 模型元数据噪音)
# ---------------------------------------------------------------------------

# 真样本观察 (2513_hk.txt):
#   "announcements": [...]
#   <-- 这里突然插入 "Agent: xiaodian1 | Model: ..." -->
#   <-- 然后 "xiaodian1" 这行又重复出现 -->
#   "analyst_reports": [...]
# 这些行不是合法 JSON token, 需要剥离.

_RE_AGENT_NOISE = re.compile(
    r"^\s*Agent\s*:.*$|^\s*xiaodian\d+\s*$|^\s*model\s*:.*$|^\s*Model\s*:.*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_noise(text: str) -> str:
    """去掉 openclaw 偶尔混进 JSON 体的 'Agent: xxx | Model: yyy' 元行."""
    return _RE_AGENT_NOISE.sub("", text)


def _locate_json_object(text: str) -> str:
    """从可能含前后噪音的文本中拎出最外层 {...} 大对象.

    策略: 找第一个 '{' 和最后一个 '}', 截取中间.
    再走一次 _strip_noise 清掉里面的非 JSON 行.
    """
    text = _strip_noise(text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise HKNewsParseError(
            f"未能在文本中定位 JSON 对象边界 (start={start}, end={end})"
        )
    return text[start: end + 1]


# ---------------------------------------------------------------------------
# Ticker 规范化
# ---------------------------------------------------------------------------

def _normalize_ticker(raw: str) -> str:
    """ '00700.HK' / '0700.HK' / '00700' / 'hk00700' → '00700.HK'.

    走 markets.ticker.parse_ticker 单一 SoT;
    后者不可达时退化为简单的零填充 + .HK 后缀.
    """
    if not raw:
        return ""
    raw = str(raw).strip()
    if _parse_ticker is not None:
        try:
            return _parse_ticker(raw).full_ticker
        except (ValueError, TypeError, AttributeError):
            pass
    # Stub fallback
    if "." in raw:
        code, suf = raw.upper().split(".", 1)
        if suf == "HK":
            return f"{code.zfill(5)}.HK"
        return f"{code}.{suf}"
    if raw.isdigit() and len(raw) <= 5:
        return f"{raw.zfill(5)}.HK"
    return raw


# ---------------------------------------------------------------------------
# 入库入口
# ---------------------------------------------------------------------------

def ingest_snapshot(data: dict, *, mark_ingested_at: bool = True) -> HKNewsSnapshot:
    """从 dict 入库. 容错走 schema.from_dict().

    Args:
        data: 解析后的 JSON dict.
        mark_ingested_at: 是否打入库时间戳 (默认 True; False 用于回放历史).
    """
    if not isinstance(data, dict):
        raise HKNewsParseError(f"data 必须是 dict, 得 {type(data).__name__}")
    snap = HKNewsSnapshot.from_dict(data)
    # 规范化 ticker
    if snap.ticker:
        snap.ticker = _normalize_ticker(snap.ticker)
    if mark_ingested_at:
        snap.ingested_at = datetime.now()
    return snap


def ingest_snapshot_text(text: str, *, mark_ingested_at: bool = True) -> HKNewsSnapshot:
    """从 JSON 文本入库 (容错剥离 openclaw 元数据噪音).

    Args:
        text: 可以是纯 JSON, 也可以是含前后噪音 / 中间夹杂 'Agent: xxx' 的脏文本.
    """
    if not text or not text.strip():
        raise HKNewsParseError("空文本")
    try:
        # 先直接试解析 (干净 JSON 走快路径)
        data = json.loads(text)
    except json.JSONDecodeError:
        # 走清洗 + 截取最外层 {...} 路径
        cleaned = _locate_json_object(text)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise HKNewsParseError(f"JSON 解析失败 (after cleanup): {exc}") from exc
    return ingest_snapshot(data, mark_ingested_at=mark_ingested_at)


def ingest_snapshot_file(
    path: Union[str, Path],
    *,
    mark_ingested_at: bool = True,
) -> HKNewsSnapshot:
    """从文件入库 (.json / .txt 都行, 编码 UTF-8)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return ingest_snapshot_text(text, mark_ingested_at=mark_ingested_at)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[hk_news.ingest v{__version__}] self-test")
    failures: list[str] = []

    # T1: 纯净 JSON 字符串
    sample_clean = json.dumps({
        "ticker": "00700.HK",
        "company_name_zh": "腾讯",
        "company_name_en": "Tencent",
        "market": "HK",
        "snapshot_at": "2026-06-18T19:05:00+08:00",
        "data_window_days": 30,
        "schema_version": "1.0",
        "news": [],
        "announcements": [],
        "analyst_reports": [],
        "sentiment_signals": None,
        "risk_events": [],
        "peer_events": [],
        "data_gaps": [],
    }, ensure_ascii=False)
    snap = ingest_snapshot_text(sample_clean)
    if snap.ticker != "00700.HK":
        failures.append(f"T1.ticker: {snap.ticker}")
    if snap.ingested_at is None:
        failures.append("T1.ingested_at 未被打戳")
    print(f"[T1] clean JSON: PASS (snapshot_id={snap.snapshot_id})")

    # T2: 含 Agent 噪音的脏 JSON
    dirty = """  some preamble noise
Agent: xiaodian1 | Model: deepseek-v4-flash | Provider: deepseek
{
  "ticker": "02513.HK",
  "company_name_zh": "智谱华章",
  "company_name_en": "Zhipu",
  "market": "HK",
  "snapshot_at": "2026-06-18T22:18:00+08:00",
  "data_window_days": 30,
  "schema_version": "1.0",
  "news": [],
  "announcements": [{
    "title": "GLM-5.2",
    "published_at": "2026-06-15T18:30:00+08:00",
    "type": "other",
    "summary": "推出 GLM-5.2",
    "is_material": true,
    "url": ""
  }]
}
xiaodian1
some trailing noise
"""
    snap = ingest_snapshot_text(dirty)
    if snap.ticker != "02513.HK":
        failures.append(f"T2.ticker: {snap.ticker}")
    if len(snap.announcements) != 1:
        failures.append(f"T2.announcements: {len(snap.announcements)}")
    if snap.announcements[0].title != "GLM-5.2":
        failures.append(f"T2.announcement.title: {snap.announcements[0].title}")
    if not any(f.startswith("T2") for f in failures):
        print("[T2] dirty JSON with Agent noise: PASS")

    # T3: ticker 规范化 (短码 → 5 位)
    short_ticker_dirty = json.dumps({
        "ticker": "0700.HK",
        "company_name_zh": "腾讯", "company_name_en": "Tencent",
        "market": "HK", "snapshot_at": "2026-06-18T19:05:00+08:00",
        "data_window_days": 30, "schema_version": "1.0",
    }, ensure_ascii=False)
    snap = ingest_snapshot_text(short_ticker_dirty)
    if snap.ticker != "00700.HK":
        failures.append(f"T3.ticker zfill: 期望 00700.HK 得 {snap.ticker}")
    else:
        print("[T3] ticker normalize (0700.HK → 00700.HK): PASS")

    # T4: 真实样本回归 (3 份 openclaw 输出)
    uploads = Path("/mnt/user-data/uploads")
    expected = {
        "00148_hk.txt": ("00148.HK", "建滔集团"),
        "01810_hk.txt": ("01810.HK", "小米集团-W"),
        "2513_hk.txt": ("02513.HK", "智谱华章"),  # 注: 文件名 2513,但 ticker 02513
    }
    for fname, (exp_ticker, exp_name) in expected.items():
        fpath = uploads / fname
        if not fpath.exists():
            continue
        try:
            snap = ingest_snapshot_file(fpath)
        except Exception as exc:
            failures.append(f"T4.{fname} parse error: {type(exc).__name__}: {exc}")
            continue
        if snap.ticker != exp_ticker:
            failures.append(f"T4.{fname}.ticker: 期望 {exp_ticker} 得 {snap.ticker}")
        if snap.company_name_zh != exp_name:
            failures.append(f"T4.{fname}.company_name_zh: 期望 {exp_name} 得 {snap.company_name_zh}")
        # 至少有 1 条新闻 (除非样本特别小)
        if not (snap.news or snap.announcements or snap.analyst_reports):
            failures.append(f"T4.{fname}: news/announcements/reports 全空")
        if not any(f.startswith(f"T4.{fname}") for f in failures):
            print(
                f"[T4.{fname}] real openclaw output: PASS "
                f"(ticker={snap.ticker}, name={snap.company_name_zh}, "
                f"news={len(snap.news)}, ann={len(snap.announcements)}, "
                f"reports={len(snap.analyst_reports)}, "
                f"risks={len(snap.risk_events)}, peers={len(snap.peer_events)})"
            )

    # T5: empty / invalid input rejection
    for bad_input, label in [
        ("", "empty"),
        ("not json at all just text", "non-json"),
        ('{"ticker": "no_snapshot_at"}', "missing snapshot_at"),
    ]:
        try:
            ingest_snapshot_text(bad_input)
            failures.append(f"T5.{label}: 应抛错")
        except HKNewsParseError:
            pass
        except Exception as exc:
            # 允许 schema 内部的解析异常也通过 (我们在乎"抛了错")
            if not isinstance(exc, (ValueError, TypeError)):
                failures.append(f"T5.{label}: 抛了奇怪的错 {type(exc).__name__}")
    if not any(f.startswith("T5") for f in failures):
        print("[T5] reject empty / invalid / missing required field: PASS")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[hk_news.ingest v{__version__}] self-test PASS")
