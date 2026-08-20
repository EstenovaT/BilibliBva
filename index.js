/**
 * B站视频在线解析 —— Cloudflare Worker（JS 版后端）
 * =====================================================
 * 与本地 Python 版（src/server/resolve_server.py）功能等价，
 * 让纯静态站点（Cloudflare Pages / Workers Static Assets）也能在线解析。
 *
 * 路由：
 *   GET /                       → 静态网页（src/webui/index.html，经 ASSETS binding）
 *   GET /api/resolve?url=&p=&qn= → JSON（视频信息 + mp4 直链 + 永久链接）
 *   GET /?bv=xxx&p=N             → 兼容 bot 永久链接：302 跳转到 mp4 直链
 *   GET /pic?url=...             → 封面图片代理（带 B 站 Referer，解决防盗链 403）
 *   GET /proxy?url=...           → 视频代理播放（默认关闭，环境变量 PROXY=1 开启）
 *
 * 本地预览：cd src/worker && npm install && npm run dev
 * 部署：npm run deploy（需先 wrangler login）
 */

const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  Referer: "https://www.bilibili.com",
  Accept: "application/json, text/plain, */*",
};

const QUALITY_MAP = {
  120: "4K", 116: "1080P60", 112: "1080P+", 80: "1080P",
  74: "720P60", 64: "720P", 32: "480P", 16: "360P",
};
const DEFAULT_QN = 80; // 默认 1080P

// /proxy 允许的 CDN 域名后缀（防止被当作开放代理滥用）
const ALLOWED_PROXY_SUFFIXES = [
  ".bilibili.com", ".hdslb.com", ".akamaized.net",
  ".bilivideo.com", ".bilivideo.cn", ".mcdn.bilivideo.cn",
];
// 封面图片允许的域名后缀（B 站图片 CDN）
const ALLOWED_PIC_SUFFIXES = [".hdslb.com", ".bilibili.com"];

// ─── 工具函数 ─────────────────────────────────────────────

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function proxyEnabled(env) {
  return (env && env.PROXY === "1");
}

/**
 * 是否处于「完全版」模式（允许代理播放）：
 *   - 全局开关 env.PROXY=1（wrangler.toml [vars]），或
 *   - 请求携带 ex=1（由 /ex 完全版网页自动附带）
 */
function isExMode(q, env) {
  return proxyEnabled(env) || (q && q.get("ex") === "1");
}

function hostAllowed(urlStr, suffixes) {
  let u;
  try { u = new URL(urlStr); } catch (e) { return false; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  const host = u.hostname.toLowerCase();
  return suffixes.some((s) => host === s.replace(/^\./, "") || host.endsWith(s));
}

function extractBvid(text) {
  const m = /(BV\w{10})/.exec(text || "");
  return m ? m[1] : "";
}

function extractAid(text) {
  const m = /(?:av|AV)(\d+)/.exec(text || "");
  return m ? parseInt(m[1], 10) : 0;
}

function fmtDuration(seconds) {
  if (!seconds) return "未知";
  const total = Math.floor(seconds);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${m}:${String(s).padStart(2, "0")}`;
}

function picContentType(urlStr) {
  const lower = urlStr.toLowerCase();
  if (lower.includes(".png")) return "image/png";
  if (lower.includes(".webp")) return "image/webp";
  if (lower.includes(".gif")) return "image/gif";
  return "image/jpeg";
}

// ─── B 站 API ─────────────────────────────────────────────

// 当前请求的 env（在 fetch 入口赋值；含 SESSDATA 等配置）
let _env = null;

// 最近一次 API 请求的失败原因（供错误提示；多请求下可能互相覆盖，仅作提示）
let _lastApiError = "";

// buvid3 cookie 预热：B 站对"无 cookie 的陌生 IP"风控严格（-412），
// 先访问 www.bilibili.com 拿到 buvid3，再携带调用 API，可显著降低被风控概率
let _buvid3 = "";
let _buvid3Ts = 0;
const BUVID_TTL = 10 * 60 * 1000; // 10 分钟

async function ensureBuvid3() {
  if (_buvid3 && Date.now() - _buvid3Ts < BUVID_TTL) return _buvid3;
  try {
    const resp = await fetch("https://www.bilibili.com/", {
      headers: { "User-Agent": HEADERS["User-Agent"] },
      redirect: "follow",
    });
    let cookies = [];
    if (typeof resp.headers.getSetCookie === "function") {
      cookies = resp.headers.getSetCookie();
    } else {
      const sc = resp.headers.get("set-cookie");
      if (sc) cookies = [sc];
    }
    for (const c of cookies) {
      const m = /buvid3=([^;,\s]+)/.exec(c);
      if (m) { _buvid3 = "buvid3=" + m[1]; break; }
    }
    _buvid3Ts = Date.now();
  } catch (e) {
    // 预热失败则继续用无 cookie 请求（保留旧值）
  }
  return _buvid3;
}

// 组装请求 Cookie：优先用整段会话 Cookie（BILI_COOKIE，最接近真实浏览器，
// 含 buvid3/buvid4/bili_ticket/SESSDATA）；否则用 buvid3（自动预热）+ SESSDATA
function buildBiliCookie() {
  const full = _env && _env.BILI_COOKIE;
  if (full) return full;
  const parts = [];
  if (_buvid3) parts.push(_buvid3);
  const sess = _env && _env.SESSDATA;
  if (sess) parts.push("SESSDATA=" + sess);
  return parts.join("; ");
}

async function biliGet(urlStr) {
  const buvid = await ensureBuvid3();
  const headers = { ...HEADERS };
  const cookie = buildBiliCookie();
  if (cookie) headers["Cookie"] = cookie;
  let resp;
  try {
    resp = await fetch(urlStr, { headers });
  } catch (e) {
    _lastApiError = `网络请求失败: ${e.message}`;
    return null;
  }
  const status = resp.status;
  let data = null;
  try {
    const raw = await resp.text();
    data = JSON.parse(raw);
  } catch (e) {
    data = null;
  }
  if (data && data.code === 0 && data.data) {
    _lastApiError = "";
    return data.data;
  }
  if (data) {
    const code = data.code;
    if (code === -412) {
      _lastApiError = "B站风控拦截(-412)：当前出口 IP 被 B 站限制" +
        (_env && _env.SESSDATA
          ? "（已配置 SESSDATA 但仍被拦：Cookie 可能无效/已过期，或该 IP 段被整体限制）"
          : "（可配置 SESSDATA 登录 Cookie 尝试绕过）");
    }
    else if (code === -404) _lastApiError = "视频不存在或已被删除(-404)";
    else if (code === -403) _lastApiError = "访问被拒绝(-403)：可能需要登录或该视频不可见";
    else _lastApiError = `B站API错误(code=${code} msg=${data.message || ""})`;
  } else {
    _lastApiError = `HTTP ${status}，响应非 JSON`;
  }
  return null;
}

function extractMp4(play) {
  const durl = play && play.durl;
  if (Array.isArray(durl) && durl.length > 0) {
    const first = durl[0];
    return {
      url: first.url || "",
      size: first.size || 0,
      qname: QUALITY_MAP[play.quality] || `${play.quality}P`,
    };
  }
  // 兜底：返回了 DASH，取第一个视频流
  const dash = play && play.dash;
  if (dash && Array.isArray(dash.video) && dash.video.length > 0) {
    const v = dash.video[0];
    const urlStr = v.baseUrl || v.base_url || "";
    const bandwidth = v.bandwidth || 0;
    const duration = (play.timelength || 0) / 1000;
    const estSize = bandwidth && duration ? Math.floor((bandwidth * duration) / 8) : 0;
    return { url: urlStr, size: estSize, qname: QUALITY_MAP[v.id] || `${v.id}P` };
  }
  return { url: "", size: 0, qname: "" };
}

async function resolveVideo(bvid, aid, page, qn, origin, proxyOn) {
  let info = null;
  if (bvid) {
    info = await biliGet(`https://api.bilibili.com/x/web-interface/view?bvid=${bvid}`);
  } else if (aid) {
    info = await biliGet(`https://api.bilibili.com/x/web-interface/view?aid=${aid}`);
  }
  if (!info) {
    const reason = _lastApiError || "未知原因";
    return {
      code: -2,
      message: `视频信息获取失败（${reason}）`,
    };
  }

  const realBvid = info.bvid || bvid;
  const realAid = info.aid || aid;
  const pages = info.pages || [];
  const pageNum = Math.max(1, page || 1);
  const idx = pageNum - 1;
  let cid = info.cid;
  let pageTitle = "";
  if (pages.length > 0 && idx < pages.length) {
    cid = pages[idx].cid || cid;
    pageTitle = pages[idx].part || "";
  }

  const result = {
    code: 0,
    message: "ok",
    data: {
      bvid: realBvid,
      aid: realAid,
      title: info.title || "未知",
      uploader: (info.owner && info.owner.name) || "未知",
      tname: info.tname || "",
      pic: info.pic || "",
      desc: (info.desc || "").trim(),
      duration: fmtDuration(info.duration),
      pubdate: info.pubdate || 0,
      stat: info.stat || {},
      pages: pages.map((p) => ({ cid: p.cid, part: p.part || "" })),
      page: pageNum,
      page_title: pageTitle,
      mp4_url: "",
      file_size: 0,
      quality: 0,
      quality_name: "",
      // 永久解析链接：跟随当前域名（绑定 bva.estenova.top 后即为正式链接）
      perm_link: `${origin}/?bv=${realBvid}&p=${pageNum}`,
      bilibili_url: `https://www.bilibili.com/video/${realBvid}` +
        (pageNum > 1 ? `?p=${pageNum}` : ""),
      proxy_enabled: proxyOn,
    },
  };
  const data = result.data;

  if (!cid) {
    data.message_warning = "未获取到 cid，无法解析播放直链";
    return result;
  }

  const play = await biliGet(
    `https://api.bilibili.com/x/player/playurl?bvid=${realBvid}&cid=${cid}` +
    `&qn=${qn}&fnval=1&fnver=0&fourk=0&type=&otype=json&platform=html5&high_quality=1`
  );
  if (!play) {
    data.message_warning = "播放直链获取失败，请稍后重试";
    return result;
  }

  const mp4 = extractMp4(play);
  data.mp4_url = mp4.url;
  data.file_size = mp4.size;
  data.quality = play.quality || 0;
  data.quality_name = mp4.qname;
  return result;
}

// ─── 静态资源 ─────────────────────────────────────────────

const FALLBACK_HTML = `<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>B站在线解析</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h2>B站视频在线解析</h2>
<p>静态资源未找到（ASSETS binding 未生效）。</p>
<p>API 可用：<code>/api/resolve?url=BVxxxx&amp;p=1&amp;qn=64</code></p>
</body></html>`;

async function serveAsset(request, env) {
  if (env && env.ASSETS) return env.ASSETS.fetch(request);
  return new Response(FALLBACK_HTML, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

// ─── 各路由处理 ───────────────────────────────────────────

async function handleApi(request, q, env) {
  const urlParam = q.get("url") || "";
  let bvid = q.get("bv") || extractBvid(urlParam);
  let aid = 0;
  const avParam = q.get("av") || "";
  if (avParam) aid = parseInt(avParam, 10) || 0;
  if (!aid && !bvid) aid = extractAid(urlParam);
  if (!bvid && !aid) {
    return json({ code: 400, message: "缺少参数：请提供 url 或 bv / av" }, 400);
  }

  let page = parseInt(q.get("p") || "1", 10);
  if (!page || page < 1) page = 1;
  let qn = parseInt(q.get("qn") || String(DEFAULT_QN), 10);
  if (!qn || qn < 1) qn = DEFAULT_QN;

  const origin = new URL(request.url).origin;
  const result = await resolveVideo(bvid, aid, page, qn, origin, isExMode(q, env));
  return json(result, result.code === 0 ? 200 : 404);
}

async function handleLegacyLink(request, bvid, aid, q, env) {
  let page = parseInt(q.get("p") || "1", 10);
  if (!page || page < 1) page = 1;
  let qn = parseInt(q.get("qn") || String(DEFAULT_QN), 10);
  if (!qn || qn < 1) qn = DEFAULT_QN;

  const origin = new URL(request.url).origin;
  const result = await resolveVideo(bvid, aid, page, qn, origin, proxyEnabled(env));
  const mp4 = result.data && result.data.mp4_url;
  if (!mp4) {
    return json({ code: -2, message: "解析失败，无法获取播放直链" }, 404);
  }
  return Response.redirect(mp4, 302);
}

async function handlePic(request, q) {
  let urlStr = q.get("url") || "";
  if (!urlStr) return json({ code: 400, message: "缺少 url 参数" }, 400);
  if (urlStr.startsWith("//")) urlStr = "https:" + urlStr;
  if (!hostAllowed(urlStr, ALLOWED_PIC_SUFFIXES)) {
    return json({ code: 400, message: "封面地址不合法" }, 400);
  }
  try {
    await ensureBuvid3();
    const cookie = buildBiliCookie();
    const headers = { ...HEADERS };
    if (cookie) headers["Cookie"] = cookie;
    const upstream = await fetch(urlStr, { headers });
    if (!upstream.ok) return json({ code: -1, message: "封面获取失败" }, 502);
    const body = await upstream.arrayBuffer();
    if (body.byteLength < 100) return json({ code: -1, message: "封面内容异常" }, 502);
    return new Response(body, {
      headers: {
        "Content-Type": picContentType(urlStr),
        "Cache-Control": "public, max-age=86400",
      },
    });
  } catch (e) {
    return json({ code: -1, message: "封面获取失败" }, 502);
  }
}

async function handleProxy(request, q, env) {
  // 代理播放门控：PROXY=1 全局开启，或 /ex 完全版请求（携带 ex=1）
  if (!isExMode(q, env)) {
    return json({ code: 404, message: "代理播放未开启（/ex 完全版或 PROXY=1 可开启）" }, 404);
  }
  const urlStr = q.get("url") || "";
  if (!urlStr || !hostAllowed(urlStr, ALLOWED_PROXY_SUFFIXES)) {
    return json({ code: 400, message: "代理地址不合法" }, 400);
  }
  try {
    const headers = { ...HEADERS };
    const range = request.headers.get("Range");
    if (range) headers["Range"] = range;
    const upstream = await fetch(urlStr, { headers });
    const respHeaders = new Headers();
    respHeaders.set("Content-Type", upstream.headers.get("Content-Type") || "video/mp4");
    if (upstream.headers.get("Content-Length")) {
      respHeaders.set("Content-Length", upstream.headers.get("Content-Length"));
    }
    respHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: respHeaders });
  } catch (e) {
    return json({ code: -1, message: `代理失败: ${e.message}` }, 502);
  }
}

// ─── 入口 ─────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    _env = env;
    const url = new URL(request.url);
    const path = url.pathname;
    const q = url.searchParams;

    try {
      if (path === "/api/resolve") return await handleApi(request, q, env);
      if (path === "/proxy") return await handleProxy(request, q, env);
      if (path === "/pic") return await handlePic(request, q);
      if (path === "/" || path === "/ex") {
        // /ex：隐藏的「完全版」入口 —— 返回同一网页，前端检测到路径为 /ex
        // 后自动在 API/代理请求中附带 ex=1，从而开启代理播放
        const urlParam = q.get("url") || "";
        const bvid = q.get("bv") || extractBvid(urlParam);
        const aid = bvid ? 0 : (extractAid(urlParam) || parseInt(q.get("av") || "0", 10) || 0);
        if (bvid || aid) return await handleLegacyLink(request, bvid, aid, q, env);
        if (path === "/ex") {
          // 用 "/" 路径取 index.html，浏览器地址栏仍保持 /ex
          return serveAsset(new Request(new URL("/", request.url), request), env);
        }
        return serveAsset(request, env);
      }
      // favicon / 其他静态资源
      return serveAsset(request, env);
    } catch (e) {
      return json({ code: -1, message: `服务器内部错误: ${e.message}` }, 500);
    }
  },
};
