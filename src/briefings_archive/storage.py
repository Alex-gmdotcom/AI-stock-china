"""
briefings_archive/storage.py — Briefing 文件系统存储 + 查询

v1.0.0 (2026-06-18, Phase 2 Step 2)

布局:
    ~/.ai-hedge-fund/briefings/
        2026-06-18_morning.md           # 原始正文 (markdown / 半结构化文本)
        2026-06-18_morning.meta.json    # 抽取的 metadata sidecar
        2026-06-18_evening.md
        2026-06-18_evening.meta.json
        2026-06-W25_weekly.md
        2026-06-W25_weekly.meta.json
        index.json                       # 整库索引 (按 briefing_id → 简化记录)

设计要点:
  - 原文用 .md 后缀,任何 markdown 阅读器都能开
  - metadata 单独存 .meta.json,正文受损也能从 sidecar 恢复元数据
  - index.json 提供快速 listing,不存正文 (避免 IO 放大)
  - 重名按 briefing_id 唯一 (date + type 组合天然去重)
  - 写入用 .tmp + atomic rename,与 Phase 1 strategy.three_categories 风格一致
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional, Union

try:
    from .schema import (
        Briefing, BriefingMetadata, BriefingType, BriefingNotFoundError,
    )
except ImportError:
    try:
        from briefings_archive.schema import (  # type: ignore
            Briefing, BriefingMetadata, BriefingType, BriefingNotFoundError,
        )
    except ImportError:
        from src.briefings_archive.schema import (  # type: ignore
            Briefing, BriefingMetadata, BriefingType, BriefingNotFoundError,
        )

__version__ = "1.0.0"


# ---------------------------------------------------------------------------
# 默认路径
# ---------------------------------------------------------------------------

def default_storage_path() -> Path:
    """返回默认存储根 ~/.ai-hedge-fund/briefings/.

    与 v3.5 strategy/briefing_generator.py 的 BriefingHistory.daily_dir 同根目录,
    方便老用户无缝迁移 (老的 .md 文件如果命名规则一致, 可以直接被本模块识别).
    """
    return Path.home() / ".ai-hedge-fund" / "briefings"


# ---------------------------------------------------------------------------
# 原子写入工具
# ---------------------------------------------------------------------------

def _atomic_write_text(target: Path, content: str) -> None:
    """写 .tmp + rename, Windows / POSIX 都安全."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        # Windows 下 os.replace 也是原子的, 且能跨 fs
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_json(target: Path, data: dict) -> None:
    _atomic_write_text(target, json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class BriefingStorage:
    """单仓库实例.

    使用模式:
        store = BriefingStorage()              # 默认路径
        store.save(briefing)                    # 入库
        store.list_by_date(date(2026, 6, 18))   # 按日期查
        store.get("2026-06-18_morning")         # 按 ID 查
        store.recent(n=10)                      # 取最新 N 条
    """

    def __init__(self, root: Optional[Union[str, Path]] = None):
        self.root = Path(root) if root else default_storage_path()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    # ── 路径计算 ─────────────────────────────────────────────────

    def _content_path(self, briefing_id: str) -> Path:
        return self.root / f"{briefing_id}.md"

    def _meta_path(self, briefing_id: str) -> Path:
        return self.root / f"{briefing_id}.meta.json"

    # ── 索引维护 ─────────────────────────────────────────────────

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {"version": __version__, "entries": {}}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 损坏 → 重建 (扫文件系统)
            return self._rebuild_index()

    def _save_index(self, idx: dict) -> None:
        _atomic_write_json(self.index_path, idx)

    def _rebuild_index(self) -> dict:
        """扫盘重建 index.json (索引损坏 / 老数据迁移用)."""
        entries = {}
        for meta_file in self.root.glob("*.meta.json"):
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
                bid = m.get("briefing_id")
                if bid:
                    entries[bid] = {
                        "briefing_id": bid,
                        "briefing_type": m.get("briefing_type"),
                        "briefing_date": m.get("briefing_date"),
                        "ingested_at": m.get("ingested_at"),
                        "source": m.get("source"),
                    }
            except (json.JSONDecodeError, OSError):
                continue
        idx = {"version": __version__, "entries": entries}
        self._save_index(idx)
        return idx

    # ── 主 API ───────────────────────────────────────────────────

    def save(self, briefing: Briefing, *, overwrite: bool = False) -> None:
        """入库. 默认 briefing_id 已存在时报错; overwrite=True 覆盖."""
        cpath = self._content_path(briefing.briefing_id)
        mpath = self._meta_path(briefing.briefing_id)
        if cpath.exists() and not overwrite:
            raise FileExistsError(
                f"briefing_id {briefing.briefing_id!r} 已存在 "
                f"({cpath}), 用 overwrite=True 覆盖"
            )

        # 写原文
        _atomic_write_text(cpath, briefing.raw_content)

        # 写 sidecar (整 dict 含正文位置信息,但不重复正文)
        d = briefing.to_dict()
        # 把 raw_content 路径化以节省 sidecar 大小
        d_meta = {k: v for k, v in d.items() if k != "raw_content"}
        d_meta["content_file"] = cpath.name
        _atomic_write_json(mpath, d_meta)

        # 更新索引
        idx = self._load_index()
        idx["entries"][briefing.briefing_id] = {
            "briefing_id": briefing.briefing_id,
            "briefing_type": briefing.briefing_type.value,
            "briefing_date": briefing.briefing_date.isoformat(),
            "ingested_at": briefing.ingested_at.isoformat(),
            "source": briefing.source,
        }
        self._save_index(idx)

    def get(self, briefing_id: str) -> Briefing:
        """按 ID 取出完整 Briefing."""
        mpath = self._meta_path(briefing_id)
        cpath = self._content_path(briefing_id)
        if not mpath.exists():
            raise BriefingNotFoundError(f"{briefing_id} (no meta)")
        if not cpath.exists():
            raise BriefingNotFoundError(f"{briefing_id} (no content)")
        meta_dict = json.loads(mpath.read_text(encoding="utf-8"))
        # 重组完整 dict 喂给 from_dict
        meta_dict["raw_content"] = cpath.read_text(encoding="utf-8")
        # 兼容: 把 content_file 字段去掉再传给 from_dict
        meta_dict.pop("content_file", None)
        return Briefing.from_dict(meta_dict)

    def delete(self, briefing_id: str) -> bool:
        """删除一条. 返回是否真的删了 (不存在则 False)."""
        cpath = self._content_path(briefing_id)
        mpath = self._meta_path(briefing_id)
        existed = False
        for p in (cpath, mpath):
            if p.exists():
                p.unlink()
                existed = True
        idx = self._load_index()
        if briefing_id in idx["entries"]:
            del idx["entries"][briefing_id]
            self._save_index(idx)
            existed = True
        return existed

    def exists(self, briefing_id: str) -> bool:
        return self._content_path(briefing_id).exists()

    # ── 查询 ─────────────────────────────────────────────────────

    def list_all(self) -> list[dict]:
        """返回索引中所有条目 (轻量, 不读正文)."""
        return sorted(
            self._load_index()["entries"].values(),
            key=lambda e: e.get("briefing_date", "") + e.get("briefing_type", ""),
        )

    def list_by_date(self, d: Union[date, str]) -> list[dict]:
        if isinstance(d, date):
            d_str = d.isoformat()
        else:
            d_str = d
        return [e for e in self.list_all() if e.get("briefing_date") == d_str]

    def list_by_type(self, btype: BriefingType) -> list[dict]:
        return [e for e in self.list_all() if e.get("briefing_type") == btype.value]

    def recent(self, n: int = 10) -> list[dict]:
        """返回最近 n 条 (按 briefing_date 倒序)."""
        all_entries = sorted(
            self._load_index()["entries"].values(),
            key=lambda e: (e.get("briefing_date", ""), e.get("ingested_at", "")),
            reverse=True,
        )
        return all_entries[:n]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    print(f"[briefings_archive.storage v{__version__}] self-test")
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        store = BriefingStorage(td)

        # 建两份 mock briefing
        b_morning = Briefing(
            briefing_id="2026-06-18_morning",
            briefing_type=BriefingType.MORNING,
            briefing_date=date(2026, 6, 18),
            raw_content="📅 早报正文,长得很长很长。" * 50,
            metadata=BriefingMetadata(score_grade="B", score_value=74,
                                       tickers_mentioned=["T1", "中际旭创"]),
            ingested_at=datetime(2026, 6, 18, 8, 35, 0),
        )
        b_evening = Briefing(
            briefing_id="2026-06-18_evening",
            briefing_type=BriefingType.EVENING,
            briefing_date=date(2026, 6, 18),
            raw_content="📅 晚报正文,有大量分析内容...",
            metadata=BriefingMetadata(score_grade="B", score_value=69,
                                       cocoon_signals=["科技换锚"]),
            ingested_at=datetime(2026, 6, 18, 16, 5, 0),
        )

        # T1: save + get round-trip
        store.save(b_morning)
        store.save(b_evening)
        rb = store.get("2026-06-18_morning")
        if rb.briefing_id != "2026-06-18_morning":
            failures.append(f"T1.id mismatch: {rb.briefing_id}")
        if rb.raw_content != b_morning.raw_content:
            failures.append(f"T1.raw_content corrupted")
        if rb.metadata.score_value != 74:
            failures.append(f"T1.metadata: {rb.metadata.score_value}")
        if rb.briefing_type != BriefingType.MORNING:
            failures.append(f"T1.type: {rb.briefing_type}")
        if not failures:
            print("[T1] save + get round-trip: PASS")

        # T2: 重写报错, overwrite=True 允许
        try:
            store.save(b_morning)
            failures.append("T2.no-overwrite 应该报错")
        except FileExistsError:
            pass
        store.save(b_morning, overwrite=True)
        print("[T2] overwrite protection: PASS")

        # T3: 查询
        same_day = store.list_by_date(date(2026, 6, 18))
        if len(same_day) != 2:
            failures.append(f"T3.list_by_date count: {len(same_day)}")
        by_type = store.list_by_type(BriefingType.MORNING)
        if len(by_type) != 1:
            failures.append(f"T3.list_by_type count: {len(by_type)}")
        if by_type[0]["briefing_id"] != "2026-06-18_morning":
            failures.append(f"T3.list_by_type wrong id")
        if len([f for f in failures if f.startswith("T3")]) == 0:
            print(f"[T3] list_by_date / list_by_type: PASS "
                  f"(same_day={len(same_day)}, morning={len(by_type)})")

        # T4: recent
        recent = store.recent(5)
        if len(recent) != 2:
            failures.append(f"T4.recent count: {len(recent)}")
        # 应该按 date desc + ingested_at desc, evening (16:05) 在前
        if recent[0]["briefing_id"] != "2026-06-18_evening":
            failures.append(f"T4.recent ordering: {[r['briefing_id'] for r in recent]}")
        if len([f for f in failures if f.startswith("T4")]) == 0:
            print(f"[T4] recent sort order: PASS")

        # T5: delete
        ok = store.delete("2026-06-18_morning")
        if not ok:
            failures.append("T5.delete should return True")
        if store.exists("2026-06-18_morning"):
            failures.append("T5.delete: file still exists")
        if len(store.list_all()) != 1:
            failures.append(f"T5.delete: index has {len(store.list_all())}, expected 1")
        if not store.delete("nonexistent_id"):
            pass  # 期望 False
        else:
            failures.append("T5.delete nonexistent should return False")
        if len([f for f in failures if f.startswith("T5")]) == 0:
            print("[T5] delete + nonexistent handling: PASS")

        # T6: index rebuild (索引损坏自愈)
        store.index_path.write_text("BROKEN JSON GARBAGE {{{", encoding="utf-8")
        # 加载应触发 rebuild
        recent2 = store.recent(5)
        if len(recent2) != 1:  # 之前删了 morning, 剩 evening
            failures.append(f"T6.rebuild: expected 1 entry got {len(recent2)}")
        if len([f for f in failures if f.startswith("T6")]) == 0:
            print("[T6] corrupt index self-heal via rebuild: PASS")

        # T7: not found
        try:
            store.get("does_not_exist")
            failures.append("T7.get nonexistent should raise BriefingNotFoundError")
        except BriefingNotFoundError:
            pass
        print("[T7] BriefingNotFoundError on missing: PASS")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[briefings_archive.storage v{__version__}] self-test PASS (7 groups)")
