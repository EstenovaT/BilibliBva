#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地一键启动器 (start_local.py)
=================================
跨平台（Windows / Linux / macOS）：启动解析服务并自动打开浏览器。

功能：
  1. 检测 Python 版本（需 3.7+）
  2. 检测 8080 端口是否被占用，占用则给出明确提示
  3. 启动 server/resolve_server.py 服务
  4. 自动开启代理播放（BILI_PROXY=1），解决 1080P 防盗链
  5. 1 秒后自动打开浏览器访问 http://127.0.0.1:8080/
  6. 按 Ctrl+C 停止服务
  7. 所有输出同时写入 launch.log；出错时窗口停留不闪退

用法：
  python local/start_local.py            # 正常启动
  python local/start_local.py --check    # 仅环境自检（不启动服务）
  或双击 local/start_local.bat  /  bash local/start_local.sh
"""

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import ThreadingHTTPServer

# 本地测试默认开启代理播放（解决 B 站防盗链）
os.environ.setdefault("BILI_PROXY", "1")

BASE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(BASE, "..", "server")
sys.path.insert(0, SERVER_DIR)

import resolve_server  # noqa: E402

LOG_FILE = os.path.join(BASE, "launch.log")


def log(msg: str):
    """同时输出到控制台和 launch.log（解决窗口闪退看不到信息的问题）"""
    print(msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def pause_exit(code: int):
    """保持窗口停留，避免一闪而过看不到错误"""
    try:
        input("按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt):
        time.sleep(3)
    sys.exit(code)


def port_in_use(port: int) -> bool:
    """检查本机端口是否已被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_browser(url: str):
    """自动打开浏览器，失败时提示手动访问（不同电脑默认浏览器可能未注册）"""
    try:
        opened = webbrowser.open(url)
        if opened:
            log("已自动打开浏览器…")
        else:
            log("未能自动打开浏览器，请手动复制地址访问:")
            log("  {0}".format(url))
    except Exception as e:
        log("自动打开浏览器失败({0})，请手动复制地址访问:".format(e))
        log("  {0}".format(url))


def self_check():
    """环境自检模式：不启动服务，只诊断环境问题"""
    import json
    import urllib.request

    log("===== 环境自检 =====")
    log("Python: {0}".format(sys.version.split()[0]))
    log("当前目录: {0}".format(os.getcwd()))
    log("脚本目录: {0}".format(BASE))

    # 端口
    log("端口 8080 占用: {0}".format("是（被其他程序占用）" if port_in_use(8080) else "否"))

    # 网页文件
    log("网页文件存在: {0}".format(os.path.exists(resolve_server.INDEX_PATH)))

    # B 站 API 连通性
    try:
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            log("B站API连通: 正常 (code={0})".format(data.get("code")))
    except Exception as e:
        log("B站API连通: 失败 ({0})".format(e))
        log("  -> 请检查网络，能否访问 api.bilibili.com（关闭代理/VPN 试试）")

    log("===== 自检完成 =====")
    log("若以上全部正常，可尝试正常启动；若异常，请把本日志内容发给开发者。")


def main():
    log("=" * 52)
    log("B站视频解析服务 启动中...")

    # Python 版本检查（ThreadingHTTPServer 需要 3.7+）
    if sys.version_info < (3, 7):
        log("[ERROR] 需要 Python 3.7 或更高版本 (当前 {0}.{1})".format(
            sys.version_info[0], sys.version_info[1]))
        pause_exit(1)

    port = resolve_server.PORT
    host = "127.0.0.1"

    if port_in_use(port):
        log("[ERROR] 端口 {0} 已被其他程序占用！".format(port))
        log("        请先关闭占用该端口的程序（可能是微信开发者工具、")
        log("        Java、代理软件等），")
        log("        或设置环境变量 PORT 换一个端口后重试（如 PORT=9000）。")
        pause_exit(1)

    try:
        httpd = ThreadingHTTPServer((host, port), resolve_server.Handler)
    except OSError as e:
        log("[ERROR] 服务启动失败: {0}".format(e))
        pause_exit(1)

    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = "http://{0}:{1}/".format(host, port)
    log("-" * 52)
    log("B站视频解析服务 - 本地测试已启动")
    log("Python: {0}  端口: {1}".format(sys.version.split()[0], port))
    log("网页地址: {0}".format(url))
    log("代理播放: 已开启 (BILI_PROXY=1，解决1080P防盗链)")
    log("按 Ctrl+C 停止服务")
    log("-" * 52)

    # 延迟 1 秒等服务就绪后自动打开浏览器（失败不影响服务）
    threading.Timer(1.0, lambda: open_browser(url)).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("\n正在停止服务…")
    finally:
        httpd.shutdown()
        log("服务已停止")


if __name__ == "__main__":
    if "--check" in sys.argv:
        self_check()
        pause_exit(0)
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 兜底：任何未捕获异常都打印并停留窗口
        log("!! 发生未预期的错误，详情如下（已写入 launch.log）：")
        tb = traceback.format_exc()
        print(tb)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(tb + "\n")
        except Exception:
            pass
        pause_exit(1)
