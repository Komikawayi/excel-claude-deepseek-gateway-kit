@echo off
setlocal

echo ========================================
echo   Claude Gateway - Release Build
echo ========================================

:: 检查 Rust
where cargo >nul 2>&1
if errorlevel 1 (
    echo [ERROR] cargo not found. Please install Rust: https://rustup.rs
    exit /b 1
)

:: 构建 release
echo.
echo [1/3] Building release...
PATH=C:\mingw64\mingw64\bin;%PATH%
cargo build --release
if errorlevel 1 (
    echo [ERROR] Build failed!
    exit /b 1
)

:: 创建发布目录
set VERSION=1.0.0
set DIST_DIR=dist\claude-gateway-v%VERSION%-win64
echo.
echo [2/3] Packaging to %DIST_DIR%...

if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
mkdir "%DIST_DIR%"

copy target\release\claude-gateway-rs.exe "%DIST_DIR%\claude-gateway.exe" >nul
copy .env.example "%DIST_DIR%\.env.example" >nul

:: 生成快速启动说明
(
echo # Claude Gateway v%VERSION%
echo.
echo ## 快速启动
echo.
echo 1. 复制 .env.example 为 .env
echo 2. 编辑 .env 填入你的 API Key
echo 3. 双击 claude-gateway.exe 启动
echo.
echo ## 命令行参数
echo.
echo   claude-gateway.exe --port 8790 --provider deepseek -k sk-xxx
echo.
echo ## 支持的提供商
echo.
echo - deepseek: DeepSeek API
echo - kimi: Moonshot Kimi
echo - mimo: Xiaomi MiMo
echo - minimax: MiniMax
echo - auto: 自动检测
echo.
echo ## 默认端口
echo.
echo 8790
) > "%DIST_DIR%\README.md"

:: 打包 zip
echo.
echo [3/3] Creating zip...
powershell -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath 'dist\claude-gateway-v%VERSION%-win64.zip' -Force"

:: 清理临时目录
rmdir /s /q "%DIST_DIR%"

echo.
echo ========================================
echo   Done! Output: dist\claude-gateway-v%VERSION%-win64.zip
echo ========================================
dir dist\claude-gateway-v%VERSION%-win64.zip
