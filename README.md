# LBOX 源仓库

一个本地跑的**源聚合站**：同时管「开源阅读（legado）书源」和「TVBox 配置源」，
带可用性检测、搜索、订阅地址导出。零第三方依赖，`python server.py` 就能用。

---

## 快速开始

```bash
cd lbox-repo
python server.py
```

启动后终端会打印两个地址：

```
本机   http://127.0.0.1:8787
局域网 http://192.168.x.x:8787   <- 手机/电视用这个
```

浏览器打开即可。按 `Ctrl+C` 停止。

> 需要 Python 3.8+，不需要 pip install 任何东西。

---

## 能干什么

| 功能 | 说明 |
|---|---|
| **两类源统一管理** | 书源（`bookSourceUrl` 数组）和 TVBox 配置（`sites` / `urls`）放一个列表里 |
| **一键检测** | 并发抓取所有源，判断是「可用 / 失效」，记录条目数和响应延迟 |
| **容错解析** | 真实源文件常带 BOM、`//` 注释、尾逗号、全角引号，标准 `json.loads` 会崩，这里会先清洗再解析 |
| **搜索筛选** | 按名称 / 地址 / 来源搜，按类型切 |
| **导出订阅** | 把可用书源合并去重成**一个** JSON，阅读 App 填一个地址就全导入 |
| **手动加源** | 页面上直接贴地址，或编辑 `seed_sources.json` |

---

## 两种用法

### 方式一：本地跑（能点按钮，完整交互）

```bash
python server.py
```

浏览器打开 `http://127.0.0.1:8787`，可以加源、点检测、看实时状态。

缺点是**电脑关机就没了**，订阅地址只在局域网内有效。

### 方式二：部署到 GitHub Pages（订阅地址永久有效）

订阅地址指向静态文件，由 GitHub 托管，**和你电脑开不开机无关**，手机在外面用流量也能拉。

部署一次，之后每天自动更新。

---

## 部署到 GitHub Pages

### 方式一：一键脚本（推荐）

```bash
gh auth login      # 只需授权一次，用浏览器登录你的 GitHub
python deploy.py   # 建仓库、推代码、开 Pages、开权限、触发构建，全自动
```

跑完直接打印三个地址。详见 `部署手册.md`。

### 方式二：手动操作

### 1. 建仓库并推代码

在 GitHub 新建一个 **public** 仓库（私有仓库用 Pages 要付费），然后：

```bash
cd lbox-repo
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 2. 打开 Actions 写权限

仓库 → **Settings** → **Actions** → **General** → 最下面 **Workflow permissions**
→ 选 **Read and write permissions** → Save

> 不设这一步，定时任务跑完没法把结果提交回仓库。

### 3. 打开 Pages

仓库 → **Settings** → **Pages** → **Source** 选 **Deploy from a branch**
→ Branch 选 **main**，目录选 **/docs** → Save

### 4. 手动跑一次

仓库 → **Actions** → 左边选 **update-sources** → 右边 **Run workflow** → 绿色按钮

等 1~2 分钟，`docs/` 会被重新生成并提交。

### 5. 拿到你的地址

```
主页        https://<用户名>.github.io/<仓库名>/
书源订阅    https://<用户名>.github.io/<仓库名>/book.json
TVBox 订阅  https://<用户名>.github.io/<仓库名>/tvbox.json
```

把后两个填进「阅读」App / TVBox，**这个地址永久有效**。

### 之后怎么维护

| 想做什么 | 怎么做 |
|---|---|
| 加一个源 | 改 `seed_sources.json` 推到 GitHub，自动触发重建 |
| 立刻重新检测 | Actions → update-sources → Run workflow |
| 改检测频率 | 编辑 `.github/workflows/update.yml` 里的 `cron` |
| 看每天结果 | 直接打开 Pages 主页 |

默认每天跑一次：`cron: "0 20 * * *"`（UTC 20:00 = **北京时间次日 04:00**）。

---

## 本地模式的订阅地址怎么用

点页面右上角的「书源订阅地址」/「TVBox 订阅地址」，会复制类似：

```
http://192.168.1.10:8787/api/export?type=book
http://192.168.1.10:8787/api/export?type=tvbox
```

- **书源**：打开「阅读」App → 我的 → 书源管理 → 右上角 → 网络导入 → 粘贴地址
- **TVBox**：设置 → 配置地址 → 粘贴地址

⚠️ 两个前提：
1. 手机/电视和电脑必须**在同一个 WiFi** 下
2. 电脑不能关机，服务得一直开着

**要 7×24 可用，用上面的 GitHub Pages 方式。**

---

## 目录结构

```
lbox-repo/
├── server.py           # 全部后端 + 静态构建，零依赖
├── seed_sources.json   # 种子源清单，可自己加
├── static/             # 本地交互版前端
│   ├── index.html
│   ├── style.css
│   └── app.js
├── docs/               # 构建产物（GitHub Pages 从这里发布）
│   ├── index.html      # 静态展示页
│   ├── book.json       # 书源订阅
│   ├── tvbox.json      # TVBox 订阅
│   └── index.json      # 元数据
├── .github/workflows/
│   └── update.yml      # 每天自动检测 + 提交
└── data/
    └── lbox.db         # SQLite，首次启动自动生成
```

### 两个命令

```bash
python server.py                 # 启动本地服务（可交互）
python server.py --build --out docs   # 只跑检测，输出静态站
```

---

## 加源的三种方式

**1. 页面添加**（最方便）
点「添加源」，选类型、填地址。加完会自动检测一次。

**2. 改种子文件**（适合批量）
编辑 `seed_sources.json`，加一条：

```json
{
  "type": "book",
  "name": "某某书源",
  "url": "https://raw.githubusercontent.com/xxx/xxx/main/book.json",
  "origin": "github/xxx"
}
```

重启后自动导入。已存在的地址不会重复加，也不会覆盖检测结果。

**3. 直接问接口**

```bash
curl -X POST http://127.0.0.1:8787/api/sources \
  -H "Content-Type: application/json" \
  -d '{"type":"tvbox","name":"测试","url":"https://..."}'
```

---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sources?type=&q=&status=` | 列表，支持类型/关键字/状态筛选 |
| GET | `/api/stats` | 统计：各类总数、可用数、条目数、最后检测时间 |
| POST | `/api/check` | 检测全部；传 `{"ids":[1,2]}` 只测指定几条 |
| POST | `/api/sources` | 添加源 `{type,name,url}` |
| DELETE | `/api/sources/{id}` | 删除 |
| GET | `/api/export?type=book` | 合并导出可用书源（去重） |
| GET | `/api/export?type=tvbox` | 导出多仓格式 `{"urls":[...]}` |

---

## 检测逻辑

```
拉取 → 去掉 BOM/注释/尾逗号 → 严格 JSON 解析 → 失败则清洗后重试
     ↓
判断结构：
  数组 + 有 bookSourceUrl  → 书源，count = 条目数
  dict + 有 sites          → TVBox 单仓，count = 站点数
  dict + 有 urls           → TVBox 多仓，count = 仓数
  其它                     → 标记失效，记下原因
```

并发 8 线程，单源超时 15 秒，失败重试 1 次。

---

## 已知边界

- **只测「配置能不能拉到、结构对不对」**，不测「里面的影视源能不能播」。
  因为 TVBox 配置里 84% 的站点是 jar/JS 爬虫，必须由客户端执行，Python 跑不了。
  真要测播放，得在 TVBox 客户端里测。
- **本地模式电脑关机服务就停**。要 7×24 可用，用 GitHub Pages 方式（见上文），或放 VPS / 树莓派。
- **GitHub Actions 的定时不是精确的**。高峰期可能延迟几分钟到十几分钟，个人用无影响。
- **仓库设为 public 意味着源清单公开**。介意就换私有仓库 + 自建托管。
- **源会腐烂**。建议每周点一次「检测全部」，失效的清掉、补新的。

---

## 关于内容

本工具只做**地址索引**：记录哪个地址指向一份配置，不托管、不缓存、不代理任何媒体内容。
配置里指向什么，由配置提供方决定，与本工具无关。仅供个人整理自用。
