# B站解析代理服务器（deploy/proxy/）

把一台国内服务器变成 HTTP/HTTPS 代理出口，让解析请求从代理 IP 出站，
从而绕过 B 站对境外/机房 IP 的 -412 风控。

```
本机 Python 后端 (8080)
   │ BILI_HTTP_PROXY=http://Token:x@服务器IP:端口
   ▼
deploy/proxy/proxy.py（服务器上运行，纯 Python 标准库，零依赖）
   │ 转发（校验 Token，剥掉代理认证头）
   ▼
api.bilibili.com  ← 看到的是服务器 IP
```

## 文件说明

| 文件 | 用途 |
|---|---|
| `proxy.py` | 代理主程序（HTTP + HTTPS CONNECT，支持 Token 认证，本机实测通过） |
| `setup_ubuntu.sh` | **Ubuntu/Debian 一键部署**（装脚本 + systemd 开机自启 + 放行 ufw） |
| `proxy.service` | systemd 服务单元（开机自启、崩溃自动重启；手动配置用） |

## 快速开始（Ubuntu 服务器）

```bash
# 把 deploy/proxy 目录传到服务器后（或 git clone 仓库），执行：
sudo bash setup_ubuntu.sh 12860 你的Token
```

脚本自动完成：复制脚本到 `/opt/proxy` → 注册 systemd（`systemctl enable --now`）
→ 放行 ufw。**云服务器还需在云控制台安全组放行同端口 TCP。**

## 本机验证

```bash
curl -x http://你的Token:x@服务器IP:12860 "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD"
# 返回 "code":0 + 视频信息 = 成功
```

## 让解析后端走代理

```powershell
# Windows
set BILI_HTTP_PROXY=http://你的Token:x@服务器IP:12860
python src\script\start_local.py

# Linux
export BILI_HTTP_PROXY=http://你的Token:x@服务器IP:12860
```

## 管理命令

```bash
systemctl status proxy          # 状态
journalctl -u proxy -f          # 日志
sudo systemctl restart proxy    # 重启
sudo systemctl stop proxy       # 停止
```

## 安全提醒

- **务必设置 Token**：不设 = 开放代理，会被扫描器滥用（流量费 + 风险）。
  客户端连接格式 `http://Token:x@IP:端口`。
- Token 属于凭据，勿写入公开仓库；服务端用 systemd `Environment=` 注入。
- 代理只转发"本机 → B 站"的 API 请求，不影响网页本身。
