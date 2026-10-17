#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LBOX 源仓库 —— 半自动发现

扫描 GitHub 上最近更新的 tvbox / legado 仓库，探测其中的配置文件，
把「能拉取且结构正确」的列为候选，输出到 docs/candidates.json 和 docs/discover.html。

用法：
    python discover.py                    # 输出到 docs/
    python discover.py --out docs --repos 30
"""

import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from server import detect, fetch, normalize

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

API = "https://api.github.com"
UA = "LBOX-Repo-Discover/1.0"
MAX_REPOS = 30
MAX_PER_REPO = 4

# 文件名里出现这些词，更可能是配置
GOOD_WORDS = ("tvbox", "config", "api", "source", "book", "shuyuan", "接口", "配置", "仓库")
# 出现这些基本可以跳过
BAD_WORDS = ("package", "tsconfig", "manifest", "lock", "composer", "node_modules",
             ".github", "test", "eslint", "prettier", "renovate", "dependabot")

# GitHub 不支持 `pushed:>14days` 这种相对写法，必须用具体日期
_SINCE = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()

SEARCH_QUERIES = [
    f"tvbox in:name,description pushed:>{_SINCE}",
    f"legado 书源 pushed:>{_SINCE}",
    f"tvbox 配置 pushed:>{_SINCE}",
]


def token():
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    # 本地跑时借用 gh 的登录态
    try:
        import subprocess
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


TOKEN = token()


def api_get(path):
    req = urllib.request.Request(API + path, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def search_repos(query, per_page=15):
    q = urllib.parse.quote(query)
    try:
        d = api_get(f"/search/repositories?q={q}&sort=updated&order=desc&per_page={per_page}")
        return d.get("items", [])
    except Exception as e:
        print(f"[discover] 搜索失败 {query}: {str(e)[:60]}")
        return []


def list_json_files(full_name, branch):
    try:
        d = api_get(f"/repos/{full_name}/git/trees/{branch}?recursive=1")
    except Exception:
        return []
    out = []
    for t in d.get("tree", []):
        p = t.get("path", "")
        if not p.endswith(".json"):
            continue
        if t.get("size", 0) > 6_000_000:
            continue
        low = p.lower()
        if any(b in low for b in BAD_WORDS):
            continue
        out.append(p)
    return out


def score_path(path):
    low = path.lower()
    s = 0
    for w in GOOD_WORDS:
        if w in low:
            s += 2
    if low.count("/") == 0:
        s += 1
    s -= len(path) / 60.0
    return s


def probe_candidate(full_name, branch, path, repo_meta):
    url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{urllib.parse.quote(path)}"
    body, ms, err = fetch(normalize(url), timeout=12, retries=0)
    if not body:
        return None
    kind, typ, count, derr = detect(body)
    if kind == "unknown":
        return None
    return {
        "name": path.split("/")[-1].replace(".json", ""),
        "url": url,
        "type": typ,
        "kind": kind,
        "count": count,
        "latency": ms,
        "repo": full_name,
        "stars": repo_meta.get("stargazers_count", 0),
        "pushed": (repo_meta.get("pushed_at") or "")[:10],
    }


def discover(max_repos=MAX_REPOS):
    seen_repo, repos = set(), []
    for q in SEARCH_QUERIES:
        for r in search_repos(q):
            fn = r["full_name"]
            if fn in seen_repo:
                continue
            seen_repo.add(fn)
            repos.append(r)

    # 星标高的优先，其次最近更新的
    repos.sort(key=lambda r: (-r.get("stargazers_count", 0), r.get("pushed_at") or ""))
    repos = repos[:max_repos]
    print(f"[discover] 候选仓库 {len(repos)} 个")

    tasks = []
    for r in repos:
        branch = r.get("default_branch") or "main"
        files = list_json_files(r["full_name"], branch)
        files.sort(key=score_path, reverse=True)
        for p in files[:MAX_PER_REPO]:
            tasks.append((r["full_name"], branch, p, r))
        print(f"    {r['full_name']:42s} json={len(files):>3d} 取 {min(len(files), MAX_PER_REPO)}")

    print(f"[discover] 开始探测 {len(tasks)} 个文件 …")
    results, seen_url = [], set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item in pool.map(lambda a: probe_candidate(*a), tasks):
            if not item or item["url"] in seen_url:
                continue
            seen_url.add(item["url"])
            results.append(item)

    results.sort(key=lambda x: (-x["count"], -x["stars"]))
    print(f"[discover] 可用候选 {len(results)} 个")
    return results, len(repos)


def render_page(cands, scanned, built_at):
    css_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "style.css")
    css = ""
    if os.path.exists(css_file):
        with open(css_file, encoding="utf-8") as f:
            css = f.read()

    def card(c):
        typ = "书源" if c["type"] == "book" else "TVBox"
        if c["kind"] == "depot":
            unit = f'{c["count"]} 个仓'
        elif c["type"] == "book":
            unit = f'{c["count"]} 条书源'
        else:
            unit = f'{c["count"]} 个站点'
        snippet = json.dumps({
            "type": c["type"], "name": c["name"], "url": c["url"],
            "origin": "github/" + c["repo"].split("/")[0],
        }, ensure_ascii=False, indent=2)
        return f"""
    <article class="card">
      <div class="card-top">
        <span class="status-dot ok"></span>
        <h3 class="card-title">{html.escape(c['name'])}</h3>
        <span class="badge {c['type']}">{typ}</span>
      </div>
      <div class="card-meta">
        <span>{unit}</span><span class="sep">·</span><span>{c['latency']}ms</span>
        <span class="sep">·</span><span>{html.escape(c['repo'])}</span>
        <span class="sep">·</span><span>★{c['stars']}</span>
      </div>
      <div class="card-url" title="{html.escape(c['url'])}">{html.escape(c['url'])}</div>
      <div class="card-actions">
        <button class="mini" data-copy='{html.escape(snippet)}'>复制配置</button>
        <button class="mini" data-copy="{html.escape(c['url'])}">复制地址</button>
      </div>
    </article>"""

    cards = "".join(card(c) for c in cands) or (
        '<p class="note">这次没扫到新候选，过几天再看。</p>')

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LBOX 源仓库 · 发现</title>
<style>{css}</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <div class="logo">LB</div>
    <div class="brand-text">
      <h1>发现新源</h1>
      <p>扫描了 {scanned} 个仓库 · 更新于 {built_at}</p>
    </div>
  </div>
  <div class="top-actions">
    <a class="btn" href="./">返回首页</a>
    <a class="btn primary" href="https://github.com/angelevil-code/lbox-repo/edit/main/seed_sources.json" target="_blank">编辑源清单</a>
  </div>
</header>

<section class="sub-box">
  <div class="sub-row">
    <span class="sub-label">怎么加</span>
    <span style="flex:1;color:var(--muted);font-size:12.5px">
      点「复制配置」→ 打开右上角「编辑源清单」→ 粘到 sources 数组里（注意前一条末尾加逗号）→ 提交，1 分钟后自动生效
    </span>
  </div>
</section>

<main class="grid">{cards}
</main>

<p class="note">只收录「能拉取 + 结构正确」的地址，不判断里面的片子能不能播。候选每天刷新一次。</p>

<div id="toast" class="toast"></div>
<script>
function toast(m) {{
  const el = document.getElementById("toast");
  el.textContent = m; el.classList.add("show");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 1800);
}}
async function copy(t) {{
  try {{ await navigator.clipboard.writeText(t); return true; }}
  catch {{
    const a = document.createElement("textarea");
    a.value = t; a.style.position = "fixed"; a.style.opacity = "0";
    document.body.appendChild(a); a.select();
    let ok = false; try {{ ok = document.execCommand("copy"); }} catch {{}}
    a.remove(); return ok;
  }}
}}
document.addEventListener("click", async (e) => {{
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  toast(await copy(b.dataset.copy) ? "已复制" : "复制失败");
}});
</script>
</body>
</html>"""


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs")
    ap.add_argument("--repos", type=int, default=MAX_REPOS)
    args = ap.parse_args()

    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)

    if not TOKEN:
        print("[discover] 警告：没有 GitHub token，API 限流会很低（60/小时）")

    t0 = time.time()
    cands, scanned = discover(args.repos)
    built_at = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    payload = {
        "generated_at": int(time.time()),
        "generated_at_text": built_at,
        "scanned_repos": scanned,
        "count": len(cands),
        "candidates": cands,
    }
    with open(os.path.join(outdir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "discover.html"), "w", encoding="utf-8") as f:
        f.write(render_page(cands, scanned, built_at))

    print(f"[discover] 完成，用时 {int(time.time()-t0)}s -> {outdir}/discover.html")


if __name__ == "__main__":
    main()
