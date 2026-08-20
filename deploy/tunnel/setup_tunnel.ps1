# ============================================
#  B站解析 Cloudflare Tunnel 一键配置 (Windows)
#  用途：自动完成 login / create / 生成config / 绑定域名
#  用法：右键 → 使用 PowerShell 运行；或
#        powershell -ExecutionPolicy Bypass -File setup_tunnel.ps1
#  完成后运行 start_tunnel.bat 一键启动（后端+隧道）
# ============================================
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== B站视频在线解析 - Cloudflare Tunnel 一键配置 ===" -ForegroundColor Cyan
Write-Host ""

# ── 1. 检查/安装 cloudflared ──
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "[1/5] 未找到 cloudflared，尝试用 winget 安装..." -ForegroundColor Yellow
    winget install cloudflare.cloudflared --accept-package-agreements --accept-source-agreements
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] cloudflared 安装失败。请手动安装后重跑：" -ForegroundColor Red
        Write-Host "       https://github.com/cloudflare/cloudflared/releases"
        exit 1
    }
} else {
    Write-Host "[1/5] cloudflared 已安装: $(cloudflared --version)" -ForegroundColor Green
}

# ── 2. 登录 Cloudflare ──
Write-Host "[2/5] 打开浏览器授权 Cloudflare 账号（如未自动打开，点终端里的链接）..." -ForegroundColor Cyan
cloudflared tunnel login
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] 登录失败，请重试" -ForegroundColor Red; exit 1 }

# ── 3. 创建隧道 ──
Write-Host "[3/5] 创建隧道 bva ..." -ForegroundColor Cyan
$out = (cloudflared tunnel create bva 2>&1 | Out-String)
Write-Host $out
$m = [regex]::Match($out, "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
if (-not $m.Success) {
    Write-Host "[ERROR] 未能识别 Tunnel ID。请检查上方输出（可能已存在：改用 cloudflared tunnel list 查看）" -ForegroundColor Red
    exit 1
}
$tid = $m.Groups[1].Value
Write-Host "Tunnel ID: $tid" -ForegroundColor Green

# ── 4. 生成 config.yml ──
Write-Host "[4/5] 生成隧道配置 ..." -ForegroundColor Cyan
$cfDir = Join-Path $HOME ".cloudflared"
New-Item -ItemType Directory -Force -Path $cfDir | Out-Null
$credFile = Join-Path $cfDir "$tid.json"
$domain = Read-Host "请输入要绑定的域名 [默认 bva.estenova.top]"
if (-not $domain) { $domain = "bva.estenova.top" }

$cfgLines = @(
    "# 由 setup_tunnel.ps1 自动生成",
    "tunnel: $tid",
    "credentials-file: $credFile",
    "",
    "ingress:",
    "  - hostname: $domain",
    "    service: http://localhost:8080",
    "  # 兜底：未匹配访问一律 404",
    "  - service: http_status:404"
)
Set-Content -Path (Join-Path $cfDir "config.yml") -Value $cfgLines -Encoding UTF8
Write-Host "已写入: $(Join-Path $cfDir 'config.yml')" -ForegroundColor Green

# ── 5. 绑定域名 DNS ──
Write-Host "[5/5] 绑定域名 $domain 到隧道（自动添加 DNS 记录）..." -ForegroundColor Cyan
cloudflared tunnel route dns bva $domain
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] DNS 绑定失败：请确认域名已在 Cloudflare 托管（添加站点并改 Nameserver）" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "===== 配置完成！下一步 =====" -ForegroundColor Green
Write-Host "  1) 启动 Python 后端 + 隧道（一键）："
Write-Host "       双击 deploy\tunnel\start_tunnel.bat"
Write-Host "  2) 浏览器访问:  https://$domain/"
Write-Host ""
Write-Host "  （可选）配置 B 站完整会话 Cookie 降风控：编辑 start_tunnel.bat 里的"
Write-Host "       set BILI_COOKIE=... 一行"
Write-Host ""
pause
