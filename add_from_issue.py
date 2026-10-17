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

from server import detect, extract_keys, fetch, loads_lenient, normalize, similarity

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_sources.json")

# 相似度到这个程度就认为「装的是同一批东西」
DUP_REJECT = 0.70
DUP_WARN = 0.40


def fail(msg):
    print(f"[add] 失败：{msg}")
    sys.exit(1)


def check_duplicate(new_keys, seed, typ):
    """和池子里同类型的源逐个比 Jaccard，返回 (最高相似度, 那个源的名字)"""
    best, best_name = 0.0, ""
    for item in seed.get("sources", []):
        if item.get("type") != typ:
            continue
        body, _, _ = fetch(normalize(item["url"]), timeout=10, retries=0)
        if not body:
            continue
        obj, _ = loads_lenient(body.decode("utf-8", "ignore"))
        if obj is None:
            continue
        old = extract_keys(obj)
        if not old:
            continue
        sim = similarity(new_keys, old)
        print(f"    对比 {item['name'][:18]:20s} 相似度 {sim*100:>3.0f}%")
        if sim > best:
            best, best_name = sim, item["name"]
    return best, best_name


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

    # 内容级去重：不同地址装同一批站点的情况很常见（同仓库的不同版本、fork、镜像）
    obj, _ = loads_lenient(raw.decode("utf-8", "ignore"))
    new_keys = extract_keys(obj)
    if new_keys:
        print("[add] 检查是否与池子里的源内容重复 …")
        sim, sim_name = check_duplicate(new_keys, seed, typ)
        if sim >= DUP_REJECT:
            fail(f"与「{sim_name}」内容重复 {sim * 100:.0f}%，装的是同一批站点，不再添加")
        if sim >= DUP_WARN:
            print(f"[add] 注意：与「{sim_name}」重复 {sim * 100:.0f}%，仍然添加")

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
