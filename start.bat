@echo off
chcp 65001 >nul
title Reup-Video Studio
cd /d "%~dp0"

set "PATH=%LOCALAPPDATA%\agy\bin;%LOCALAPPDATA%\agy;%LOCALAPPDATA%\Programs\agy\bin;%LOCALAPPDATA%\Google\Antigravity;%PATH%"

if not exist "venv\Scripts\python.exe" (
    echo Chua cai dat. Hay chay install.bat truoc.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo Chua cai frontend. Hay chay install.bat truoc.
    pause
    exit /b 1
)

echo Khoi dong backend FastAPI tai http://127.0.0.1:6000
echo Dich phu de: agy CLI tren may (khong can API key)
start "Reup-Video Backend" cmd /k "cd /d "%~dp0" && set PATH=%LOCALAPPDATA%\agy\bin;%LOCALAPPDATA%\agy;%PATH% && venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 6000"

echo Khoi dong frontend Vite tai http://127.0.0.1:6001
start "Reup-Video Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo Backend:  http://127.0.0.1:6000
echo Frontend: http://127.0.0.1:6001
echo Desktop:  start-desktop.bat
echo.
pause
