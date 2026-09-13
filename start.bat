@echo off
chcp 65001 >nul
title AI-SVWF (AI Short Video Workflow Studio)

echo ======================================================================
echo   AI-SVWF AI带货视频工业工作流引擎 (MVP V1.0)
echo ======================================================================
echo.
echo [1/3] 检查 Python 环境...
python --version
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    pause
    exit /b
)

echo.
echo [2/3] 正在启动 FastAPI 后端服务与 Web Studio...
echo       本地访问地址: http://localhost:8000
echo       API 接口文档: http://localhost:8000/docs
echo.

start http://localhost:8000
python main.py

pause
