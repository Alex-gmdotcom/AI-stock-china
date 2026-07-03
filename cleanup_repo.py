# -*- coding: utf-8 -*-
"""
cleanup_repo.py — 清理过程文件 + 脱敏（在仓库根运行）
====================================================
做三件事,均不自动 commit（让你 review 后自己提交,可随时 git revert）:
  1) git rm 过程产物:deploy_*/apply_* 一次性脚本、*.bak*、各 backup 目录、
     tar.gz、probe、根目录误生成的 '2.1'。(经核:无任何源码 import 它们)
  2) 脱敏第三方真名「郑超」→「同事」(MIGRATION.md / openclaw_task.md 等)。
  3) 追加 .gitignore 规则,防这些过程产物再次入库。

安全:只删 git 已跟踪且匹配下列模式的文件;真源码(src/**.py 非 .bak)不动。
注意:删除/脱敏只改“当前快照”,git 历史仍保留旧内容(无密钥,无需重写历史;
     若要连历史一起抹掉郑超,另见末尾提示)。
"""
import os, re, subprocess, sys

DELETE_PATTERN = re.compile(
    r"^deploy_[^/]*\.py$"
    r"|^apply_[^/]*\.py$"
    r"|^baostock_probe.*"
    r"|\.bak[0-9]*$"
    r"|^_aihf_backup_"
    r"|^deploy_backup/"
    r"|^phase1_backup/"
    r"|^phase2_backup/"
    r"|^phase2_hotfix_backup/"
    r"|\.tar\.gz$"
    r"|^2\.1$"
)

REDACT = {"郑超": "同事"}

GITIGNORE_MARKER = "# === 过程产物(cleanup_repo.py 追加) ==="
GITIGNORE_BLOCK = """
# === 过程产物(cleanup_repo.py 追加) ===
deploy_*.py
apply_*.py
*.bak
*.bak[0-9]
*.tar.gz
_aihf_backup_*/
deploy_backup/
phase1_backup/
phase2_backup/
phase2_hotfix_backup/
baostock_probe*
"""


def sh(*args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def ensure_repo_root():
    if not os.path.isdir(".git"):
        sys.exit("✗ 请在仓库根目录(含 .git/)运行。")


def git_tracked():
    out = sh("git", "ls-files").stdout
    return [l for l in out.splitlines() if l]


def step_delete(tracked):
    targets = [f for f in tracked if DELETE_PATTERN.search(f)]
    # 双保险:绝不删非 .bak 的 src 源码
    safe = []
    for f in targets:
        if f.startswith("src/") and not re.search(r"\.bak[0-9]*$", f):
            print("  ! 跳过(疑似源码):", f); continue
        safe.append(f)
    if not safe:
        print("  (无可删项,已干净)"); return 0
    print(f"  待删 {len(safe)} 项:")
    for f in safe:
        print("    -", f)
    # 分批 git rm
    for i in range(0, len(safe), 50):
        sh("git", "rm", "-q", "--", *safe[i:i+50])
    return len(safe)


def step_redact(tracked):
    n = 0
    for f in tracked:
        if DELETE_PATTERN.search(f) or not os.path.exists(f):
            continue
        try:
            txt = open(f, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError):
            continue
        new = txt
        for k, v in REDACT.items():
            new = new.replace(k, v)
        if new != txt:
            open(f, "w", encoding="utf-8").write(new)
            sh("git", "add", "--", f)
            print(f"  脱敏: {f}")
            n += 1
    if n == 0:
        print("  (无需脱敏,已干净)")
    return n


def step_gitignore():
    path = ".gitignore"
    cur = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if GITIGNORE_MARKER in cur:
        print("  (.gitignore 已含规则,跳过)"); return False
    if cur and not cur.endswith("\n"):
        cur += "\n"
    open(path, "w", encoding="utf-8").write(cur + GITIGNORE_BLOCK)
    sh("git", "add", "--", path)
    print("  .gitignore 已追加过程产物规则")
    return True


def main():
    ensure_repo_root()
    tracked = git_tracked()
    print("== 1) 删除过程产物 ==")
    d = step_delete(tracked)
    print("== 2) 脱敏第三方真名 ==")
    r = step_redact(tracked)
    print("== 3) 更新 .gitignore ==")
    step_gitignore()
    print("\n== 暂存区状态(git status --short)==")
    print(sh("git", "status", "--short").stdout or "  (无变更)")
    print(f"\n完成:删 {d} 项、脱敏 {r} 文件。已 git add,未 commit。")
    print("请 review 后提交:")
    print('  git commit -m "chore: remove process artifacts + redact PII + gitignore"')


if __name__ == "__main__":
    main()
