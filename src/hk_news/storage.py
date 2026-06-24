"""
hk_news/storage.py — 港股新闻快照文件系统存档

v1.0.0 (2026-06-18, Phase 2 Step 3)

布局:
    ~/.ai-hedge-fund/hk_news/
        00700.HK/
            00700.HK_20260618T190500.json
            00700.HK_20260619T093000.json
        02513.HK/
            02513.HK_20260618T221800.json
        index.json                   — 全库索引

按 ticker 分目录, 一个 ticker 可以有任意多次快照.
所有原始 JSON 完整保留 (审计可追溯), 无衍生加工.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union

try:
    from .schema import HKNewsSnapshot, HKNewsNotFoundError
except ImportError:
    try:
        from hk_news.schema import HKNewsSnapshot, HKNewsNotFoundError  # type: ignore
    except ImportError:
        from src.hk_news.schema import HKNewsSnapshot, HKNewsNotFoundError  # type: ignore

__version__ = "1.0.0"


def default_storage_path() -> Path:
    """默认存档根 ~/.ai-hedge-fund/hk_news/."""
    return Path.home() / ".ai-hedge-fund" / "hk_news"


def _atomic_write_json(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class HKNewsStorage:
    """单仓库实例.

    使用模式:
        store = HKNewsStorage()
        store.save(snap)
        store.latest("00700.HK")
        store.list_snapshots("00700.HK")
        store.list_tickers()
    """

    def __init__(self, root: Optional[Union[str, Path]] = None):
        self.root = Path(root) if root else default_storage_path()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    # ── 路径计算 ─────────────────────────────────────────────────

    def _ticker_dir(self, ticker: str) -> Path:
        return self.root / ticker

    def _snapshot_path(self, snap: HKNewsSnapshot) -> Path:
        return self._ticker_dir(snap.ticker) / f"{snap.snapshot_id}.json"

    def _path_for_id(self, snapshot_id: str) -> Path:
        # snapshot_id = "{ticker}_{YYYYMMDDTHHMMSS}"
        ticker, _ = snapshot_id.split("_", 1) if "_" in snapshot_id else (snapshot_id, "")
        return self._ticker_dir(ticker) / f"{snapshot_id}.json"

    # ── 索引 ────────────────────────────────────────────────────

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {"version": __version__, "entries": {}}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._rebuild_index()

    def _save_index(self, idx: dict) -> None:
        _atomic_write_json(self.index_path, idx)

    def _rebuild_index(self) -> dict:
        entries: dict[str, dict] = {}
        for sub in self.root.iterdir():
            if not sub.is_dir():
                continue
            for jf in sub.glob("*.json"):
                try:
                    d = json.loads(jf.read_text(encoding="utf-8"))
                    ticker = d.get("ticker")
                    snap_at = d.get("snapshot_at")
                    if not (ticker and snap_at):
                        continue
                    sid = jf.stem
                    entries[sid] = {
                        "snapshot_id": sid,
                        "ticker": ticker,
                        "snapshot_at": snap_at,
                        "company_name_zh": d.get("company_name_zh", ""),
                        "ingested_at": d.get("ingested_at"),
                    }
                except (json.JSONDecodeError, OSError):
                    continue
        idx = {"version": __version__, "entries": entries}
        self._save_index(idx)
        return idx

    # ── 主 API ───────────────────────────────────────────────────

    def save(self, snap: HKNewsSnapshot, *, overwrite: bool = True) -> Path:
        """入库. 默认 overwrite=True (同 snapshot_id 视为重跑).

        Returns:
            实际写入的文件路径.
        """
        target = self._snapshot_path(snap)
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"snapshot_id {snap.snapshot_id} 已存在, 用 overwrite=True 覆盖"
            )
        _atomic_write_json(target, snap.to_dict())

        idx = self._load_index()
        idx["entries"][snap.snapshot_id] = {
            "snapshot_id": snap.snapshot_id,
            "ticker": snap.ticker,
            "snapshot_at": snap.snapshot_at.isoformat(),
            "company_name_zh": snap.company_name_zh,
            "ingested_at": snap.ingested_at.isoformat() if snap.ingested_at else None,
        }
        self._save_index(idx)
        return target

    def get(self, snapshot_id: str) -> HKNewsSnapshot:
        p = self._path_for_id(snapshot_id)
        if not p.exists():
            raise HKNewsNotFoundError(snapshot_id)
        d = json.loads(p.read_text(encoding="utf-8"))
        return HKNewsSnapshot.from_dict(d)

    def latest(self, ticker: str) -> HKNewsSnapshot:
        """返回该 ticker 最新一次快照."""
        snaps = self.list_snapshots(ticker)
        if not snaps:
            raise HKNewsNotFoundError(f"{ticker} (no snapshots)")
        # snaps 已按时间倒序
        return self.get(snaps[0]["snapshot_id"])

    def list_snapshots(self, ticker: Optional[str] = None) -> list[dict]:
        """列出快照 (索引轻量版).

        Args:
            ticker: 不传 → 全部 ticker;传 → 该 ticker 全部快照.

        Returns:
            按 snapshot_at 倒序的轻量索引条目.
        """
        idx = self._load_index()
        entries = list(idx["entries"].values())
        if ticker:
            entries = [e for e in entries if e.get("ticker") == ticker]
        entries.sort(key=lambda e: e.get("snapshot_at", ""), reverse=True)
        return entries

    def list_tickers(self) -> list[str]:
        """返回所有有过快照的 ticker (去重排序)."""
        idx = self._load_index()
        tickers = {e["ticker"] for e in idx["entries"].values() if e.get("ticker")}
        return sorted(tickers)

    def delete_snapshot(self, snapshot_id: str) -> bool:
        p = self._path_for_id(snapshot_id)
        existed = p.exists()
        if existed:
            p.unlink()
        idx = self._load_index()
        if snapshot_id in idx["entries"]:
            del idx["entries"][snapshot_id]
            self._save_index(idx)
            existed = True
        return existed


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from datetime import datetime as _dt

    print(f"[hk_news.storage v{__version__}] self-test")
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        store = HKNewsStorage(td)

        # 两份不同 ticker 各 1 个快照, 同 ticker 1 ticker 2 个时间点的快照
        s1 = HKNewsSnapshot(
            ticker="00700.HK", company_name_zh="腾讯", company_name_en="Tencent",
            market="HK", snapshot_at=_dt(2026, 6, 18, 19, 5, 0),
            data_window_days=30, schema_version="1.0",
            ingested_at=_dt(2026, 6, 18, 19, 6, 0),
        )
        s2 = HKNewsSnapshot(
            ticker="00700.HK", company_name_zh="腾讯", company_name_en="Tencent",
            market="HK", snapshot_at=_dt(2026, 6, 19, 9, 30, 0),  # 后一天
            data_window_days=30, schema_version="1.0",
            ingested_at=_dt(2026, 6, 19, 9, 31, 0),
        )
        s3 = HKNewsSnapshot(
            ticker="02513.HK", company_name_zh="智谱华章", company_name_en="Zhipu",
            market="HK", snapshot_at=_dt(2026, 6, 18, 22, 18, 0),
            data_window_days=30, schema_version="1.0",
            ingested_at=_dt(2026, 6, 18, 22, 20, 0),
        )

        # T1: save + get round-trip
        p1 = store.save(s1)
        store.save(s2)
        store.save(s3)
        if not p1.exists():
            failures.append(f"T1: 写入文件不存在 {p1}")
        rs = store.get(s1.snapshot_id)
        if rs.ticker != "00700.HK":
            failures.append(f"T1: round-trip ticker {rs.ticker}")
        if rs.company_name_zh != "腾讯":
            failures.append("T1: company_name_zh missing")
        print("[T1] save + get round-trip: PASS")

        # T2: latest 返回最新
        latest = store.latest("00700.HK")
        if latest.snapshot_id != s2.snapshot_id:
            failures.append(f"T2: latest 应该是 s2 ({s2.snapshot_id}) 得 {latest.snapshot_id}")
        print(f"[T2] latest('00700.HK'): PASS (got {latest.snapshot_id})")

        # T3: list_snapshots (全部 + 单 ticker)
        all_snaps = store.list_snapshots()
        if len(all_snaps) != 3:
            failures.append(f"T3.all: {len(all_snaps)} != 3")
        tencent_snaps = store.list_snapshots("00700.HK")
        if len(tencent_snaps) != 2:
            failures.append(f"T3.00700: {len(tencent_snaps)} != 2")
        if tencent_snaps[0]["snapshot_id"] != s2.snapshot_id:
            failures.append(f"T3.00700 ordering: {[s['snapshot_id'] for s in tencent_snaps]}")
        print(f"[T3] list_snapshots: PASS (all={len(all_snaps)}, 00700={len(tencent_snaps)})")

        # T4: list_tickers
        tickers = store.list_tickers()
        if tickers != ["00700.HK", "02513.HK"]:
            failures.append(f"T4: {tickers}")
        print(f"[T4] list_tickers: PASS ({tickers})")

        # T5: delete
        store.delete_snapshot(s1.snapshot_id)
        if store.list_snapshots("00700.HK") and \
           any(s["snapshot_id"] == s1.snapshot_id for s in store.list_snapshots()):
            failures.append("T5: s1 删除后仍在索引")
        if len(store.list_snapshots("00700.HK")) != 1:
            failures.append(f"T5: 删 s1 后剩 {len(store.list_snapshots('00700.HK'))} 条")
        print("[T5] delete_snapshot: PASS")

        # T6: index 损坏自愈
        store.index_path.write_text("BROKEN {{{", encoding="utf-8")
        snaps = store.list_snapshots()  # 应触发 rebuild
        if len(snaps) != 2:  # s1 删了, s2 + s3 还在
            failures.append(f"T6 rebuild: 期望 2 得 {len(snaps)}")
        print("[T6] corrupt index self-heal: PASS")

        # T7: not found
        try:
            store.get("no_such_snapshot_id")
            failures.append("T7: 不存在应抛 HKNewsNotFoundError")
        except HKNewsNotFoundError:
            pass
        print("[T7] HKNewsNotFoundError on missing: PASS")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  ✗ {f}")
        raise SystemExit(1)
    print(f"\n[hk_news.storage v{__version__}] self-test PASS (7 groups)")
