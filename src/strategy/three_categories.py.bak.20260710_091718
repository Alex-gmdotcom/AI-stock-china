"""
strategy/three_categories.py — 三分法池管理（加固版 v1.0.1）

对应不变量：
    I4.1: 池规模锁定 V5 + T5 + N5
    I4.2: 月度迁移上限（每月最多 2 次配对迁移）
    I4.3: 迁移操作仅手动触发
    I4.4: 池状态持久化健壮（.tmp + atomic rename + 7 天滚动 backup）
    I4.5: 迁移记录含完整 evidence 字段 + user_rationale 必填

设计要点：
    操作分两类，互不交叉：

    1. **配对迁移** execute_migration_pair(exit_entry, enter_entry, ...)
       - 一次操作同时 (a) 把一只票移出某池 (b) 另一只票移入某池
       - 保持池规模 5+5+5 不变
       - 月度配额 +1（上限 2 次）
       - 这是常规工作流（周末复盘后的"换仓"动作）

    2. **池初始化 / 池修正** add_initial_entry / remove_entry
       - 首次配池 / 异常维护
       - 不消耗月度配额
       - 不保证调用后立刻 5+5+5（允许中间状态）
       - 但 save_pool_state 时会校验最终状态

为什么这样设计：
    v3.5 教训：单步迁移 API 与 5+5+5 不变量天然冲突。Phase 1 自测立刻
    暴露了这个问题，trade off：换成配对 API 让常规工作流更安全。

v3.5 持久化教训：
    - Pydantic v2 的 model_dump_json(ensure_ascii=False) 不支持
      → 用 json.dumps(state.to_dict(), ensure_ascii=False)
    - 直接 write 出现写到一半 kill 进程 → 池状态损坏
      → 必须 .tmp + atomic rename
    - 损坏后无 backup 可恢复
      → 7 天滚动 backup

自测：
    python -m strategy.three_categories
"""

import copy
import json
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Literal, Optional, Tuple

__version__ = "v1.0.1"


# =====================================================================
# 数据模型
# =====================================================================

Category = Literal["V", "T", "N"]


@dataclass
class PoolEntry:
    ticker: str            # 标准化形式 "600519.SH" / "00700.HK"
    name: str
    category: Category
    sub_id: str            # "V1" / "T3" / "N2"
    rationale: str         # 入池理由（短）
    added_at: str          # ISO datetime
    last_migrated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PoolEntry":
        return cls(**d)


@dataclass
class MigrationRecord:
    record_id: str
    pair_id: str           # 同一次配对迁移的两条记录共享 pair_id
    ticker: str
    from_category: Category
    to_category: Category
    signal: str            # "T2V_PROSPERITY_WEAKEN_3D" / "manual" / etc.
    evidence: List[dict] = field(default_factory=list)
    user_rationale: str = ""
    decided_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MigrationRecord":
        return cls(**d)


@dataclass
class PoolState:
    v_pool: List[PoolEntry] = field(default_factory=list)
    t_pool: List[PoolEntry] = field(default_factory=list)
    n_pool: List[PoolEntry] = field(default_factory=list)
    watchlist: List[PoolEntry] = field(default_factory=list)
    migrations_this_month: List[MigrationRecord] = field(default_factory=list)
    last_modified: str = ""
    schema_version: str = "1.0"

    def to_dict(self) -> dict:
        return {
            "v_pool": [e.to_dict() for e in self.v_pool],
            "t_pool": [e.to_dict() for e in self.t_pool],
            "n_pool": [e.to_dict() for e in self.n_pool],
            "watchlist": [e.to_dict() for e in self.watchlist],
            "migrations_this_month": [m.to_dict() for m in self.migrations_this_month],
            "last_modified": self.last_modified,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PoolState":
        return cls(
            v_pool=[PoolEntry.from_dict(e) for e in data.get("v_pool", [])],
            t_pool=[PoolEntry.from_dict(e) for e in data.get("t_pool", [])],
            n_pool=[PoolEntry.from_dict(e) for e in data.get("n_pool", [])],
            watchlist=[PoolEntry.from_dict(e) for e in data.get("watchlist", [])],
            migrations_this_month=[
                MigrationRecord.from_dict(m)
                for m in data.get("migrations_this_month", [])
            ],
            last_modified=data.get("last_modified", ""),
            schema_version=data.get("schema_version", "1.0"),
        )


# =====================================================================
# 错误类型
# =====================================================================

class PoolError(Exception):
    """池管理基类异常。"""


class PoolStateLoadError(PoolError):
    """读盘失败 + backup 链也无法恢复。"""


class PoolSizeInvariantViolated(PoolError):
    """池规模约束被破坏（不是 5+5+5）。"""


class MonthlyMigrationLimitExceeded(PoolError):
    """月度迁移上限超出。"""


class MissingMigrationRationale(PoolError):
    """迁移没填理由。"""


class PoolNotFoundError(PoolError):
    """标的不在指定池里。"""


class DuplicateTickerError(PoolError):
    """同一 ticker 已在某池中（不能重复加入）。"""


# =====================================================================
# 路径 + 常量
# =====================================================================

MONTHLY_MIGRATION_LIMIT = 2  # 每月最多 2 次配对迁移
BACKUP_RETENTION_DAYS = 7


def default_pool_dir() -> Path:
    return Path.home() / ".ai-hedge-fund"


# =====================================================================
# 持久化（加固）
# =====================================================================

def save_pool_state(state: PoolState, dir_override: Optional[Path] = None) -> None:
    """
    原子写盘 + 7 天滚动 backup。崩溃中途不丢数据。

    步骤：
        1. 校验 5+5+5（除非空池）。失败抛 PoolSizeInvariantViolated。
        2. 若主文件已存在，先 backup 到 .bak.{YYYY-MM-DD}（同日覆盖）
        3. 写到 .json.tmp
        4. atomic rename .tmp → .json
        5. 清理 > 7 天的 backup
    """
    assert_pool_size_invariant(state)

    base_dir = dir_override or default_pool_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / "three_categories.json"

    state.last_modified = datetime.now().isoformat()

    # backup 当前文件
    if path.exists():
        today_str = datetime.now().strftime("%Y-%m-%d")
        bak_today = base_dir / f"three_categories.json.bak.{today_str}"
        shutil.copy2(path, bak_today)
        _prune_old_backups(base_dir, days=BACKUP_RETENTION_DAYS)

    # 写 .tmp
    tmp_path = base_dir / "three_categories.json.tmp"
    payload = json.dumps(
        state.to_dict(),
        default=str,
        ensure_ascii=False,
        indent=2,
    )
    tmp_path.write_text(payload, encoding="utf-8")

    # atomic rename
    tmp_path.replace(path)


def load_pool_state(dir_override: Optional[Path] = None) -> PoolState:
    """
    读盘。主文件损坏时尝试 7 天 backup 链。全部失败抛 PoolStateLoadError。
    主文件不存在 → 返回空 PoolState。
    """
    base_dir = dir_override or default_pool_dir()
    path = base_dir / "three_categories.json"

    if not path.exists():
        print(f"  [pool] no existing state at {path}, returning empty PoolState")
        return PoolState()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PoolState.from_dict(data)
    except Exception as primary_error:
        print(f"  [pool] WARN: primary file unreadable: {primary_error}")
        print(f"  [pool] attempting backup chain...")

        baks = sorted(
            base_dir.glob("three_categories.json.bak.*"),
            reverse=True,
        )
        for bak in baks[:BACKUP_RETENTION_DAYS]:
            try:
                data = json.loads(bak.read_text(encoding="utf-8"))
                state = PoolState.from_dict(data)
                print(f"  [pool] recovered from backup: {bak.name}")
                return state
            except Exception as bak_error:
                print(f"  [pool] backup {bak.name} unreadable: {bak_error}")
                continue

        raise PoolStateLoadError(
            f"Pool state unrecoverable. Primary file error: {primary_error}. "
            f"Tried {len(baks)} backup file(s), all failed."
        )


def _prune_old_backups(base_dir: Path, days: int = 7) -> None:
    cutoff = datetime.now() - timedelta(days=days)
    for bak in base_dir.glob("three_categories.json.bak.*"):
        try:
            date_str = bak.name.replace("three_categories.json.bak.", "")
            bak_date = datetime.strptime(date_str, "%Y-%m-%d")
            if bak_date < cutoff:
                bak.unlink()
                print(f"  [pool] pruned old backup: {bak.name}")
        except Exception:
            continue


# =====================================================================
# 不变量校验
# =====================================================================

def assert_pool_size_invariant(state: PoolState) -> None:
    """I4.1: V/T/N 必须各恰好 5 只（或全部为空 = 初始状态）。"""
    sizes = (len(state.v_pool), len(state.t_pool), len(state.n_pool))
    if sizes == (0, 0, 0):
        return  # 初始状态允许全空
    if sizes != (5, 5, 5):
        raise PoolSizeInvariantViolated(
            f"Pool sizes (V={sizes[0]}, T={sizes[1]}, N={sizes[2]}) "
            f"must each be 5 (got {sizes})"
        )


def get_monthly_migration_count(state: PoolState) -> int:
    """
    返回本月已用的配对迁移次数。

    注意：每次配对迁移会产生 2 条 MigrationRecord（出 + 进），
    但它们共享 pair_id，所以本函数按 pair_id 去重计数。
    """
    now = datetime.now()
    pair_ids = set()
    for m in state.migrations_this_month:
        try:
            t = datetime.fromisoformat(m.decided_at)
            if t.year == now.year and t.month == now.month:
                pair_ids.add(m.pair_id)
        except Exception:
            continue
    return len(pair_ids)


def find_entry(state: PoolState, ticker: str) -> Optional[Tuple[Category, PoolEntry]]:
    """在所有池里找 ticker。返回 (category, entry) 或 None。"""
    for cat, pool in [
        ("V", state.v_pool),
        ("T", state.t_pool),
        ("N", state.n_pool),
    ]:
        for e in pool:
            if e.ticker == ticker:
                return (cat, e)  # type: ignore
    return None


# =====================================================================
# 配对迁移（常规工作流）
# =====================================================================

@dataclass
class MigrationLeg:
    """配对迁移的一条腿。"""
    ticker: str
    from_category: Category
    to_category: Category
    signal: str = "manual"
    evidence: List[dict] = field(default_factory=list)


def execute_migration_pair(
    state: PoolState,
    exit_leg: MigrationLeg,
    enter_leg: MigrationLeg,
    user_rationale: str,
    dir_override: Optional[Path] = None,
) -> PoolState:
    """
    配对迁移：一次操作同时执行两个标的的迁移，保持池规模 5+5+5 不变。

    典型场景：
        周末复盘后，把 V 池中涨幅过高的 V3 转到 T 池，同时把 T 池中景气走弱
        的 T2 转到 V 池。这是 1 次配对迁移，消耗 1 个月度配额。

    Args:
        exit_leg: 第一条腿（例：V3 ticker, V→T）
        enter_leg: 第二条腿（例：T2 ticker, T→V）
        user_rationale: 人工填写的整体理由（必填，I4.5）
        dir_override: 测试时注入临时目录

    Returns:
        新 PoolState（原 state 不变）

    Raises:
        MissingMigrationRationale: rationale 为空
        MonthlyMigrationLimitExceeded: 已达 2 次/月
        PoolNotFoundError: 任一标的不在指定源池
        PoolSizeInvariantViolated: 迁移后规模 ≠ 5+5+5（理论上不会发生）

    关键约束：
        两条腿必须方向互补，使得 V/T/N 池规模净变化 = 0。
        例如：
            ✓ V→T 配 T→V（V 池净变化 0，T 池净变化 0）
            ✓ V→T 配 N→V（V 池 0，T 池 +1，N 池 -1 → 失败！）
            ✗ V→T 配 N→T（V 池 -1，T 池 +2，N 池 -1 → 失败！）

        本函数会显式校验池规模净变化。
    """
    if not user_rationale.strip():
        raise MissingMigrationRationale(
            "迁移必须填写理由（user_rationale 不能为空）"
        )

    # 月度配额（I4.2）
    current = get_monthly_migration_count(state)
    if current >= MONTHLY_MIGRATION_LIMIT:
        raise MonthlyMigrationLimitExceeded(
            f"本月已用 {current}/{MONTHLY_MIGRATION_LIMIT} 次配对迁移配额。下月清零。"
        )

    # 校验源池中存在
    for leg in (exit_leg, enter_leg):
        src = _get_pool(state, leg.from_category)
        if not any(e.ticker == leg.ticker for e in src):
            raise PoolNotFoundError(
                f"{leg.ticker} not in {leg.from_category} pool "
                f"(have: {[e.ticker for e in src]})"
            )

    # 构造新 state（深拷贝避免副作用）
    new_state = copy.deepcopy(state)
    pair_id = str(uuid.uuid4())
    decided_at = datetime.now().isoformat()

    # 执行两条腿
    for leg in (exit_leg, enter_leg):
        _apply_migration_leg(new_state, leg, pair_id, user_rationale, decided_at)

    # 校验池规模（I4.1）— 配对迁移后必须仍 5+5+5
    assert_pool_size_invariant(new_state)

    # 持久化
    save_pool_state(new_state, dir_override=dir_override)

    return new_state


def _apply_migration_leg(
    state: PoolState,
    leg: MigrationLeg,
    pair_id: str,
    user_rationale: str,
    decided_at: str,
) -> None:
    """在 state 上原地执行一条迁移腿。"""
    src = _get_pool(state, leg.from_category)
    dst = _get_pool(state, leg.to_category)

    entry = next(e for e in src if e.ticker == leg.ticker)
    src.remove(entry)

    new_entry = copy.copy(entry)
    new_entry.category = leg.to_category
    new_entry.sub_id = _next_sub_id(dst, leg.to_category)
    new_entry.last_migrated_at = decided_at
    dst.append(new_entry)

    rec = MigrationRecord(
        record_id=str(uuid.uuid4()),
        pair_id=pair_id,
        ticker=leg.ticker,
        from_category=leg.from_category,
        to_category=leg.to_category,
        signal=leg.signal,
        evidence=leg.evidence,
        user_rationale=user_rationale,
        decided_at=decided_at,
    )
    state.migrations_this_month.append(rec)


def _get_pool(state: PoolState, cat: Category) -> List[PoolEntry]:
    return {"V": state.v_pool, "T": state.t_pool, "N": state.n_pool}[cat]


def _next_sub_id(pool: List[PoolEntry], cat: Category) -> str:
    used = set()
    for e in pool:
        if e.sub_id.startswith(cat):
            try:
                used.add(int(e.sub_id[1:]))
            except Exception:
                pass
    for i in range(1, 10):
        if i not in used:
            return f"{cat}{i}"
    raise PoolError(f"{cat} pool sub_id exhausted (1-9)")


# =====================================================================
# 池初始化 / 池修正（不消耗月度配额，允许中间状态）
# =====================================================================

def add_initial_entry(
    state: PoolState,
    entry: PoolEntry,
    *,
    persist: bool = True,
    dir_override: Optional[Path] = None,
) -> PoolState:
    """
    把一只票加入池（初次填池或运维修正）。

    不消耗月度配额。但会拒绝重复 ticker。

    persist=True 时立即写盘（需要 5+5+5）；persist=False 时只改 state 不写盘
    （供批量初始化使用，最后由调用方手动 save_pool_state）。
    """
    if find_entry(state, entry.ticker):
        raise DuplicateTickerError(f"{entry.ticker} already in pool")

    new_state = copy.deepcopy(state)
    pool = _get_pool(new_state, entry.category)
    # 重新分配 sub_id
    new_entry = copy.copy(entry)
    new_entry.sub_id = _next_sub_id(pool, entry.category)
    pool.append(new_entry)

    if persist:
        save_pool_state(new_state, dir_override=dir_override)

    return new_state


def remove_entry(
    state: PoolState,
    ticker: str,
    *,
    persist: bool = True,
    dir_override: Optional[Path] = None,
) -> PoolState:
    """
    从池中移除一只票（运维修正）。

    不消耗月度配额。persist=True 时立即写盘（需要 5+5+5）。
    """
    found = find_entry(state, ticker)
    if not found:
        raise PoolNotFoundError(f"{ticker} not in any pool")

    cat, entry = found
    new_state = copy.deepcopy(state)
    pool = _get_pool(new_state, cat)
    pool[:] = [e for e in pool if e.ticker != ticker]

    if persist:
        save_pool_state(new_state, dir_override=dir_override)

    return new_state


# =====================================================================
# 自测
# =====================================================================

def _make_entry(t: str, cat: Category, sub: str) -> PoolEntry:
    return PoolEntry(
        ticker=t,
        name=f"name-{t}",
        category=cat,
        sub_id=sub,
        rationale="test",
        added_at=datetime.now().isoformat(),
    )


def _self_test() -> None:
    import tempfile

    print("=" * 60)
    print("strategy.three_categories self-test")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # === T1: 空池 ===
        print("\nT1: load 空池（首次启动）")
        s = load_pool_state(dir_override=tmp_dir)
        assert len(s.v_pool) == 0
        assert s.schema_version == "1.0"
        print("  PASS")

        # === T2: save + load roundtrip ===
        print("\nT2: save + load roundtrip")
        s.v_pool = [_make_entry(f"60000{i}.SH", "V", f"V{i}") for i in range(1, 6)]
        s.t_pool = [_make_entry(f"30000{i}.SZ", "T", f"T{i}") for i in range(1, 6)]
        s.n_pool = [_make_entry(f"0080{i}.HK", "N", f"N{i}") for i in range(1, 6)]
        save_pool_state(s, dir_override=tmp_dir)
        assert (tmp_dir / "three_categories.json").exists()
        assert not (tmp_dir / "three_categories.json.tmp").exists()
        loaded = load_pool_state(dir_override=tmp_dir)
        assert len(loaded.v_pool) == 5
        assert loaded.v_pool[0].ticker == "600001.SH"
        print("  PASS")

        # === T3: 池规模约束 ===
        print("\nT3: 池规模约束")
        try:
            assert_pool_size_invariant(
                PoolState(v_pool=[_make_entry("x", "V", "V1")])
            )
            raise AssertionError("Expected violation")
        except PoolSizeInvariantViolated as e:
            print(f"  PASS (1-element pool rejected): {e}")
        assert_pool_size_invariant(PoolState())  # 空池允许
        assert_pool_size_invariant(loaded)  # 5+5+5 通过
        print("  PASS (5+5+5 accepted)")

        # === T4: backup 创建 ===
        print("\nT4: backup 创建")
        save_pool_state(loaded, dir_override=tmp_dir)
        today_str = datetime.now().strftime("%Y-%m-%d")
        bak = tmp_dir / f"three_categories.json.bak.{today_str}"
        assert bak.exists()
        print(f"  PASS: backup at {bak.name}")

        # === T5: 缺 rationale 拒绝配对迁移 ===
        print("\nT5: 缺 rationale 拒绝迁移")
        try:
            execute_migration_pair(
                loaded,
                exit_leg=MigrationLeg("600001.SH", "V", "T"),
                enter_leg=MigrationLeg("300001.SZ", "T", "V"),
                user_rationale="",
                dir_override=tmp_dir,
            )
            raise AssertionError("Expected MissingMigrationRationale")
        except MissingMigrationRationale as e:
            print(f"  PASS: {e}")

        # === T6: 成功的配对迁移 ===
        print("\nT6: 配对迁移（V→T + T→V）")
        after = execute_migration_pair(
            loaded,
            exit_leg=MigrationLeg(
                ticker="600001.SH",
                from_category="V",
                to_category="T",
                signal="V2T_OVERBOUGHT",
                evidence=[{"key": "cum_return_5d", "value": 0.18}],
            ),
            enter_leg=MigrationLeg(
                ticker="300001.SZ",
                from_category="T",
                to_category="V",
                signal="T2V_PROSPERITY_WEAKEN_3D",
                evidence=[{"key": "capital_flow_3d", "value": [-1.2, -0.8, -1.5]}],
            ),
            user_rationale="周末复盘：600001 涨幅过高转 T；300001 景气走弱转 V",
            dir_override=tmp_dir,
        )
        # 池规模仍 5+5+5
        assert len(after.v_pool) == 5
        assert len(after.t_pool) == 5
        assert len(after.n_pool) == 5
        # 600001 现在在 T 池
        v_tickers = [e.ticker for e in after.v_pool]
        t_tickers = [e.ticker for e in after.t_pool]
        assert "600001.SH" not in v_tickers
        assert "600001.SH" in t_tickers
        assert "300001.SZ" not in t_tickers
        assert "300001.SZ" in v_tickers
        # 迁移记录：2 条共享 pair_id
        assert len(after.migrations_this_month) == 2
        pair_ids = {m.pair_id for m in after.migrations_this_month}
        assert len(pair_ids) == 1
        assert get_monthly_migration_count(after) == 1
        print(f"  PASS: 池规模 5+5+5, pair_id 共享, 月度配额 1/{MONTHLY_MIGRATION_LIMIT}")

        # === T7: 月度配额 ===
        print("\nT7: 月度配额耗尽")
        # 当前已用 1 次，再做 1 次到达 2 次
        after2 = execute_migration_pair(
            after,
            exit_leg=MigrationLeg("600002.SH", "V", "N"),
            enter_leg=MigrationLeg("00801.HK", "N", "V"),
            user_rationale="第二次配对",
            dir_override=tmp_dir,
        )
        assert get_monthly_migration_count(after2) == 2
        # 第 3 次应被拒
        try:
            execute_migration_pair(
                after2,
                exit_leg=MigrationLeg("600003.SH", "V", "T"),
                enter_leg=MigrationLeg("300002.SZ", "T", "V"),
                user_rationale="第三次",
                dir_override=tmp_dir,
            )
            raise AssertionError("Expected MonthlyMigrationLimitExceeded")
        except MonthlyMigrationLimitExceeded as e:
            print(f"  PASS: {e}")

        # === T8: 配对方向不匹配（非互补） ===
        print("\nT8: 非互补配对（V→T + N→T）→ 池规模会爆")
        # 重置月度配额（用空 migrations 模拟新月）
        fresh = copy.deepcopy(loaded)
        try:
            execute_migration_pair(
                fresh,
                exit_leg=MigrationLeg("600002.SH", "V", "T"),
                enter_leg=MigrationLeg("00802.HK", "N", "T"),   # 都到 T，T 会变 7
                user_rationale="故意构造非互补配对",
                dir_override=tmp_dir,
            )
            raise AssertionError("Expected PoolSizeInvariantViolated")
        except PoolSizeInvariantViolated as e:
            print(f"  PASS: {e}")

        # === T9: 损坏文件 → backup 恢复 ===
        print("\nT9: 损坏主文件 → backup 恢复")
        path = tmp_dir / "three_categories.json"
        path.write_text("{ this is broken json", encoding="utf-8")
        recovered = load_pool_state(dir_override=tmp_dir)
        assert len(recovered.v_pool) == 5
        print(f"  PASS: recovered {len(recovered.v_pool)} V-pool entries")

        # === T10: 池初始化操作（add_initial_entry / remove_entry） ===
        print("\nT10: 池初始化操作")
        # 从空池开始批量加入
        empty = PoolState()
        for i in range(1, 6):
            empty = add_initial_entry(
                empty,
                _make_entry(f"60010{i}.SH", "V", "(reassigned)"),
                persist=False,
            )
        for i in range(1, 6):
            empty = add_initial_entry(
                empty,
                _make_entry(f"30010{i}.SZ", "T", "(reassigned)"),
                persist=False,
            )
        for i in range(1, 6):
            empty = add_initial_entry(
                empty,
                _make_entry(f"0081{i}.HK", "N", "(reassigned)"),
                persist=False,
            )
        # 现在 5+5+5，可以 save
        save_pool_state(empty, dir_override=tmp_dir)
        # sub_id 应该自动分配
        assert {e.sub_id for e in empty.v_pool} == {"V1", "V2", "V3", "V4", "V5"}
        print(f"  PASS: 批量初始化 + sub_id 自动分配")

        # 重复 ticker 拒绝
        try:
            add_initial_entry(
                empty,
                _make_entry("600101.SH", "T", "T1"),
                persist=False,
            )
            raise AssertionError("Expected DuplicateTickerError")
        except DuplicateTickerError as e:
            print(f"  PASS: 重复 ticker 拒绝 ({e})")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")


# =====================================================================
# v3.5 兼容 wrapper (Phase 2.1 hotfix, 2026-06-19)
# =====================================================================
#
# 历史背景:
#   v3.5 暴露 ThreeCategoryPool 类,OO 接口 (实例方法).
#   v1.0.1 重写时改为函数式 API (load_pool_state / save_pool_state /
#   execute_migration_pair 等),没保留 OO 入口.
#   web_app.py 仍调 `ThreeCategoryPool()` 和 `.to_json_for_frontend()`,
#   起服时 ImportError → 观察池 500.
#
# 本 wrapper 是单一调用点修复:在 web_app.py 0 改动的前提下让观察池
# 重新 work.新代码不应再依赖此类,直接用上面的函数式 API.

class ThreeCategoryPool:
    """v3.5 兼容 wrapper. 内部全部委托给 v1.0.1 函数式 API.

    使用方式 (v3.5 风格,仅为兼容现有调用方保留):
        pool = ThreeCategoryPool()                    # 自动 load_pool_state
        data = pool.to_json_for_frontend()             # 给 web 前端的 dict
        v_t_n, entry = pool.find("600519.SH")         # 查标的所属池
        pool.reload()                                  # 别人改盘后重读

    新代码应直接用:
        state = load_pool_state()
        execute_migration_pair(state, exit_leg=..., enter_leg=...)
        save_pool_state(state)
    """

    def __init__(self, dir_override: Optional[Path] = None):
        """加载现有 pool state. 主文件 + backup 链全坏 → 给空 state 让前端能 render."""
        self._dir = dir_override
        try:
            self._state = load_pool_state(dir_override)
        except PoolStateLoadError as exc:
            # backup 链全失败 — 给空 state, 不让前端整个挂.
            # 上游可通过 .state 字段查实际情况.
            print(f"  [ThreeCategoryPool] WARN: pool state unrecoverable, "
                  f"using empty state ({exc})")
            self._state = PoolState()

    # ── 状态访问 ────────────────────────────────────────────────

    @property
    def state(self) -> PoolState:
        """暴露内部 PoolState 供函数式 API 调用."""
        return self._state

    def reload(self) -> None:
        """重读盘 (供别处改完 state 后刷新)."""
        try:
            self._state = load_pool_state(self._dir)
        except PoolStateLoadError as exc:
            print(f"  [ThreeCategoryPool] reload WARN: {exc}")
            self._state = PoolState()

    # ── 前端友好序列化 ──────────────────────────────────────────

    def to_json_for_frontend(self) -> dict:
        """前端友好的 dict (JSON-serializable).

        返回 5 个核心字段 + 元信息. 字段名与 v3.5 一致, 前端 JS 不用改.
        """
        return {
            "v_pool": [e.to_dict() for e in self._state.v_pool],
            "t_pool": [e.to_dict() for e in self._state.t_pool],
            "n_pool": [e.to_dict() for e in self._state.n_pool],
            "watchlist": [e.to_dict() for e in self._state.watchlist],
            "monthly_migrations_used": get_monthly_migration_count(self._state),
            "monthly_migrations_limit": MONTHLY_MIGRATION_LIMIT,
            "last_modified": self._state.last_modified,
            "schema_version": self._state.schema_version,
            # 容量摘要 (方便前端做 5/5/5/5+5+5 等小角标)
            "pool_sizes": {
                "v": len(self._state.v_pool),
                "t": len(self._state.t_pool),
                "n": len(self._state.n_pool),
                "watchlist": len(self._state.watchlist),
            },
        }

    # ── 几个最常用 OO 查询 (薄包装到函数式 API) ─────────────────

    def find(self, ticker: str) -> Optional[Tuple[Category, PoolEntry]]:
        """ticker → (所属池类别, 该 entry) 或 None."""
        return find_entry(self._state, ticker)

    def monthly_migration_count(self) -> int:
        return get_monthly_migration_count(self._state)

    # ── dunder ──────────────────────────────────────────────────

    def __len__(self) -> int:
        """池中总条目数 (不算 watchlist)."""
        return (len(self._state.v_pool)
                + len(self._state.t_pool)
                + len(self._state.n_pool))

    def __repr__(self) -> str:
        return (f"ThreeCategoryPool(V={len(self._state.v_pool)}, "
                f"T={len(self._state.t_pool)}, "
                f"N={len(self._state.n_pool)}, "
                f"watchlist={len(self._state.watchlist)})")


# =====================================================================
# v3.5 兼容 wrapper 的自测 (跑在原 _self_test 之后)
# =====================================================================

def _self_test_compat_wrapper() -> None:
    """ThreeCategoryPool wrapper 的独立自测. 用 tmp dir 不污染 ~/.ai-hedge-fund/."""
    import tempfile

    print("\n" + "=" * 60)
    print("v3.5 兼容 wrapper (ThreeCategoryPool) 自测")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)

        # T1: 空 state (无文件) → wrapper 不挂, to_json_for_frontend 返回干净结构
        pool = ThreeCategoryPool(dir_override=td_path)
        d = pool.to_json_for_frontend()
        assert d["v_pool"] == [], d
        assert d["t_pool"] == [], d
        assert d["n_pool"] == [], d
        assert d["watchlist"] == [], d
        assert d["monthly_migrations_used"] == 0, d
        assert d["monthly_migrations_limit"] == MONTHLY_MIGRATION_LIMIT, d
        assert d["pool_sizes"] == {"v": 0, "t": 0, "n": 0, "watchlist": 0}, d
        print("[T1] 空 state → to_json_for_frontend 正常: PASS")

        # T2: dunder 工作
        assert len(pool) == 0, len(pool)
        assert "V=0" in repr(pool) and "T=0" in repr(pool), repr(pool)
        print("[T2] __len__ / __repr__: PASS")

        # T3: 落点 state 后 reload 反映新数据
        state = PoolState()
        # 必须 5+5+5 才能 save (assert_pool_size_invariant)
        # add_initial_entry 是函数式 (返回新 state, 不改原 state),
        # persist=False 批量初始化, 最后一次 save
        for i in range(5):
            entry = PoolEntry(
                ticker=f"60001{i}.SH", name=f"票{i}", category="V",
                sub_id="", rationale=f"V 池条目 {i}",
                added_at=datetime.now().isoformat(),
            )
            state = add_initial_entry(state, entry, persist=False)
        for i in range(5):
            entry = PoolEntry(
                ticker=f"60002{i}.SH", name=f"T票{i}", category="T",
                sub_id="", rationale=f"T 池条目 {i}",
                added_at=datetime.now().isoformat(),
            )
            state = add_initial_entry(state, entry, persist=False)
        for i in range(5):
            entry = PoolEntry(
                ticker=f"60003{i}.SH", name=f"N票{i}", category="N",
                sub_id="", rationale=f"N 池条目 {i}",
                added_at=datetime.now().isoformat(),
            )
            state = add_initial_entry(state, entry, persist=False)
        save_pool_state(state, dir_override=td_path)

        pool.reload()
        d = pool.to_json_for_frontend()
        assert d["pool_sizes"] == {"v": 5, "t": 5, "n": 5, "watchlist": 0}, d
        assert len(d["v_pool"]) == 5
        assert d["v_pool"][0]["ticker"] == "600010.SH"
        print(f"[T3] reload → to_json_for_frontend 反映落盘: PASS ({pool})")

        # T4: find 查标的
        result = pool.find("600010.SH")
        assert result is not None
        cat, entry = result
        assert cat == "V"
        assert entry.name == "票0"
        assert pool.find("999999.SH") is None
        print("[T4] find(ticker): PASS")

        # T5: __len__
        assert len(pool) == 15, len(pool)
        print("[T5] __len__ 返回 15 (5+5+5 不含 watchlist): PASS")

        # T6: 损坏文件路径 → wrapper 不抛, 返回空 state
        broken_dir = td_path / "broken"
        broken_dir.mkdir()
        # 写个完全坏的 json + 无 backup
        (broken_dir / "three_categories.json").write_text("NOT VALID JSON {{{",
                                                          encoding="utf-8")
        pool_broken = ThreeCategoryPool(dir_override=broken_dir)
        d_broken = pool_broken.to_json_for_frontend()
        assert d_broken["v_pool"] == []
        assert len(pool_broken) == 0
        print("[T6] 损坏文件 → 安全降级为空 state: PASS")

    print("\n[ThreeCategoryPool v3.5 wrapper] 自测 6/6 PASS\n")


if __name__ == "__main__":
    _self_test()
    _self_test_compat_wrapper()
