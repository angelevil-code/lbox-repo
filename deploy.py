#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LBOX 源仓库 —— 一键部署到 GitHub Pages

用法：
    python deploy.py              # 仓库名默认 lbox-repo
    python deploy.py my-repo      # 自定义仓库名

前置：本机已登录 gh（gh auth login）。脚本会检查，没登录会提示。
"""

import json
import os
import shutil
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.chdir(os.path.dirname(os.path.abspath(__file__)))


def _find(name, candidates):
    """优先 PATH，其次常见安装位置"""
    p = shutil.which(name)
    if p:
        return p
    for c in candidates:
        if os.path.exists(c):
            return c
    return name


GH = _find("gh", [
    os.path.expanduser(r"~\.workbuddy-ai\binaries\gh\bin\gh.exe"),
    r"C:\Program Files\GitHub CLI\gh.exe",
    r"C:\Program Files (x86)\GitHub CLI\gh.exe",
])
GIT = _find("git", [r"C:\Program Files\Git\cmd\git.exe"])


def run(cmd, check=True, stdin=None):
    r = subprocess.run(cmd, input=stdin, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        print(f"\n[失败] {' '.join(cmd)}")
        print((r.stdout or "") + (r.stderr or ""))
        raise SystemExit(1)
    return r


def gh(*args, check=True, stdin=None):
    return run([GH, *args], check=check, stdin=stdin)


def git(*args, check=True):
    return run([GIT, *args], check=check)


def step(msg):
    print(f"\n==> {msg}")


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "lbox-repo"

    step("检查 GitHub 登录状态")
    if gh("auth", "status", check=False).returncode != 0:
        print("""
还没有登录 GitHub。请在终端执行下面这一条，按提示用浏览器授权：

    gh auth login

选择：GitHub.com -> HTTPS -> Login with a web browser
授权完再回来跑一次 python deploy.py
""")
        raise SystemExit(1)
    owner = gh("api", "user", "--jq", ".login").stdout.strip()
    print(f"    已登录：{owner}")

    step("初始化本地仓库")
    if not os.path.isdir(".git"):
        git("init")
        print("    已初始化")
    else:
        print("    已存在，跳过")

    step("配置 git 身份")
    git("config", "user.name", owner)
    git("config", "user.email", f"{owner}@users.noreply.github.com")
    print(f"    {owner} <{owner}@users.noreply.github.com>")

    git("add", "-A")
    r = git("commit", "-m", "init: LBOX 源仓库", check=False)
    if r.returncode == 0:
        print("    已提交")
    else:
        print("    无新改动，跳过提交")
    git("branch", "-M", "main")

    step(f"创建远程仓库 {owner}/{repo}")
    exists = gh("repo", "view", f"{owner}/{repo}", check=False).returncode == 0
    if exists:
        print("    仓库已存在，改为推送更新")
        if gh("remote", "get-url", "origin", check=False).returncode != 0:
            git("remote", "add", "origin", f"https://github.com/{owner}/{repo}.git")
        git("push", "-u", "origin", "main", check=False)
    else:
        r = gh("repo", "create", repo, "--public", "--source=.", "--remote=origin", "--push", check=False)
        if r.returncode != 0:
            print((r.stdout or "") + (r.stderr or ""))
            print("\n创建失败。如果是权限问题，请确认 gh 授权包含 repo 权限。")
            raise SystemExit(1)
        print("    已创建并推送")

    step("开启 Actions 写权限（否则定时任务无法提交结果）")
    r = gh("api", "--method", "PUT",
           f"repos/{owner}/{repo}/actions/permissions/workflow",
           "-f", "default_workflow_permissions=write", check=False)
    print("    ok" if r.returncode == 0 else f"    需手动设置：{r.stderr[:80]}")

    step("开启 GitHub Pages（main / docs）")
    payload = json.dumps({"source": {"branch": "main", "path": "/docs"}})
    r = gh("api", "--method", "POST", f"repos/{owner}/{repo}/pages",
           "--input", "-", check=False, stdin=payload)
    if r.returncode != 0:
        r = gh("api", "--method", "PUT", f"repos/{owner}/{repo}/pages",
               "--input", "-", check=False, stdin=payload)
    print("    ok" if r.returncode == 0 else "    可能需手动开启（见 README）")

    step("触发一次构建")
    r = gh("workflow", "run", "update.yml", "--repo", f"{owner}/{repo}", check=False)
    print("    已触发" if r.returncode == 0 else "    跳过（可稍后在 Actions 页手动运行）")

    base = f"https://{owner}.github.io/{repo}"
    print("\n" + "=" * 62)
    print("  部署完成")
    print("=" * 62)
    print(f"  主页        {base}/")
    print(f"  书源订阅    {base}/book.json")
    print(f"  TVBox 订阅  {base}/tvbox.json")
    print("=" * 62)
    print("  Pages 首次生效需要 1~3 分钟，稍等再打开。")
    print(f"  查看构建进度：https://github.com/{owner}/{repo}/actions")


if __name__ == "__main__":
    main()
