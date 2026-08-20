#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简 HTTP/HTTPS 代理（Python 标准库，零依赖，支持 CONNECT）
用途：把国内服务器变成代理出口，让本机 Python 后端通过它访问
      B 站 API（国内 IP 出站，绕过 -412 风控）。

用法：
  python3 proxy.py [端口] [认证Token]
  端口默认 12860；Token 可选但强烈建议设置（防公共代理滥用）。
  设置 Token 后，客户端连接方式：
    http://Token:x@服务器IP:端口

安全提醒：不设 Token = 完全开放的代理，任何人可借你服务器中转流量，
  可能被滥用/产生流量费用/被云厂商警告。务必设置 Token。
"""
import os
import re
import socket
import sys
import threading
import urllib.parse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 12860
TOKEN = sys.argv[2] if len(sys.argv) > 2 else (os.environ.get("PROXY_TOKEN") or "")


def auth_ok(raw_headers):
    """校验 Proxy-Authorization: Basic base64("Token:x")"""
    if not TOKEN:
        return True
    for line in raw_headers:
        if line.lower().startswith(b"proxy-authorization:"):
            auth = line.split(b":", 1)[1].strip()
            try:
                user = __import__("base64").b64decode(auth.split()[1]).decode().split(":")[0]
                return user == TOKEN
            except Exception:
                return False
    return False


def relay(a, b):
    def pipe(s1, s2):
        try:
            while True:
                d = s1.recv(65536)
                if not d:
                    break
                s2.sendall(d)
        except Exception:
            pass
        finally:
            try:
                s2.shutdown(socket.SHUT_WR)
            except Exception:
                pass
    t1 = threading.Thread(target=pipe, args=(a, b), daemon=True)
    t2 = threading.Thread(target=pipe, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()


def handle(client):
    try:
        client.settimeout(30)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 65536:
                break
        if not buf:
            client.close()
            return
        lines = buf.split(b"\r\n")
        first = lines[0].decode("latin1", "replace")
        parts = first.split(" ")
        if len(parts) < 3:
            client.close()
            return
        method, target = parts[0], parts[1]

        if not auth_ok(lines[1:]):
            client.sendall(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b'Proxy-Authenticate: Basic realm="proxy"\r\n'
                b"Content-Length: 0\r\n\r\n")
            client.close()
            return

        if method == "CONNECT":
            host, _, port = target.partition(":")
            port = int(port or 443)
            try:
                up = socket.create_connection((host, port), timeout=15)
            except Exception:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                client.close()
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            relay(client, up)
        else:
            u = urllib.parse.urlsplit(target if "://" in target else "http://" + target)
            host = u.hostname or ""
            port = u.port or 80
            try:
                up = socket.create_connection((host, port), timeout=15)
            except Exception:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                client.close()
                return
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
            keep = [h for h in lines[1:] if not h.lower().startswith(b"proxy-")]
            keep.append(b"Host: " + host.encode())
            req = method + " " + path + " HTTP/1.1\r\n" + b"\r\n".join(keep) + b"\r\n\r\n"
            up.sendall(req)
            relay(client, up)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(128)
    print("proxy listening on 0.0.0.0:%d" % PORT)
    if TOKEN:
        print("auth token: %s (客户端用 http://%s:x@IP:%d)" % (TOKEN, TOKEN, PORT))
    else:
        print("WARNING: 未设置 Token，这是开放代理！建议 Ctrl+C 后用:")
        print("  python3 proxy.py %d 你的Token" % PORT)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
