#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 GitHub Issue 里读取源地址，探测通过后追加到 seed_sources.json。

由 .github/workflows/add-source.yml 调用，环境变量：
    ISSUE_TITLE  形如 "[add] 源名称"
    ISSUE_BODY   正文里含地址（第一行通常就是）
"""

import json
import os
import re
import sys

from server import detect, fetch, normalize

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_sources.json")


def fail(msg):
    print(f"[add] 失败：{msg}")
    sys.exit(1)


def main():
    title = os.environ.get("ISSUE_TITLE", "").strip()
    body = os.environ.get("ISSUE_BODY", "")

    urls = re.findall(r"https?://[^\s<>\"'\)\]]+", body)
    if not urls:
        fail("正文里没找到 http(s) 地址")
    url = urls[0].rstrip(".,;")

    print(f"[add] 地址：{url}")
    print("[add] 探测中 …")
    raw, ms, err = fetch(normalize(url), timeout=15, retries=1)
    if not raw:
        fail(f"拉取不到（{err}）")

    kind, typ, count, derr = detect(raw)
    if kind == "unknown":
        fail(f"结构不合法（{derr}）")

    unit = {"book": "条书源", "tvbox": "个站点", "depot": "个仓"}.get(kind, "条")
    print(f"[add] 通过：{kind} / {count} {unit} / {ms}ms")

    name = re.sub(r"^\[add\]\s*", "", title).strip() or url.rstrip("/").split("/")[-1]

    with open(SEED, encoding="utf-8") as f:
        seed = json.load(f)
    items = seed.setdefault("sources", [])

    if any(s.get("url") == url for s in items):
        print("[add] 这个地址已经在清单里了，不重复添加")
        print(f"[add] RESULT=exists {name}")
        return

    # raw.githubusercontent.com/owner/repo/... 和 github.com/owner/repo 都要能识别
    m = (re.search(r"githubusercontent\.com/([^/]+)/", url)
         or re.search(r"github\.com/([^/]+)/", url))
    items.append({
        "type": typ,
        "name": name,
        "url": url,
        "origin": f"github/{m.group(1)}" if m else "手动添加",
    })

    with open(SEED, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[add] 已写入 seed_sources.json：{name}（{count} {unit}）")
    print(f"[add] RESULT=added {name} {count}{unit}")


if __name__ == "__main__":
    main()
