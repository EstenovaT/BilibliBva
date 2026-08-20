#!/usr/bin/env bash
# ============================================
#  B站解析 - 国内服务器一键部署脚本 (Linux)
#  架构：域名(Cloudflare) → Tunnel → 本机8080 Python后端 → B站API(国内IP)
#  用法：sudo bash setup_vps.sh [域名]   默认域名 bva.estenova.top
#  支持：Ubuntu/Debian(apt)、CentOS/Alibaba Cloud Linux(yum/dnf)
# ============================================
set -euo pipefail

DOMAIN="${1:-bva.estenova.top}"
APP_DIR="/opt/biliblivBva"
TUNNEL_NAME="bva"
CF_DIR="/etc/cloudflared"

echo "=== B站视频在线解析 - 国内服务器部署 ==="
echo "目标域名: $DOMAIN"
echo ""

# ── 0. 权限检查 ──
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请用 root 运行: sudo bash setup_vps.sh"
    exit 1
fi

# ── 1. 安装 cloudflared ──
if ! command -v cloudflared >/dev/null 2>&1; then
    echo "[1/6] 安装 cloudflared ..."
    if command -v apt-get >/dev/null 2>&1; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb" -o /tmp/cloudflared.deb
        dpkg -i /tmp/cloudflared.deb || apt-get -f install -y
    elif command -v dnf >/dev/null 2>&1; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.rpm" -o /tmp/cloudflared.rpm
        dnf install -y /tmp/cloudflared.rpm
    elif command -v yum >/dev/null 2>&1; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.rpm" -o /tmp/cloudflared.rpm
        yum install -y /tmp/cloudflared.rpm
    else
        echo "[ERROR] 无法识别发行版，请手动安装 cloudflared"
        exit 1
    fi
fi
echo "cloudflared: $(cloudflared --version)"

# ── 2. 获取项目代码 ──
if [ ! -f "$APP_DIR/src/server/resolve_server.py" ]; then
    echo "[2/6] 获取项目代码到 $APP_DIR ..."
    mkdir -p "$APP_DIR"
    if ! git clone "https://github.com/EstenovaT/BilibliBva.git" "$APP_DIR"; then
        echo "[ERROR] git clone 失败（国内访问 GitHub 可能慢/被拦）"
        echo "       可改用：浏览器下载仓库 ZIP 上传到服务器，解压到 $APP_DIR 后重跑本脚本"
        exit 1
    fi
fi

# ── 3. 启动 Python 后端（systemd） ──
echo "[3/6] 安装并启动后端服务 bili-resolve ..."
cp "$APP_DIR/deploy/tunnel/bili-resolve.service" /etc/systemd/system/bili-resolve.service
systemctl daemon-reload
systemctl enable --now bili-resolve
sleep 2
if curl -fsS "http://127.0.0.1:8080/" >/dev/null 2>&1; then
    echo "后端已启动: http://127.0.0.1:8080"
else
    echo "[警告] 后端未响应，请查看: journalctl -u bili-resolve -n 30"
fi

# ── 4. 登录 Cloudflare ──
echo "[4/6] 登录 Cloudflare（请在服务器上打开浏览器/复制链接授权）..."
cloudflared tunnel login || { echo "[ERROR] 登录失败"; exit 1; }

# ── 5. 创建/复用隧道 ──
echo "[5/6] 创建隧道 $TUNNEL_NAME ..."
if ! cloudflared tunnel list 2>/dev/null | grep -qw "$TUNNEL_NAME"; then
    cloudflared tunnel create "$TUNNEL_NAME" || true
fi
TID="$(ls /root/.cloudflared/*.json 2>/dev/null | head -n1 | xargs -n1 basename 2>/dev/null | sed 's/\.json$//' || true)"
if [ -z "$TID" ]; then
    echo "[ERROR] 未找到隧道凭证文件，请检查 /root/.cloudflared/ 或手动执行 cloudflared tunnel create bva"
    exit 1
fi
echo "Tunnel ID: $TID"

# ── 6. 生成 config.yml + 绑定域名 + 隧道开机自启 ──
echo "[6/6] 生成隧道配置并绑定域名 $DOMAIN ..."
mkdir -p "$CF_DIR"
cat > "$CF_DIR/config.yml" <<EOF
# 由 setup_vps.sh 自动生成
tunnel: $TID
credentials-file: /root/.cloudflared/$TID.json

ingress:
  - hostname: $DOMAIN
    service: http://localhost:8080
  # 兜底：未匹配访问一律 404
  - service: http_status:404
EOF

cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN" || echo "[警告] DNS 绑定失败：请确认域名已在 Cloudflare 托管（添加站点并改 Nameserver）"
cloudflared service install || true
systemctl enable --now cloudflared

echo ""
echo "==================== 部署完成 ===================="
echo "浏览器访问:   https://$DOMAIN/"
echo "后端日志:     journalctl -u bili-resolve -f"
echo "隧道日志:     journalctl -u cloudflared -f"
echo "可选：配置 B 站 Cookie 降风控 → 编辑 /etc/bili-resolve.env 添加："
echo "      BILI_COOKIE=你的完整Cookie字符串"
echo "      然后 systemctl restart bili-resolve"
echo "=================================================="
