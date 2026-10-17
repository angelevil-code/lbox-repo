#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LBOX 源仓库 —— 零依赖本地服务

同时管理两类源：
  book  : 开源阅读(legado) 书源 JSON
  tvbox : TVBox / 影视仓 配置 JSON

运行： python server.py
访问： http://127.0.0.1:8787

不依赖任何第三方包，只用标准库。
"""

import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "lbox.db")
SEED_PATH = os.path.join(ROOT, "seed_sources.json")
STATIC_DIR = os.path.join(ROOT, "static")

PORT = 8787
TIMEOUT = 15
MAX_BYTES = 32 * 1024 * 1024
UA = ("Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

_DB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 数据库

def db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _DB_LOCK, closing(db()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT NOT NULL DEFAULT 'book',
                kind        TEXT DEFAULT '',
                name        TEXT NOT NULL,
                url         TEXT NOT NULL UNIQUE,
                origin      TEXT DEFAULT '',
                count       INTEGER DEFAULT 0,
                status      TEXT DEFAULT 'unchecked',
                latency     INTEGER DEFAULT 0,
                error       TEXT DEFAULT '',
                checked_at  INTEGER DEFAULT 0,
                created_at  INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON sources(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON sources(status)")


def load_seed():
    """首次启动导入种子清单；已存在的 URL 不覆盖（保留用户的检测结果）"""
    if not os.path.exists(SEED_PATH):
        return 0
    try:
        with open(SEED_PATH, encoding="utf-8") as f:
            seed = json.load(f)
    except Exception as e:
        print(f"[seed] 读取失败: {e}")
        return 0
    added = 0
    now = int(time.time())
    with _DB_LOCK, closing(db()) as conn, conn:
        for item in seed.get("sources", []):
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO sources(type,name,url,origin,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (item.get("type", "book"), item["name"], item["url"],
                     item.get("origin", ""), now),
                )
                added += cur.rowcount
            except Exception as e:
                print(f"[seed] 跳过 {item.get('name')}: {e}")
    return added


# ---------------------------------------------------------------- 网络与解析

def to_punycode(url: str) -> str:
    """中文域名转 punycode，否则 urllib 抛 latin-1 编码错误"""
    p = urllib.parse.urlsplit(url)
    host = p.hostname or ""
    if host and any(ord(c) > 127 for c in host):
        try:
            netloc = host.encode("idna").decode("ascii")
            if p.port:
                netloc += f":{p.port}"
            url = urllib.parse.urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))
        except Exception:
            pass
    return url


def normalize(url: str) -> str:
    url = url.strip()
    for prefix in ("https://gh-proxy.com/", "https://ghproxy.net/",
                   "https://mirror.ghproxy.com/", "https://ghproxy.cc/"):
        if url.startswith(prefix):
            url = "https://" + url[len(prefix):]
            break
    url = to_punycode(url)
    return urllib.parse.quote(url, safe=":/?&=#%+~@,;!$'()*[]")


def fetch(url: str, timeout: int = TIMEOUT, retries: int = 1):
    """返回 (bytes|None, 耗时ms, 错误信息)"""
    last_err = ""
    elapsed = 0
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(MAX_BYTES)
                return body, int((time.time() - t0) * 1000), ""
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            last_err = f"网络错误: {str(reason)[:60]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
        elapsed = int((time.time() - t0) * 1000)
        if attempt < retries:
            time.sleep(0.8)
    return None, elapsed, last_err


def _strip_noise(text: str) -> str:
    """清掉真实源文件里常见的脏东西：BOM、注释、尾逗号、全角引号"""
    text = text.lstrip("\ufeff").strip()
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)      # 行注释（只匹配行首，不会误伤 http://）
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)       # 块注释
    text = re.sub(r",\s*([}\]])", r"\1", text)              # 尾逗号
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    return text


def loads_lenient(text: str):
    """先严格解析，失败再清洗。返回 (obj, err)"""
    try:
        return json.loads(text), ""
    except Exception as first_err:
        cleaned = _strip_noise(text)
        try:
            return json.loads(cleaned), ""
        except Exception:
            return None, f"JSON 解析失败: {str(first_err)[:60]}"


def detect(body: bytes):
    """
    识别源类型与条目数
    返回 (kind, type, count, err)
      kind: book | tvbox | depot | unknown
    """
    text = body.decode("utf-8", "ignore")
    obj, err = loads_lenient(text)
    if obj is None:
        return "unknown", "", 0, err

    if isinstance(obj, list):
        books = [x for x in obj if isinstance(x, dict)
                 and ("bookSourceUrl" in x or "bookSourceName" in x)]
        if books:
            return "book", "book", len(books), ""
        return "unknown", "", 0, f"数组但无书源字段（{len(obj)} 条）"

    if isinstance(obj, dict):
        if isinstance(obj.get("sites"), list):
            return "tvbox", "tvbox", len(obj["sites"]), ""
        if isinstance(obj.get("urls"), list):
            return "depot", "tvbox", len(obj["urls"]), ""
        if "bookSourceUrl" in obj:
            return "book", "book", 1, ""
        if "msg" in obj:
            return "unknown", "", 0, f"接口返回 msg: {str(obj['msg'])[:40]}"
        return "unknown", "", 0, f"未知结构，字段: {','.join(list(obj.keys())[:5])}"

    return "unknown", "", 0, "内容既不是对象也不是数组"


def check_source(row) -> dict:
    url = normalize(row["url"])
    # 重试 2 次：网络瞬时抖动很常见，一次失败就判死会误杀好源
    body, ms, err = fetch(url, timeout=12, retries=2)
    if body is None:
        return {"status": "dead", "latency": ms, "error": err,
                "count": 0, "kind": row["kind"] or ""}
    kind, typ, count, derr = detect(body)
    if kind == "unknown":
        return {"status": "dead", "latency": ms, "error": derr,
                "count": 0, "kind": row["kind"] or ""}
    return {"status": "ok", "latency": ms, "error": "", "count": count, "kind": kind}


# ---------------------------------------------------------------- 业务

def list_sources(q="", typ="", status=""):
    sql = "SELECT * FROM sources WHERE 1=1"
    args = []
    if q:
        sql += " AND (name LIKE ? OR url LIKE ? OR origin LIKE ?)"
        like = f"%{q}%"
        args += [like, like, like]
    if typ:
        sql += " AND type = ?"
        args.append(typ)
    if status:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY (status='ok') DESC, count DESC, id ASC"
    with _DB_LOCK, closing(db()) as conn, conn:
        return [dict(r) for r in conn.execute(sql, args)]


def stats():
    with _DB_LOCK, closing(db()) as conn, conn:
        rows = conn.execute(
            "SELECT type, status, COUNT(*) n, SUM(count) total FROM sources GROUP BY type, status"
        ).fetchall()
        last = conn.execute("SELECT MAX(checked_at) m FROM sources").fetchone()["m"] or 0
    out = {"book": {"total": 0, "ok": 0, "dead": 0, "items": 0},
           "tvbox": {"total": 0, "ok": 0, "dead": 0, "items": 0},
           "last_checked": last}
    for r in rows:
        t = r["type"] if r["type"] in ("book", "tvbox") else "book"
        out[t]["total"] += r["n"]
        out[t]["items"] += r["total"] or 0
        if r["status"] == "ok":
            out[t]["ok"] += r["n"]
        elif r["status"] == "dead":
            out[t]["dead"] += r["n"]
    return out


def check_all(ids=None):
    if ids:
        placeholders = ",".join("?" * len(ids))
        with _DB_LOCK, closing(db()) as conn, conn:
            rows = conn.execute(
                f"SELECT * FROM sources WHERE id IN ({placeholders})", ids).fetchall()
    else:
        with _DB_LOCK, closing(db()) as conn, conn:
            rows = conn.execute("SELECT * FROM sources").fetchall()
    rows = [dict(r) for r in rows]
    if not rows:
        return {"checked": 0, "ok": 0, "dead": 0}

    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(check_source, r): r for r in rows}
        for fut in futures:
            row = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"status": "dead", "latency": 0, "error": str(e)[:80],
                       "count": 0, "kind": row["kind"] or ""}
            results.append((row["id"], res))

    now = int(time.time())
    with _DB_LOCK, closing(db()) as conn, conn:
        for sid, res in results:
            conn.execute(
                "UPDATE sources SET status=?, latency=?, error=?, count=?, kind=?, checked_at=? "
                "WHERE id=?",
                (res["status"], res["latency"], res["error"], res["count"],
                 res["kind"], now, sid),
            )
    ok = sum(1 for _, r in results if r["status"] == "ok")
    return {"checked": len(results), "ok": ok, "dead": len(results) - ok}


def add_source(name, url, typ="book", origin="手动添加"):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("地址必须以 http:// 或 https:// 开头")
    with _DB_LOCK, closing(db()) as conn, conn:
        try:
            conn.execute(
                "INSERT INTO sources(type,name,url,origin,created_at) VALUES(?,?,?,?,?)",
                (typ, name or url[:40], url, origin, int(time.time())),
            )
        except sqlite3.IntegrityError:
            raise ValueError("这个地址已经存在了")
        return conn.execute("SELECT * FROM sources WHERE url=?", (url,)).fetchone()["id"]


def delete_source(sid):
    with _DB_LOCK, closing(db()) as conn, conn:
        conn.execute("DELETE FROM sources WHERE id=?", (sid,))
        return conn.total_changes


_export_cache = {}
_export_lock = threading.Lock()


def export_book():
    """把所有可用书源合并成一份 JSON（按 bookSourceUrl 去重），可直接导入阅读 App"""
    with _export_lock:
        cached = _export_cache.get("book")
        if cached and time.time() - cached[0] < 120:
            return cached[1]

    rows = list_sources(typ="book", status="ok")
    merged, seen = [], set()

    def pull(row):
        body, _, _ = fetch(normalize(row["url"]), retries=0)
        if not body:
            return []
        obj, _ = loads_lenient(body.decode("utf-8", "ignore"))
        return obj if isinstance(obj, list) else []

    with ThreadPoolExecutor(max_workers=6) as pool:
        for items in pool.map(pull, rows):
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = item.get("bookSourceUrl") or item.get("bookSourceName")
                if key and key not in seen:
                    seen.add(key)
                    merged.append(item)

    with _export_lock:
        _export_cache["book"] = (time.time(), merged)
    return merged


def export_tvbox():
    """输出标准多仓格式 {"urls":[...]}，TVBox/影视仓 可直接订阅"""
    rows = list_sources(typ="tvbox", status="ok")
    urls = []
    for r in rows:
        if r["kind"] == "depot":
            urls.append({"url": r["url"], "name": r["name"]})
        elif r["kind"] == "tvbox":
            urls.append({"url": r["url"], "name": r["name"]})
    return {"urls": urls}


# ---------------------------------------------------------------- 静态构建

def _fmt_time(ts):
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _static_card(src):
    st = src["status"]
    type_label = "书源" if src["type"] == "book" else "TVBox"
    if st == "ok":
        if src["kind"] == "depot":
            unit = f'{src["count"]} 个仓'
        elif src["type"] == "book":
            unit = f'{src["count"]} 条书源'
        else:
            unit = f'{src["count"]} 个站点'
        meta = f"<span>{unit}</span><span class='sep'>·</span><span>{src['latency']}ms</span>"
    elif st == "dead":
        meta = f"<span class='err'>{src['error'] or '不可用'}</span>"
    else:
        meta = "<span>未检测</span>"
    origin = f"<span class='sep'>·</span><span>{src['origin']}</span>" if src["origin"] else ""
    return f"""
    <article class="card">
      <div class="card-top">
        <span class="status-dot {st}"></span>
        <h3 class="card-title">{html_escape(src['name'])}</h3>
        <span class="badge {src['type']}">{type_label}</span>
      </div>
      <div class="card-meta">{meta}{origin}</div>
      <div class="card-url">{html_escape(src['url'])}</div>
      <div class="card-actions">
        <button class="mini" data-copy="{html_escape(src['url'])}">复制地址</button>
      </div>
    </article>"""


def html_escape(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_static(sources, st, built_at):
    css_file = os.path.join(STATIC_DIR, "style.css")
    css = ""
    if os.path.exists(css_file):
        with open(css_file, encoding="utf-8") as f:
            css = f.read()

    book, tv = st["book"], st["tvbox"]
    cards = "".join(_static_card(s) for s in sources)
    updated = _fmt_time(built_at)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LBOX 源仓库</title>
<style>{css}</style>
<style>
.sub-box {{ background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 16px; margin: 0 28px 16px; display: grid; gap: 10px; }}
.sub-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.sub-label {{ font-size: 12px; color: var(--muted); width: 76px; flex: none; }}
.sub-url {{ flex: 1; min-width: 200px; font-family: ui-monospace, Consolas, monospace; font-size: 12px;
  color: var(--text); background: rgba(255,255,255,.03); border: 1px solid var(--border);
  border-radius: 7px; padding: 7px 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.note {{ padding: 0 28px 40px; color: var(--dim); font-size: 12px; }}
@media (max-width: 860px) {{ .sub-box, .note {{ margin-left: 16px; margin-right: 16px; padding-left: 0; padding-right: 0; }} }}
</style>
</head>
<body>

<header class="topbar">
  <div class="brand">
    <div class="logo">LB</div>
    <div class="brand-text">
      <h1>LBOX 源仓库</h1>
      <p>书源 · TVBox 配置 · 每日自动更新</p>
    </div>
  </div>
  <div class="top-actions"><span class="badge">更新于 {updated}</span></div>
</header>

<section class="stats">
  <div class="stat">
    <div class="stat-head"><span class="dot book"></span>书源</div>
    <div class="stat-value"><b>{book['ok']}</b><span>/ {book['total']} 可用</span></div>
    <div class="stat-foot"><span>{book['items']} 条书源</span></div>
  </div>
  <div class="stat">
    <div class="stat-head"><span class="dot tvbox"></span>TVBox 配置</div>
    <div class="stat-value"><b>{tv['ok']}</b><span>/ {tv['total']} 可用</span></div>
    <div class="stat-foot"><span>{tv['items']} 个站点</span></div>
  </div>
  <div class="stat">
    <div class="stat-head"><span class="dot time"></span>最后更新</div>
    <div class="stat-value"><b style="font-size:15px">{updated}</b></div>
    <div class="stat-foot"><span>每天自动检测一次</span></div>
  </div>
</section>

<section class="sub-box">
  <div class="sub-row">
    <span class="sub-label">书源订阅</span>
    <span class="sub-url" id="u-book">—</span>
    <button class="mini" data-copy-el="u-book">复制</button>
  </div>
  <div class="sub-row">
    <span class="sub-label">TVBox 订阅</span>
    <span class="sub-url" id="u-tvbox">—</span>
    <button class="mini" data-copy-el="u-tvbox">复制</button>
  </div>
</section>

<main class="grid">{cards}
</main>

<p class="note">数据由 GitHub Actions 每天自动检测生成。失效的源已自动排除在订阅之外。</p>

<div id="toast" class="toast"></div>

<script>
const base = location.origin + location.pathname.replace(/index\\.html$/, "");
document.getElementById("u-book").textContent = base + "book.json";
document.getElementById("u-tvbox").textContent = base + "tvbox.json";

function toast(msg) {{
  const el = document.getElementById("toast");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 1800);
}}
async function copy(text) {{
  try {{ await navigator.clipboard.writeText(text); return true; }}
  catch {{
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try {{ ok = document.execCommand("copy"); }} catch {{}}
    ta.remove(); return ok;
  }}
}}
document.addEventListener("click", async (e) => {{
  const btn = e.target.closest("[data-copy],[data-copy-el]");
  if (!btn) return;
  const text = btn.dataset.copy || document.getElementById(btn.dataset.copyEl).textContent;
  toast(await copy(text) ? "已复制" : "复制失败");
}});
</script>
</body>
</html>"""


def build_static(outdir):
    """跑一遍检测，把结果输出成可直接托管的静态站"""
    os.makedirs(outdir, exist_ok=True)
    # GitHub Pages 默认走 Jekyll，放这个空文件让它跳过处理
    open(os.path.join(outdir, ".nojekyll"), "w").close()
    print("[build] 开始检测所有源 …")
    r = check_all()
    print(f"[build] 检测完成：{r['ok']} 可用 / {r['dead']} 失效")

    print("[build] 合并书源 …")
    book = export_book()
    tvbox = export_tvbox()
    sources = list_sources()
    st = stats()
    built_at = int(time.time())

    def dump(name, obj):
        path = os.path.join(outdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        print(f"[build] {name:14s} {os.path.getsize(path):>9,d} B")

    dump("book.json", book)
    dump("tvbox.json", tvbox)
    dump("index.json", {
        "built_at": built_at,
        "built_at_text": _fmt_time(built_at),
        "stats": st,
        "sources": sources,
        "book_count": len(book),
        "tvbox_count": len(tvbox.get("urls", [])),
    })

    page = render_static(sources, st, built_at)
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"[build] index.html     {os.path.getsize(os.path.join(outdir, 'index.html')):>9,d} B")

    print(f"[build] 完成 -> {outdir}")
    print(f"[build] 书源 {len(book)} 条 | TVBox {len(tvbox.get('urls', []))} 个仓")
    return {"book": len(book), "tvbox": len(tvbox.get("urls", []))}


# ---------------------------------------------------------------- HTTP

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png"}


class Handler(BaseHTTPRequestHandler):
    server_version = "LBOXRepo/1.0"

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers -------------------------------------------------
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

    # -- routes --------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/sources":
            return self._json(list_sources(
                q=(qs.get("q") or [""])[0],
                typ=(qs.get("type") or [""])[0],
                status=(qs.get("status") or [""])[0]))
        if path == "/api/stats":
            return self._json(stats())
        if path == "/api/export":
            typ = (qs.get("type") or ["book"])[0]
            if typ == "book":
                data = export_book()
                return self._json(data)
            return self._json(export_tvbox())
        if path == "/api/seed":
            return self._json({"added": load_seed()})
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return self._static(path)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        data = self._body()

        if path == "/api/check":
            ids = data.get("ids")
            return self._json(check_all(ids if isinstance(ids, list) and ids else None))
        if path == "/api/sources":
            try:
                sid = add_source(data.get("name", ""), data.get("url", ""),
                                 data.get("type", "book"), data.get("origin", "手动添加"))
                return self._json({"id": sid, "ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        m = re.match(r"^/api/sources/(\d+)$", parsed.path)
        if m:
            n = delete_source(int(m.group(1)))
            return self._json({"deleted": n})
        return self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        })


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="LBOX 源仓库：书源 + TVBox 配置源管理")
    ap.add_argument("--build", action="store_true",
                    help="只跑检测并输出静态站，不启动 HTTP 服务")
    ap.add_argument("--out", default="docs", help="静态站输出目录（默认 docs）")
    args = ap.parse_args()

    init_db()
    added = load_seed()

    if args.build:
        build_static(os.path.abspath(args.out))
        return

    port = PORT
    for _ in range(10):
        try:
            httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
            break
        except OSError:
            port += 1
    else:
        print("端口被占用，启动失败")
        return

    st = stats()
    print("=" * 58)
    print("  LBOX 源仓库")
    print("=" * 58)
    if added:
        print(f"  已导入种子源 {added} 条")
    print(f"  书源 {st['book']['total']} 条 | TVBox {st['tvbox']['total']} 条")
    print(f"  本机   http://127.0.0.1:{port}")
    print(f"  局域网 http://{lan_ip()}:{port}   <- 手机/电视用这个")
    print("=" * 58)
    print("  Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
