# -*- coding: utf-8 -*-
"""smoke_step16_hotfix1.py — baostock 共享会话热修确定性验证(离线,假 baostock 桩)。

护栏双向:①并发正确性(单次登录/零 logout/锁互斥);②被保护方仍工作
(baostock_data 经假桩查询照常;eval 兜底路径在无 baostock_data 时保留旧行为)。
"""
import sys, threading, time, types
sys.path.insert(0, ".")

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name)

# ── 假 baostock 桩(注入 sys.modules,先于 baostock_data 导入)──
calls = {"login": 0, "logout": 0}
fake = types.ModuleType("baostock")
fake.login = lambda: (calls.__setitem__("login", calls["login"] + 1),
                      types.SimpleNamespace(error_code="0", error_msg=""))[1]
fake.logout = lambda: calls.__setitem__("logout", calls["logout"] + 1)
sys.modules["baostock"] = fake

from src.tools import baostock_data as bsd
from src.eval import data as ed

check("T1 baostock_data 识别假桩(_HAVE_BS)", bsd._HAVE_BS)

print("== T2 共享会话:两次进出 → 登录一次,零 logout ==")
with ed.baostock_session():
    pass
with ed.baostock_session():
    pass
check("T2a login 恰一次(进程级)", calls["login"] == 1)
check("T2b logout 从未发生(不拆连接)", calls["logout"] == 0)
check("T2c baostock_data 侧已登录(会话共享)", bsd._logged_in)

print("== T3 互斥:session 持锁期间他人拿不到 _BS_LOCK ==")
inside, got_lock_while_inside = threading.Event(), []
def rival():
    inside.wait(2)
    got_lock_while_inside.append(bsd._BS_LOCK.acquire(timeout=0.4))
    if got_lock_while_inside[-1]:
        bsd._BS_LOCK.release()
t = threading.Thread(target=rival)
t.start()
with ed.baostock_session():
    inside.set()
    time.sleep(0.8)
t.join()
check("T3a 持锁期间对手方 acquire 失败(串行化成立)", got_lock_while_inside == [False])
check("T3b 退出后锁可再取", bsd._BS_LOCK.acquire(timeout=0.5) and (bsd._BS_LOCK.release() or True))

print("== T4 兜底路径:无 baostock_data 时保留旧 login/logout ==")
import importlib
saved = {k: sys.modules.pop(k, None) for k in ("src.tools.baostock_data", "tools.baostock_data", "src.tools", "tools")}
sys.modules["src.tools"] = None  # 强制 ImportError
sys.modules["tools"] = None
importlib.reload(ed)
base_login = calls["login"]
with ed.baostock_session():
    pass
check("T4a 兜底路径 login+logout 各一次", calls["login"] == base_login + 1 and calls["logout"] == 1)
for k, v in saved.items():
    if v is not None: sys.modules[k] = v
    else: sys.modules.pop(k, None)

print(f"\n结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
sys.exit(1 if FAIL else print("SMOKE HOTFIX1: ALL GREEN") or 0)
