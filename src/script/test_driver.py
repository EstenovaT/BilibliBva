#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地测试驱动 (test_driver.py)
================================
在本地电脑上启动解析服务（server/resolve_server.py）并自动执行
一系列接口测试，用于部署到云服务器之前验证本地环境与代码正确性。

用法:
  python test_driver.py            # 运行全部测试后自动关闭服务
  python test_driver.py --keep     # 测试后保持服务运行，便于手动体验网页

测试项:
  1. GET /                      网页首页可访问
  2. GET /api/resolve (无参数)   参数校验（400）
  3. GET /api/resolve?url=...   正常解析（信息 + mp4 直链 + 永久链接）
  4. GET /?bv=...&p=1           bot 永久链接 302 跳转
  5. GET /api/resolve (无效BV)  错误处理（404 / code=-2）
  6. GET /proxy                 代理播放默认关闭（404）
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(BASE, "..", "server")
sys.path.insert(0, SERVER_DIR)

import resolve_server  # noqa: E402

TEST_PORT = 8091
BASE_URL = "http://127.0.0.1:{0}".format(TEST_PORT)
TEST_BVID = "BV1xx411c7mD"  # 本地测试用视频号，可自行替换

results = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随 302，用于测试跳转行为"""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(NoRedirect)


def http_get(path, timeout=30):
    req = urllib.request.Request(BASE_URL + path, headers={"User-Agent": "test-driver"})
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    status = "PASS" if ok else "FAIL"
    print("[{0}] {1}{2}".format(status, name, "  ({0})".format(detail) if detail else ""))


def main():
    keep = "--keep" in sys.argv
    httpd = ThreadingHTTPServer(("127.0.0.1", TEST_PORT), resolve_server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print("== 服务已启动: {0} ==".format(BASE_URL))
    time.sleep(0.5)

    try:
        # ── 1. 首页 ──
        st, _, body = http_get("/")
        check("1. GET / 返回网页", st == 200 and "B站视频在线解析" in body,
              "HTTP {0}".format(st))

        # ── 2. 缺参数校验 ──
        st, _, body = http_get("/api/resolve")
        j = json.loads(body)
        check("2. 缺参数返回 400", st == 400 and j.get("code") == 400,
              "HTTP {0} code={1}".format(st, j.get("code")))

        # ── 3. 正常解析（默认画质 1080P）──
        path = "/api/resolve?url={0}&p=1".format(TEST_BVID)
        st, _, body = http_get(path)
        j = json.loads(body)
        d = j.get("data") or {}
        ok = (st == 200 and j.get("code") == 0
              and d.get("mp4_url", "").startswith("http")
              and "bva.estenova.top" in d.get("perm_link", ""))
        check("3. API 正常解析 (默认1080P)", ok,
              "HTTP {0} title={1!r} q={2} perm={3}".format(
                  st, d.get("title"), d.get("quality_name"), d.get("perm_link")))

        # ── 4. 永久链接 302 跳转 ──
        st, hd, _ = http_get("/?bv={0}&p=1".format(TEST_BVID))
        loc = hd.get("Location", "")
        check("4. 永久链接 302 跳转", st == 302 and "bilivideo" in loc,
              "HTTP {0} → {1}...".format(st, loc[:60]))

        # ── 5. 无效 BV 错误处理 ──
        st, _, body = http_get("/api/resolve?url=BV1invalid999")
        j = json.loads(body)
        check("5. 无效 BV 返回错误", st == 404 and j.get("code") == -2,
              "HTTP {0} code={1}".format(st, j.get("code")))

        # ── 6. 代理默认关闭 ──
        st, _, body = http_get("/proxy?url=http://upos-sz-mirror.bilivideo.com/x.mp4")
        j = json.loads(body)
        check("6. 代理播放默认关闭", st == 404 and j.get("code") == 404,
              "HTTP {0}".format(st))

        # ── 汇总 ──
        passed = sum(1 for _, ok, _ in results if ok)
        print()
        print("== 测试完成: {0}/{1} 通过 ==".format(passed, len(results)))
        if passed < len(results):
            sys.exit(1)
    finally:
        if keep:
            print("服务保持运行: {0}  (Ctrl+C 停止)".format(BASE_URL))
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        httpd.shutdown()
        print("服务已关闭")


if __name__ == "__main__":
    main()
