const $ = (sel) => document.querySelector(sel);

const state = { type: "all", q: "", sources: [], stats: null, busy: false };

/* ---------------- 工具 ---------------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

function toast(msg, ms = 2200) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch {}
    ta.remove();
    return ok;
  }
}

async function api(path, opt = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opt,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function relTime(ts) {
  if (!ts) return "—";
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/* ---------------- 渲染 ---------------- */
function unitText(src) {
  if (src.kind === "depot") return `${src.count} 个仓`;
  if (src.kind === "book" || src.type === "book") return `${src.count} 条书源`;
  return `${src.count} 个站点`;
}

function cardHTML(src) {
  const st = src.status || "unchecked";
  const typeLabel = src.type === "book" ? "书源" : "TVBox";
  let meta = "";
  if (st === "ok") {
    meta = `<span>${unitText(src)}</span><span class="sep">·</span><span>${src.latency}ms</span>`;
  } else if (st === "dead") {
    meta = `<span class="err">${esc(src.error || "不可用")}</span>`;
  } else {
    meta = `<span>未检测</span>`;
  }
  const origin = src.origin
    ? `<span class="sep">·</span><span>${esc(src.origin)}</span>` : "";

  return `
  <article class="card" data-id="${src.id}">
    <div class="card-top">
      <span class="status-dot ${st}"></span>
      <h3 class="card-title" title="${esc(src.name)}">${esc(src.name)}</h3>
      <span class="badge ${src.type}">${typeLabel}</span>
    </div>
    <div class="card-meta">${meta}${origin}</div>
    <div class="card-url" title="${esc(src.url)}">${esc(src.url)}</div>
    <div class="card-actions">
      <button class="mini" data-act="copy">复制地址</button>
      <button class="mini" data-act="check">检测</button>
      <button class="mini danger" data-act="del">删除</button>
    </div>
  </article>`;
}

function render() {
  const list = $("#list");
  if (!state.sources.length) {
    list.innerHTML = "";
    $("#empty").hidden = false;
  } else {
    $("#empty").hidden = true;
    list.innerHTML = state.sources.map(cardHTML).join("");
  }
  renderStats();
}

function renderStats() {
  const s = state.stats;
  if (!s) return;
  const b = s.book, t = s.tvbox;
  $("#st-book-ok").textContent = b.ok;
  $("#st-book-total").textContent = `/ ${b.total} 可用`;
  $("#st-book-items").textContent = `${b.items} 条书源`;
  $("#st-tv-ok").textContent = t.ok;
  $("#st-tv-total").textContent = `/ ${t.total} 可用`;
  $("#st-tv-items").textContent = `${t.items} 个站点`;
  $("#st-last").textContent = relTime(s.last_checked);
  $("#st-last").style.fontSize = "15px";
}

/* ---------------- 数据 ---------------- */
async function load() {
  const qs = new URLSearchParams();
  if (state.type !== "all") qs.set("type", state.type);
  if (state.q) qs.set("q", state.q);
  const [sources, stats] = await Promise.all([
    api(`/api/sources?${qs.toString()}`),
    api("/api/stats"),
  ]);
  state.sources = sources;
  state.stats = stats;
  render();
}

async function checkAll() {
  if (state.busy) return;
  state.busy = true;
  const btn = $("#btn-check");
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "检测中…";
  toast("正在检测所有源，可能需要几十秒", 4000);
  try {
    const r = await api("/api/check", { method: "POST", body: "{}" });
    toast(`检测完成：${r.ok} 可用 / ${r.dead} 失效`);
    await load();
  } catch (e) {
    toast(`检测失败：${e.message}`, 3200);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
    state.busy = false;
  }
}

async function checkOne(id) {
  try {
    const r = await api("/api/check", { method: "POST", body: JSON.stringify({ ids: [id] }) });
    toast(r.ok ? "可用" : "不可用");
    await load();
  } catch (e) {
    toast(`检测失败：${e.message}`);
  }
}

async function delOne(id, name) {
  if (!confirm(`确定删除「${name}」？`)) return;
  try {
    await api(`/api/sources/${id}`, { method: "DELETE" });
    toast("已删除");
    await load();
  } catch (e) {
    toast(`删除失败：${e.message}`);
  }
}

async function addSource(form) {
  const data = {
    type: form.type.value,
    name: form.name.value.trim(),
    url: form.url.value.trim(),
  };
  try {
    await api("/api/sources", { method: "POST", body: JSON.stringify(data) });
    toast("已添加，正在检测…");
    const id = (await api(`/api/sources?q=${encodeURIComponent(data.url)}`))[0]?.id;
    if (id) await checkOne(id);
    await load();
  } catch (e) {
    toast(e.message, 3000);
  }
}

/* ---------------- 事件 ---------------- */
$("#btn-check").addEventListener("click", checkAll);

$("#btn-add").addEventListener("click", () => $("#dlg").showModal());
$("#btn-cancel").addEventListener("click", () => $("#dlg").close());

$("#add-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  $("#dlg").close();
  await addSource(form);
  form.reset();
});

$("#tabs").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".tab");
  if (!btn) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");
  state.type = btn.dataset.type;
  load();
});

let searchTimer;
$("#search").addEventListener("input", (ev) => {
  clearTimeout(searchTimer);
  const v = ev.target.value.trim();
  searchTimer = setTimeout(() => { state.q = v; load(); }, 280);
});

$("#list").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".mini");
  if (!btn) return;
  const card = btn.closest(".card");
  const id = Number(card.dataset.id);
  const src = state.sources.find((s) => s.id === id);
  if (!src) return;

  if (btn.dataset.act === "copy") {
    const ok = await copyText(src.url);
    toast(ok ? "地址已复制" : "复制失败，请手动选择");
  } else if (btn.dataset.act === "check") {
    await checkOne(id);
  } else if (btn.dataset.act === "del") {
    await delOne(id, src.name);
  }
});

async function copyExport(type) {
  const url = `${location.origin}/api/export?type=${type}`;
  const ok = await copyText(url);
  const tip = type === "book"
    ? "书源订阅地址已复制，填到「阅读」App 的订阅里"
    : "TVBox 订阅地址已复制，填到 TVBox 的配置地址里";
  toast(ok ? `${tip}（手机需用局域网地址）` : "复制失败", 3600);
}

$("#btn-export-book").addEventListener("click", () => copyExport("book"));
$("#btn-export-tvbox").addEventListener("click", () => copyExport("tvbox"));

/* ---------------- 启动 ---------------- */
load().catch((e) => toast(`加载失败：${e.message}`, 4000));
