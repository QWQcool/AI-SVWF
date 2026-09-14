@echo off
setlocal EnableExtensions
chcp 65001 >nul
title AI-SVWF (AI Short Video Workflow Studio)

rem 始终从脚本所在目录启动，避免双击时工作目录不正确。
cd /d "%~dp0"
set "VENV_DIR=%~dp0venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PREVIEW_URL=http://127.0.0.1:8000"

echo ======================================================================
echo   AI-SVWF AI带货视频工业工作流引擎 (MVP V1.2)
echo ======================================================================
echo.
echo [1/4] 检查 Python 3.10+ 环境...
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本。
    goto :failed
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [错误] Python 版本低于 3.10，无法启动本项目。
    goto :failed
)

echo.
echo [2/4] 检查项目虚拟环境与运行依赖...
if not exist "%PYTHON_EXE%" (
    echo       首次启动：正在创建 venv 虚拟环境...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [错误] venv 虚拟环境创建失败。
        goto :failed
    )
)

"%PYTHON_EXE%" -c "import fastapi, uvicorn, pydantic, dotenv, multipart, requests, PIL, imageio, imageio_ffmpeg, edge_tts, selenium; from importlib.metadata import version; assert tuple(map(int, version('python-multipart').split('.')[:3])) >= (0, 0, 30)" >nul 2>nul
if errorlevel 1 (
    echo       正在安装 requirements.txt 中的运行依赖，请稍候...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [错误] 项目依赖安装失败，请检查网络或 requirements.txt。
        goto :failed
    )
)

rem 重复双击时复用已经运行的本项目服务，不再触发端口占用错误。
powershell.exe -NoProfile -Command "try { $status=Invoke-RestMethod -Uri '%PREVIEW_URL%/api/system/status' -TimeoutSec 1; if($status.status -eq 'healthy'){ exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
    echo.
    echo [提示] AI-SVWF 已经在运行，正在打开现有预览...
    start "" "%PREVIEW_URL%"
    exit /b 0
)

echo.
echo [3/4] 启动浏览器就绪检测...
echo       服务就绪后将自动打开: %PREVIEW_URL%
echo       API 接口文档:         %PREVIEW_URL%/docs
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='%PREVIEW_URL%'; for($i=0; $i -lt 60; $i++){ try { $response=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1; if($response.StatusCode -eq 200){ Start-Process $url; break } } catch {}; Start-Sleep -Milliseconds 500 }"

echo.
echo [4/4] 正在启动 FastAPI 后端服务...
echo       请保持此窗口开启；按 Ctrl+C 可停止服务。
echo.
"%PYTHON_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
    echo.
    echo [错误] 服务启动失败。若提示端口被占用，请关闭其他 8000 端口程序后重试。
    goto :failed
)

exit /b 0

:failed
echo.
pause
exit /b 1
