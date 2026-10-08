@echo off
setlocal
set "PROJECT_ROOT=%~dp0"

curl.exe --silent --fail http://127.0.0.1:8000/health >nul 2>&1
if errorlevel 1 (
    if not exist "%PROJECT_ROOT%backend\.venv\Scripts\python.exe" (
        echo Backend environment is missing. Open a terminal in backend and install requirements first.
        pause
        exit /b 1
    )
    start "Sentinel API" /D "%PROJECT_ROOT%backend" "%PROJECT_ROOT%backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) else (
    echo Sentinel API is already running.
)

curl.exe --silent --fail http://127.0.0.1:5173/ >nul 2>&1
if errorlevel 1 (
    start "Sentinel Web" /D "%PROJECT_ROOT%frontend" cmd /k npm.cmd run dev
) else (
    echo Sentinel Web is already running.
)

echo.
echo Wait a few seconds, then open http://localhost:5173 on this PC.
echo On your phone, join the same Wi-Fi and open http://PC-IP:5173.
echo Find PC-IP by running ipconfig and checking the Wi-Fi/Ethernet IPv4 Address.
echo Keep this PC awake. Close the Sentinel API and Sentinel Web windows to stop the app.
echo Do not port-forward this development server to the public internet.
pause
