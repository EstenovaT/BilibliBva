@echo off
rem ============================================
rem  Cloudflare Tunnel 一键启动 (Windows)
rem  前置：已运行 setup_tunnel.ps1 完成配置
rem  用法：双击本脚本，或命令行运行
rem ============================================
cd /d "%~dp0"

where cloudflared >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未找到 cloudflared，请先运行 setup_tunnel.ps1 或安装：
    echo         winget install cloudflare.cloudflared
    pause
    exit /b 1
)

echo [1/2] 启动 Python 后端 (127.0.0.1:8080) ...
rem 可选：配置 B 站完整会话 Cookie 降低风控（浏览器登录 bilibili.com 后
rem 从 F12 复制整段 Cookie），去掉下面一行注释并填入你的值：
rem set BILI_COOKIE=你的完整Cookie字符串
start "B站解析后端" cmd /k "cd /d E:\project\aiagent\biliblivBva && python src\script\start_local.py"
timeout /t 2 >nul

echo [2/2] 启动 Cloudflare Tunnel (bva) ...
echo      看到 "Registered tunnel connection" 即成功
cloudflared tunnel run bva
pause
