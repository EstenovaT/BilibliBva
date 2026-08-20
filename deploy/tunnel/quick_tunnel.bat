@echo off
rem ============================================
rem  快速体验模式（无需域名/无需绑定，10 秒出临时公网地址）
rem  用法：双击本脚本
rem  说明：用 cloudflared 快速隧道生成随机 https://xxx.trycloudflare.com
rem        地址，任何人可访问（临时，重启后地址变化）。适合先验证
rem        Tunnel+Python 方案是否可用，再走正式 setup_tunnel.ps1。
rem ============================================
cd /d "%~dp0"

where cloudflared >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 cloudflared，请先安装：
    echo         winget install cloudflare.cloudflared
    pause
    exit /b 1
)

echo [1/2] 启动 Python 后端 (127.0.0.1:8080) ...
rem 可选：配置 B 站完整会话 Cookie 降低风控：
rem set BILI_COOKIE=你的完整Cookie字符串
start "B站解析后端" cmd /k "cd /d E:\project\aiagent\biliblivBva && python src\script\start_local.py"
timeout /t 2 >nul

echo [2/2] 启动临时公网隧道（复制输出的 https://xxx.trycloudflare.com 地址）...
cloudflared tunnel --url http://localhost:8080
pause
