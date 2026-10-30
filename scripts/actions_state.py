"""在独立 Git 分支保存 SQLite 和非敏感配置，不修改源代码分支。"""
import argparse
import os
import subprocess
from pathlib import Path


BRANCH = "monitor-state"
FILES = {
    "config.json": Path(".monitor-state/config.json"),
    "prices.db": Path("data/prices.db"),
    "latest.json": Path("docs/data/latest.json"),
    "report.html": Path("docs/data/report.html"),
}


def git(*args, input=None, env=None, check=True):
    return subprocess.run(["git", *args], input=input, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def restore():
    remote = git("ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False)
    if remote.returncode == 2:
        print("First run: no previous monitor state.")
        return
    if remote.returncode:
        raise RuntimeError("Unable to read monitor-state branch from origin")
    git("fetch", "--depth=1", "origin", f"refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}")
    for name, path in FILES.items():
        result = git("show", f"refs/remotes/origin/{BRANCH}:{name}", check=False)
        if result.returncode == 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(result.stdout)
    print("Restored previous prices, alert state and itinerary.")


def save():
    if not FILES["config.json"].exists():
        raise RuntimeError("No prepared monitor configuration to save")
    index = Path(".monitor-state/git-index").resolve()
    env = dict(os.environ, GIT_INDEX_FILE=str(index),
               GIT_AUTHOR_NAME="github-actions[bot]", GIT_COMMITTER_NAME="github-actions[bot]",
               GIT_AUTHOR_EMAIL="github-actions[bot]@users.noreply.github.com",
               GIT_COMMITTER_EMAIL="github-actions[bot]@users.noreply.github.com")
    git("read-tree", "--empty", env=env)
    for name, path in FILES.items():
        if path.exists():
            oid = git("hash-object", "-w", str(path), env=env).stdout.decode().strip()
            git("update-index", "--add", "--cacheinfo", f"100644,{oid},{name}", env=env)
    tree = git("write-tree", env=env).stdout.decode().strip()
    parent = git("rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}", check=False)
    parent_args = ["-p", parent.stdout.decode().strip()] if parent.returncode == 0 else []
    commit = git("commit-tree", tree, *parent_args, "-m", "Save monitor prices and itinerary", env=env).stdout.decode().strip()
    git("push", "origin", f"{commit}:refs/heads/{BRANCH}")
    print("Saved history on monitor-state (notification credentials are excluded).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore", "save"])
    args = parser.parse_args()
    (restore if args.command == "restore" else save)()
