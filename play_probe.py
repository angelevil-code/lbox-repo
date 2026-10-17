#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L4 播放验证 —— 对 MacCMS 类（api 是 http）的站点跑完整链路：
    搜索 → 详情 → 取第一集地址 → 验证地址能否出流

只处理纯 HTTP 站点。jar / JS 爬虫需要宿主环境，这里不碰，标记为「未验证」。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
KEYWORD = "庆余年"


def http_get(url, timeout=12, headers=None, limit=2_000_000):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit), r.status, int((time.time() - t0) * 1000), dict(r.headers)
    except urllib.error.HTTPError as e:
        return None, e.code, int((time.time() - t0) * 1000), {}
    except Exception as e:
        return None, 0, int((time.time() - t0) * 1000), {"err": type(e).__name__}


def _join(api, query):
    return f"{api}{'&' if '?' in api else '?'}{query}"


def cms_search(api, kw):
    """返回 (items, ac, reason)。reason 用于区分「死站」和「不支持搜索」"""
    reasons = []
    for ac in ("videolist", "detail", "list"):
        body, code, _, _ = http_get(_join(api, f"ac={ac}&wd={urllib.parse.quote(kw)}"))
        if not body:
            reasons.append(f"HTTP {code}" if code else "连接失败")
            continue
        text = body.decode("utf-8", "ignore")
        try:
            data = json.loads(text)
        except Exception:
            snippet = text.strip().replace("\n", " ")[:24]
            if "不支持搜索" in snippet or "关闭" in snippet:
                reasons.append("站点关闭了搜索")
            elif snippet.lstrip().startswith("<"):
                reasons.append("接口失效(返回网页)")
            else:
                reasons.append(f"非JSON:{snippet[:14]}")
            continue
        items = data.get("list") or []
        if items:
            return items, ac, ""
        reasons.append("搜索无结果")
    return [], "", (reasons[0] if reasons else "无结果")


def pick_episode(play_url):
    if not play_url:
        return None, None
    for group in str(play_url).split("$$$"):
        for item in group.split("#"):
            if "$" not in item:
                continue
            name, url = item.split("$", 1)
            url = url.strip()
            if url.startswith("http"):
                return name.strip(), url
    return None, None


def check_media(url, api=""):
    is_m3u8 = ".m3u8" in url.lower()
    body, code, ms, headers = http_get(url, headers={"Range": "bytes=0-4096"})
    if body is None and code == 403 and api:
        ref = "{0.scheme}://{0.netloc}/".format(urllib.parse.urlsplit(api))
        body, code, ms, headers = http_get(
            url, headers={"Range": "bytes=0-4096", "Referer": ref})
    if body is None:
        return {"ok": False, "ms": ms, "why": f"HTTP {code}" if code else "连接失败"}
    head = body[:4096].decode("utf-8", "ignore")
    if is_m3u8:
        if "#EXTM3U" in head:
            return {"ok": True, "ms": ms, "why": "m3u8"}
        return {"ok": False, "ms": ms, "why": "不是有效 m3u8"}
    ctype = (headers.get("Content-Type") or "").lower()
    if "text/html" in ctype:
        return {"ok": False, "ms": ms, "why": "返回网页不是视频"}
    return {"ok": True, "ms": ms, "why": ctype[:24] or "直链"}


def probe_site(site):
    name = site.get("key") or site.get("name") or "?"
    api = site.get("api", "")
    r = {"name": name, "api": api, "from": site.get("from", ""),
         "search": False, "detail": False, "play": False,
         "why": "", "media": "", "ep": "", "ms": 0}

    items, ac, reason = cms_search(api, KEYWORD)
    if not items:
        r["why"] = reason or "搜索无结果"
        return r
    r["search"] = True

    first = items[0]
    play_url = first.get("vod_play_url")
    if not play_url and first.get("vod_id"):
        body, _, _, _ = http_get(_join(api, f"ac=detail&ids={first['vod_id']}"))
        if body:
            try:
                lst = json.loads(body.decode("utf-8", "ignore")).get("list") or []
                if lst:
                    play_url = lst[0].get("vod_play_url")
            except Exception:
                pass
    if not play_url:
        r["why"] = "拿不到播放列表"
        return r
    r["detail"] = True

    ep, media = pick_episode(play_url)
    if not media:
        r["why"] = "播放列表格式异常"
        return r
    r["ep"], r["media"] = ep or "", media

    chk = check_media(media, api)
    r["play"] = chk["ok"]
    r["why"] = chk["why"]
    r["ms"] = chk["ms"]
    return r


def probe_sites(sites, workers=10, limit=20):
    """sites: [{key, api, from}]，只取 api 是 http 的"""
    targets, seen = [], set()
    for s in sites:
        api = str(s.get("api", ""))
        if not api.startswith("http") or api in seen:
            continue
        seen.add(api)
        targets.append(s)
    targets = targets[:limit]
    if not targets:
        return {"keyword": KEYWORD, "total": 0, "playable": 0, "results": []}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(probe_site, targets))
    ok = sum(1 for r in results if r["play"])
    return {"keyword": KEYWORD, "total": len(results), "playable": ok, "results": results}
