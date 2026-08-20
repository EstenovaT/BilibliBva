# B站视频在线解析（biliblivBva）

粘贴 B 站链接 / BV 号 / AV 号，一键获取视频信息（标题、UP 主、封面、播放量等）、
mp4 临时直链、永久解析链接，并支持分 P 与画质选择（360P ~ 4K）。

- **本地（Python）**：`src/script/start_local.py` 一键启动 + 自动开浏览器
- **云端（Cloudflare Worker）**：根目录 `index.js` 用 JS 重写的同功能后端，
  可部署到 Cloudflare Workers / Pages，纯静态网页也能在线解析

---

## 目录结构

```
biliblivBva/
├── docs/
│   └── 项目计划表.txt        # 项目说明与部署方案
├── src/
│   ├── script/               # 本地启动脚本（Python）
│   │   ├── start_local.py    #   一键启动 + 环境自检（--check）
│   │   ├── start_local.bat   #   Windows 双击启动
│   │   ├── start_local.sh    #   Linux/macOS 启动
│   │   └── test_driver.py    #   6 项接口自测
│   ├── server/
│   │   └── resolve_server.py # Python 后端（http.server，零第三方依赖）
│   └── webui/
│       └── index.html        # 在线解析网页（纯前端，无第三方依赖）
├── index.js                  # Cloudflare Worker（JS 版后端）
│                             #   路由：/api/resolve、/pic、/proxy、302 永久链接
├── wrangler.toml             # Wrangler 配置（静态资源 + 环境变量）
├── package.json              # pnpm dev / deploy
├── pnpm-lock.yaml            # 锁文件（Cloudflare/GitHub 自动识别 pnpm）
└── pnpm-workspace.yaml       # pnpm 构建脚本许可（esbuild/workerd）
```

> Worker 配置放在**仓库根目录**：Cloudflare Workers Builds / GitHub Actions
> 连接仓库后即可自动识别（根目录默认 `/`、锁文件自动识别 pnpm、构建命令可留空）。

## 快速开始

### 方式一：本地 Python 预览（推荐先跑这个）

需要 Python 3.7+（无需安装任何第三方包）。

```bash
# 一键启动（默认 8080 端口，自动打开浏览器，代理播放默认开启）
python src/script/start_local.py

# 或仅环境自检（不启动服务）
python src/script/start_local.py --check

# 跑 6 项接口自测（结束后自动关闭服务）
python src/script/test_driver.py
```

Windows 也可直接双击 `src/script/start_local.bat`。

### 方式二：Cloudflare Worker 本地预览

需要 Node.js 18+ 与 pnpm（`corepack enable` 或 `npm i -g pnpm`），先安装依赖（仅首次）：

```bash
pnpm install
pnpm dev          # 等价于 pnpm exec wrangler dev，默认 http://localhost:8787
```

> 说明：`pnpm install` 会执行 esbuild / workerd 的构建脚本下载平台二进制，
> 相关许可已在根目录 `pnpm-workspace.yaml` 的 `allowBuilds` 中声明。

`pnpm dev` 会同时提供静态网页（`src/webui/`）和 Worker API，
浏览器打开 `http://localhost:8787/` 即可体验完整功能。

## 部署到 Cloudflare

```bash
pnpm login        # 或 pnpm exec wrangler login，授权 Cloudflare 账号
pnpm deploy       # 部署 Worker，输出形如 https://bva-resolve.<你的子域>.workers.dev
```

部署成功后：
1. 访问输出的 `*.workers.dev` 地址即可在线解析（永久链接会跟随该域名）；
2. 在 Cloudflare 控制台给 Worker 绑定自定义域（如 `bva.estenova.top`，
   需先把域名接入 Cloudflare 并添加 CNAME 记录到该 Worker），
   绑定后永久链接自动变成 `https://bva.estenova.top/?bv=...&p=...`。

### 方式一：Workers Builds 自动部署（免手写构建命令）

Worker 配置已在**仓库根目录**，连接仓库后 Cloudflare 自动识别：

- **Root directory（根目录）**：保持默认 `/`（wrangler.toml 在根目录，无需填 `src/worker`）
- **Build command（构建命令）**：**留空**——Cloudflare 根据根目录 `pnpm-lock.yaml`
  自动识别 pnpm 并安装依赖，随后按 `wrangler.toml` 自动部署
- 若构建环境不识别 pnpm，再填 `pnpm install`（仅此一句，无需 deploy）

### 方式二：GitHub Actions 自动部署（推荐，仓库已内置）

仓库已包含 `.github/workflows/deploy.yml`，push 到 `main`/`master` 后自动
`pnpm install` + `wrangler deploy`，不依赖 Cloudflare 构建环境。只需配置一次：

1. Cloudflare 控制台 → My Profile → **API Tokens** → Create Token →
   选 "Edit Cloudflare Workers" 模板，复制 Token；
2. GitHub 仓库 → **Settings → Secrets and variables → Actions** →
   New repository secret，名称填 `CLOUDFLARE_API_TOKEN`，粘贴 Token；
3. 推送代码即自动部署，可在仓库 **Actions** 页查看部署日志。

## API 说明

| 路由 | 说明 |
|---|---|
| `GET /` | 返回在线解析网页（默认版：仅直接播放） |
| `GET /ex` | **完全版网页（隐藏入口）**：开启代理播放按钮；`/proxy` 亦仅在此模式下可用 |
| `GET /api/resolve?url=&p=&qn=` | JSON：视频信息 + mp4 直链 + 永久链接 + `proxy_enabled` |
| `GET /?bv=xxx&p=N` | 兼容 bot 永久链接：302 跳转到 mp4 直链 |
| `GET /pic?url=` | 封面图片代理（带 B 站 Referer，解决防盗链 403） |
| `GET /proxy?url=` | 视频代理播放（需 `/ex` 完全版或 `PROXY=1`） |

示例：

```bash
curl "http://localhost:8787/api/resolve?url=BV1xx411c7mD&p=1&qn=80"
```

## 环境变量（Python 版）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8080` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `BILI_PROXY` | `0` | `1` 时开启 `/proxy` 代理播放 |
| `PERM_DOMAIN` | `bva.estenova.top` | 永久解析链接域名 |
| `BILI_INDEX` | `../webui/index.html` | 网页文件路径覆盖 |

## 环境变量（Worker 版，见 wrangler.toml）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROXY` | `0` | `1` 时开启 `/proxy` 代理播放 |

## 已知限制与注意事项

- **防盗链**：B 站视频直链和封面图都校验 `Referer`，浏览器直连可能 403。
  网页提供「复制直链」（配合 IDM/迅雷等工具下载）与「代理播放」两种方式；
  代理播放由后端转发流量，公开站点注意流量成本。
- **B 站风控（-412）**：B 站对异常 IP/频率有风控，解析接口可能偶发
  `-412`，稍后重试或更换网络即可；Cloudflare 出口 IP 触发概率相对更高。
- **代理播放为隐藏功能**：在线版默认页面只提供「直接播放」与复制直链；
  在域名后加 `/ex`（如 `https://bva.estenova.top/ex`）打开完全版网页，
  即可使用「代理播放」。
- **Worker 免费额度**：免费版有请求数/CPU 时长限制，代理整段视频流可能
  超时，公开站建议保持 `PROXY=0`（网页会自动隐藏「代理播放」按钮）。
- **画质降级**：1080P+ / 4K 等画质依赖账号登录态，未登录请求会被
  B 站自动降级到可用画质，网页上会标注「已降级」。

## 常见问题

**Q: 本地 Python 启动报「端口 8080 已被占用」？**
A: 设置环境变量换端口，如 `PORT=9000 python src/script/start_local.py`。

**Q: `pnpm dev` 提示需要登录？**
A: 本地开发无需登录；只有 `pnpm deploy` 部署时才需要 `pnpm login`。

**Q: `pnpm install` 后 wrangler 报找不到 workerd / esbuild？**
A: 检查 `src/worker/pnpm-workspace.yaml` 的 `allowBuilds` 是否声明了
`esbuild` 和 `workerd`；pnpm 默认拦截第三方构建脚本，未声明则平台二进制不会下载。

**Q: 永久链接域名不对？**
A: Worker 版永久链接跟随当前访问域名；Python 版用环境变量
`PERM_DOMAIN` 指定（默认 `bva.estenova.top`）。

**Q: 部署后 /proxy 代理播放按钮不见了？**
A: 正常。公开站默认关闭代理（省流量），网页自动隐藏该按钮，
建议复制直链用下载工具获取视频。

---

仅供学习交流使用，请遵守相关法律法规与平台规则。
