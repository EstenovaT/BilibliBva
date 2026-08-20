#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站视频解析服务 (resolve_server.py)
====================================
纯 Python 标准库实现，无第三方依赖，部署在云服务器上作为
bva.estenova.top 对应的后端程序。

路由：
  GET /                          → 返回在线解析网页 (web/index.html)
  GET /api/resolve?url=&p=&qn=   → 返回 JSON（视频信息 + mp4 直链）
  GET /?bv=xxx&p=N               → 兼容 bot 永久链接：302 跳转到 mp4 直链
  GET /proxy?url=...             → 可选视频代理播放（默认关闭，
                                    环境变量 BILI_PROXY=1 时开启）

运行：
  python3 resolve_server.py              # 默认监听 0.0.0.0:8080
  PORT=9000 python3 resolve_server.py    # 自定义端口
  BILI_PROXY=1 python3 resolve_server.py # 同时开启 /proxy 代理播放

部署方式见 项目计划表.txt 第五/六部分（systemd + Nginx + certbot）。
"""

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

# ─── 配置 ──────────────────────────────────────────────────

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
PROXY_ENABLED = os.environ.get("BILI_PROXY", "0") == "1"

# 永久解析链接域名（可配置；默认 bva.estenova.top，与 bot 插件保持一致）
PERM_DOMAIN = os.environ.get("PERM_DOMAIN", "bva.estenova.top")

# 可选：B 站登录 Cookie SESSDATA（环境变量 BILI_SESSDATA 传入）。
# 携带登录态可降低 B 站风控(-412)概率；值为浏览器登录 bilibili.com 后
# Cookies 里 SESSDATA 的值。请勿写入代码/仓库。
SESSDATA = os.environ.get("BILI_SESSDATA", "").strip()

# B 站官方 API 请求头（防盗链：必须带 bilibili 的 Referer）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
}
if SESSDATA:
    HEADERS["Cookie"] = "SESSDATA=" + SESSDATA

QUALITY_MAP = {
    120: "4K", 116: "1080P60", 112: "1080P+", 80: "1080P",
    74: "720P60", 64: "720P", 32: "480P", 16: "360P",
}
DEFAULT_QUALITY = 80  # 1080P

# /proxy 代理播放允许的 CDN 域名后缀（防止被当作开放代理滥用）
ALLOWED_PROXY_SUFFIXES = (
    ".bilibili.com", ".hdslb.com", ".akamaized.net",
    ".bilivideo.com", ".bilivideo.cn", ".mcdn.bilivideo.cn",
)

# 封面图片允许的域名后缀（B 站图片 CDN）
ALLOWED_PIC_SUFFIXES = (".hdslb.com", ".bilibili.com")

# 封面内存缓存：{url: (时间戳, 字节)}，TTL 1 天，上限 300 张
PIC_CACHE_TTL = 86400
PIC_CACHE_MAX = 300
_pic_cache: Dict[str, Tuple[float, bytes]] = {}

# 简单内存缓存：info 5 分钟 / playurl 10 分钟
CACHE_TTL = {"info": 300, "playurl": 600}
CACHE_MAX_ITEMS = 500
_cache: Dict[str, Tuple[float, Any]] = {}

log = logging.getLogger("bili-resolve")


# ─── 缓存工具 ─────────────────────────────────────────────

def cache_get(key: str):
    item = _cache.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > CACHE_TTL[key.split(":", 1)[0]]:
        _cache.pop(key, None)
        return None
    return val


def cache_set(key: str, val: Any):
    if len(_cache) >= CACHE_MAX_ITEMS:
        # 清理过期项，仍超限则直接清空（简单策略）
        now = time.time()
        expired = [k for k, (ts, _) in _cache.items()
                   if now - ts > max(CACHE_TTL.values())]
        for k in expired:
            _cache.pop(k, None)
        if len(_cache) >= CACHE_MAX_ITEMS:
            _cache.clear()
    _cache[key] = (time.time(), val)


# ─── B 站 API 工具 ────────────────────────────────────────

# 记录最近一次 API 请求的失败原因（供错误提示使用；多线程下可能互相覆盖，仅作提示用）
_last_api_error = ""


def _api_get(url: str, timeout: int = 15) -> Optional[dict]:
    """同步 GET 请求 Bilibili API，返回 data 字段；失败返回 None"""
    global _last_api_error
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                _last_api_error = ""
                return data.get("data")
            code = data.get("code")
            msg = data.get("message")
            log.warning("API返回非0: code=%s msg=%s url=%s", code, msg, url)
            if code == -412:
                _last_api_error = "B站风控拦截(-412)：当前网络/IP被B站限制，请稍后重试或更换网络"
            elif code == -404:
                _last_api_error = "视频不存在或已被删除(-404)"
            elif code == -403:
                _last_api_error = "访问被拒绝(-403)：可能需要登录或该视频不可见"
            else:
                _last_api_error = "B站API错误(code={0}): {1}".format(code, msg)
    except Exception as e:
        _last_api_error = "网络请求失败: {0}".format(e)
        log.error("API请求失败: %s url=%s", e, url)
    return None


def get_view(bvid: str = "", aid: int = 0) -> Optional[dict]:
    """获取视频基本信息（view API），支持 bvid 或 aid"""
    if bvid:
        key, url = f"info:{bvid}", (
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    elif aid:
        key, url = f"info:av{aid}", (
            f"https://api.bilibili.com/x/web-interface/view?aid={aid}")
    else:
        return None
    hit = cache_get(key)
    if hit is not None:
        return hit
    data = _api_get(url)
    if data:
        cache_set(key, data)
    return data


def get_playurl(bvid: str, cid: int, qn: int = DEFAULT_QUALITY) -> Optional[dict]:
    """获取播放直链（playurl API），fnval=1 强制 mp4 单文件"""
    key = f"playurl:{bvid}:{cid}:{qn}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    url = (
        f"https://api.bilibili.com/x/player/playurl"
        f"?bvid={bvid}&cid={cid}&qn={qn}"
        f"&fnval=1&fnver=0&fourk=0"
        f"&type=&otype=json&platform=html5&high_quality=1"
    )
    data = _api_get(url)
    if data:
        cache_set(key, data)
    return data


def extract_mp4(play_data: dict) -> Tuple[str, int, str]:
    """从 playurl 返回中提取 mp4 直链、文件大小、画质名"""
    durl = play_data.get("durl")
    if durl and len(durl) > 0:
        first = durl[0]
        quality = play_data.get("quality", 0)
        return (first.get("url", ""), int(first.get("size", 0) or 0),
                QUALITY_MAP.get(quality, f"{quality}P"))

    # 兜底：返回了 DASH，取第一个视频流
    dash = play_data.get("dash")
    if dash:
        video_list = dash.get("video") or []
        if video_list:
            v = video_list[0]
            url_str = v.get("baseUrl") or v.get("base_url", "")
            bandwidth = v.get("bandwidth", 0)
            duration = (play_data.get("timelength") or 0) / 1000
            est_size = int(bandwidth * duration / 8) if bandwidth and duration else 0
            qn = v.get("id", 0)
            return url_str, est_size, QUALITY_MAP.get(qn, f"{qn}P")
    return "", 0, ""


# ─── 解析主流程 ────────────────────────────────────────────

def fmt_num(n):
    if n is None:
        return "未知"
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def fmt_duration(seconds):
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def extract_bvid(text: str) -> Optional[str]:
    m = re.search(r"(BV\w{10})", text)
    return m.group(1) if m else None


def extract_aid(text: str) -> Optional[int]:
    m = re.search(r"(?:av|AV)(\d+)", text)
    return int(m.group(1)) if m else None


def extract_page_num(text: str) -> int:
    m = re.search(r"[?&/]p=(\d+)", text)
    return int(m.group(1)) if m else 1


def perm_link(bvid: str, page_num: int) -> str:
    """永久解析链接（域名可通过环境变量 PERM_DOMAIN 配置）"""
    return f"https://{PERM_DOMAIN}/?bv={bvid}&p={page_num}"


def resolve_video(bvid: str = "", aid: int = 0,
                  page: int = 1, qn: int = DEFAULT_QUALITY) -> Dict[str, Any]:
    """解析视频，返回可直接 JSON 序列化的结果字典"""
    info = get_view(bvid=bvid, aid=aid)
    if not info:
        reason = _last_api_error or "视频不存在或已被删除"
        return {"code": -2, "message": "视频信息获取失败：" + reason}

    bvid = info.get("bvid") or bvid
    aid = info.get("aid") or aid

    pages = info.get("pages") or []
    page_num = max(1, page)
    page_idx = page_num - 1
    cid = info.get("cid")
    page_title = ""
    if pages and page_idx < len(pages):
        cid = pages[page_idx].get("cid") or cid
        page_title = pages[page_idx].get("part", "")

    result = {
        "code": 0,
        "message": "ok",
        "data": {
            "bvid": bvid,
            "aid": aid,
            "title": info.get("title", "未知"),
            "uploader": (info.get("owner") or {}).get("name", "") or "未知",
            "tname": info.get("tname", ""),
            "pic": info.get("pic", ""),
            "desc": (info.get("desc") or "").strip(),
            "duration": fmt_duration(info.get("duration")),
            "pubdate": info.get("pubdate", 0),
            "stat": info.get("stat") or {},
            "pages": [
                {"cid": p.get("cid"), "part": p.get("part", "")}
                for p in pages
            ],
            "page": page_num,
            "page_title": page_title,
            "mp4_url": "",
            "file_size": 0,
            "quality": 0,
            "quality_name": "",
            "perm_link": perm_link(bvid, page_num),
            "bilibili_url": f"https://www.bilibili.com/video/{bvid}"
                            + (f"?p={page_num}" if page_num > 1 else ""),
            "proxy_enabled": PROXY_ENABLED,
        },
    }
    data = result["data"]

    if not cid:
        data["message_warning"] = "未获取到 cid，无法解析播放直链"
        return result

    play = get_playurl(bvid, cid, qn)
    if not play:
        data["message_warning"] = "播放直链获取失败，请稍后重试"
        return result

    mp4_url, file_size, quality_name = extract_mp4(play)
    data["mp4_url"] = mp4_url
    data["file_size"] = file_size
    data["quality"] = play.get("quality", 0)
    data["quality_name"] = quality_name
    return result


# ─── 视频代理（可选）──────────────────────────────────────

def _host_allowed(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == s.lstrip(".") or host.endswith(s)
               for s in ALLOWED_PROXY_SUFFIXES)


# ─── HTTP 服务 ────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 网页文件位置：优先用环境变量 BILI_INDEX 指定；默认按本仓库结构
# src/server/resolve_server.py → src/webui/index.html
INDEX_PATH = os.environ.get("BILI_INDEX") or os.path.join(
    BASE_DIR, "..", "webui", "index.html")

FALLBACK_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>B站在线解析</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h2>B站视频在线解析</h2>
<p>未找到 <code>web/index.html</code>，请确认网页文件已随服务一起部署。</p>
<p>API 可用：<code>/api/resolve?url=BVxxxx&amp;p=1&amp;qn=64</code></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "BiliResolve/1.0"

    # ── 公共方法 ──
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = 200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # ── GET ──
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)

            if path == "/api/resolve":
                self._handle_api(query)
            elif path == "/proxy":
                self._handle_proxy(query)
            elif path == "/pic":
                self._handle_pic(query)
            elif path == "/ex":
                # 隐藏的「完全版」入口（与 Worker 版一致）：直接返回网页
                self._handle_index()
            elif path == "/":
                # 带 bv/av/url 参数 → 兼容 bot 永久链接（302 跳转直链）
                url_param = (query.get("url") or [""])[0]
                bvid = (query.get("bv") or [""])[0] or extract_bvid(url_param)
                aid = extract_aid(url_param) if not bvid else 0
                if bvid or aid:
                    self._handle_legacy_link(bvid, aid, query)
                else:
                    self._handle_index()
            elif path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            else:
                self._send_json({"code": 404, "message": "Not Found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log.exception("请求处理异常")
            try:
                self._send_json({"code": -1, "message": f"服务器内部错误: {e}"}, 500)
            except Exception:
                pass

    # ── 路由实现 ──
    def _handle_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                html = f.read().decode("utf-8")
        except OSError:
            html = FALLBACK_HTML
        self._send_html(html)

    def _handle_api(self, query: dict):
        url_param = (query.get("url") or [""])[0]
        bvid = (query.get("bv") or [""])[0] or extract_bvid(url_param)
        try:
            aid = int((query.get("av") or ["0"])[0]) if (query.get("av") or ["0"])[0] else 0
        except ValueError:
            aid = 0
        aid = extract_aid(url_param) if (not aid and not bvid) else aid

        if not bvid and not aid:
            self._send_json({
                "code": 400,
                "message": "缺少参数：请提供 url 或 bv / av",
            }, 400)
            return

        try:
            page = int((query.get("p") or ["1"])[0])
        except ValueError:
            page = 1
        try:
            qn = int((query.get("qn") or [str(DEFAULT_QUALITY)])[0])
        except ValueError:
            qn = DEFAULT_QUALITY

        result = resolve_video(bvid=bvid, aid=aid, page=page, qn=qn)
        status = 200 if result.get("code") == 0 else 404
        self._send_json(result, status)

    def _handle_legacy_link(self, bvid: str, aid: int, query: dict):
        """兼容 bot 永久链接：/ ?bv=xxx&p=N → 302 跳转到 mp4 直链"""
        try:
            page = int((query.get("p") or ["1"])[0])
        except ValueError:
            page = 1
        try:
            qn = int((query.get("qn") or [str(DEFAULT_QUALITY)])[0])
        except ValueError:
            qn = DEFAULT_QUALITY

        result = resolve_video(bvid=bvid, aid=aid, page=page, qn=qn)
        mp4_url = (result.get("data") or {}).get("mp4_url")
        if not mp4_url:
            self._send_json({"code": -2, "message": "解析失败，无法获取播放直链"}, 404)
            return
        log.info("302 → %s", mp4_url[:120])
        self._send_redirect(mp4_url)

    def _handle_proxy(self, query: dict):
        if not PROXY_ENABLED:
            self._send_json({"code": 404, "message": "代理播放未开启（BILI_PROXY=1）"}, 404)
            return
        url = (query.get("url") or [""])[0]
        if not url or not _host_allowed(url):
            self._send_json({"code": 400, "message": "代理地址不合法"}, 400)
            return
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            upstream = urllib.request.urlopen(req, timeout=30)
            self.send_response(200)
            self.send_header("Content-Type",
                             upstream.headers.get("Content-Type", "video/mp4"))
            length = upstream.headers.get("Content-Length")
            if length:
                self.send_header("Content-Length", length)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            upstream.close()
        except Exception as e:
            log.error("代理失败: %s", e)
            try:
                self._send_json({"code": -1, "message": f"代理失败: {e}"}, 502)
            except Exception:
                pass

    def _handle_pic(self, query: dict):
        """封面代理：B 站图片 CDN 有防盗链（校验 Referer），
        浏览器直连会 403，由后端下载并转发（带 B 站 Referer）。
        使用方式：/pic?url=https://i0.hdslb.com/bfs/archive/xxx.jpg"""
        url = (query.get("url") or [""])[0]
        if not url:
            self._send_json({"code": 400, "message": "缺少 url 参数"}, 400)
            return
        if url.startswith("//"):
            url = "https:" + url
        if not _pic_host_allowed(url):
            self._send_json({"code": 400, "message": "封面地址不合法"}, 400)
            return

        # 内存缓存命中
        hit = _pic_cache.get(url)
        if hit:
            ts, data = hit
            if time.time() - ts <= PIC_CACHE_TTL:
                self._send_pic(data, url)
                return
            _pic_cache.pop(url, None)

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
        except Exception as e:
            log.error("封面下载失败: %s url=%s", e, url)
            self._send_json({"code": -1, "message": "封面获取失败"}, 502)
            return

        if len(data) < 100:  # 空/异常响应
            self._send_json({"code": -1, "message": "封面内容异常"}, 502)
            return

        # 简单缓存淘汰
        if len(_pic_cache) >= PIC_CACHE_MAX:
            now = time.time()
            expired = [k for k, (ts, _) in _pic_cache.items()
                       if now - ts > PIC_CACHE_TTL]
            for k in expired:
                _pic_cache.pop(k, None)
            if len(_pic_cache) >= PIC_CACHE_MAX:
                _pic_cache.clear()
        _pic_cache[url] = (time.time(), data)

        self._send_pic(data, url)

    def _send_pic(self, data: bytes, url: str):
        """发送图片字节，Content-Type 按扩展名推断"""
        lower = url.lower()
        if ".png" in lower:
            ctype = "image/png"
        elif ".webp" in lower:
            ctype = "image/webp"
        elif ".gif" in lower:
            ctype = "image/gif"
        else:
            ctype = "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass


def _pic_host_allowed(url: str) -> bool:
    """校验封面 URL 域名是否属于 B 站图片 CDN"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == s.lstrip(".") or host.endswith(s)
               for s in ALLOWED_PIC_SUFFIXES)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("B站解析服务已启动: http://%s:%d/  (proxy=%s)",
             HOST, PORT, "ON" if PROXY_ENABLED else "OFF")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("服务停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
