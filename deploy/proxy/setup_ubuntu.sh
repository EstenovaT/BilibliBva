#!/usr/bin/env bash
# ============================================
#  代理服务器一键部署脚本 (Ubuntu/Debian)
#  用途：把本机（或任何机器）变成 B站解析的 HTTP/HTTPS 代理出口，
#        让解析请求从代理 IP 出站（国内 IP 可绕过 B 站 -412 风控）
#  用法：sudo bash setup_ubuntu.sh [端口] [Token]
#        示例：sudo bash setup_ubuntu.sh 12860 mytoken123
#  完成：自动复制脚本 → 注册 systemd（开机自启+崩溃重启）→ 放行 ufw
# ============================================
set -euo pipefail

PORT="${1:-12860}"
TOKEN="${2:-}"
if [ -z "$TOKEN" ]; then
    echo "用法: sudo bash setup_ubuntu.sh [端口] [Token]"
    echo "示例: sudo bash setup_ubuntu.sh 12860 mytoken123"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== B站解析代理 - 一键部署 ==="
echo "端口: $PORT    Token: $TOKEN"

# 1. 复制代理脚本到 /opt/proxy
echo "[1/5] 安装脚本到 /opt/proxy ..."
sudo mkdir -p /opt/proxy
sudo cp "$SCRIPT_DIR/proxy.py" /opt/proxy/proxy.py

# 2. 注册 systemd 服务（开机自启 + 崩溃自动重启）
echo "[2/5] 注册 systemd 服务 ..."
sudo tee /etc/systemd/system/proxy.service > /dev/null <<EOF
[Unit]
Description=Bilibili Parse HTTP Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/proxy/proxy.py $PORT
Environment=PROXY_TOKEN=$TOKEN
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# 3. 启用并启动
echo "[3/5] 启动服务 ..."
sudo systemctl daemon-reload
sudo systemctl enable --now proxy

# 4. 放行防火墙（服务器内 ufw；云厂商安全组请另行在控制台放行同端口）
echo "[4/5] 放行防火墙 ..."
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow ${PORT}/tcp >/dev/null 2>&1 || echo "  (ufw 未启用或不可用，可跳过)"
fi

# 5. 状态与使用信息
echo "[5/5] 检查状态 ..."
sleep 1
sudo systemctl status proxy --no-pager 2>/dev/null | head -6 || true

PUBLIC_IP="$(curl -fsS --connect-timeout 5 ifconfig.me 2>/dev/null || echo '你的服务器公网IP')"
echo ""
echo "==================== 部署完成 ===================="
echo "代理地址:  http://${TOKEN}:x@${PUBLIC_IP}:${PORT}"
echo "本机验证:"
echo "  curl -x http://${TOKEN}:x@${PUBLIC_IP}:${PORT} \"https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD\""
echo "  返回 code:0 即成功"
echo "解析后端启用代理:"
echo "  Windows: set BILI_HTTP_PROXY=http://${TOKEN}:x@${PUBLIC_IP}:${PORT}"
echo "  Linux:   export BILI_HTTP_PROXY=http://${TOKEN}:x@${PUBLIC_IP}:${PORT}"
echo "常用命令:"
echo "  查看状态  systemctl status proxy"
echo "  查看日志  journalctl -u proxy -f"
echo "  重启服务  sudo systemctl restart proxy"
echo "  停止服务  sudo systemctl stop proxy"
echo "⚠️ 若在云服务器上，请同时在云控制台安全组放行 TCP $PORT"
echo "=================================================="
