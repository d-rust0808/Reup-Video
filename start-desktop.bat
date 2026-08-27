@echo off
chcp 65001 >nul
title Reup-Video Studio Desktop
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

cd frontend
call npm run electron:dev
