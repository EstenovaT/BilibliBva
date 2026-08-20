@echo off
rem ============================================
rem  Cloudflare Tunnel 一键启动 (Windows)
rem  前置：已按 docs/Cloudflare-Tunnel部署方案.txt
rem         完成 login / create / route dns 配置
rem  用法：双击本脚本，或命令行运行
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
rem 如需携带 B 站登录 Cookie 降低风控，先取消下面一行的注释并填你的值：
rem set BILI_SESSDATA=你的SESSDATA值
start "B站解析后端" cmd /k "cd /d E:\project\aiagent\biliblivBva && python src\script\start_local.py"
timeout /t 2 >nul

echo [2/2] 启动 Cloudflare Tunnel (bva) ...
echo      看到 "Registered tunnel connection" 即成功
cloudflared tunnel run bva
pause
