@echo off
REM ==============================================================================
REM REUP-VIDEO STUDIO - AUTOMATED INSTALLATION SCRIPT (Windows)
REM ==============================================================================

chcp 65001 >nul
title Cài Đặt Reup-Video Studio

echo ======================================================
echo 🎬 REUP-VIDEO STUDIO - BẮT ĐẦU CÀI ĐẶT (WINDOWS)
echo ======================================================

REM 1. Kiểm tra Python
echo [1/4] Kiểm tra Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Lỗi: Không tìm thấy Python. Vui lòng cài đặt Python ^>= 3.10 và tích chọn "Add Python to PATH".
    pause
    exit /b 1
)
python -c "import sys; print('  -> Đã tìm thấy: Python ' + sys.version)"

REM 2. Kiểm tra Node.js & npm
echo [2/4] Kiểm tra Node.js & npm...
node -v >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Lỗi: Không tìm thấy Node.js. Vui lòng cài đặt Node.js ^>= 18 (https://nodejs.org).
    pause
    exit /b 1
)
call npm -v >nul 2>&1
echo   -> Đã tìm thấy Node.js & npm.

REM 3. Kiểm tra FFmpeg / Google CLI (agy)
echo [3/4] Kiểm tra FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ Cảnh báo: Không tìm thấy lệnh 'ffmpeg' trong PATH.
    echo    Vui lòng cài đặt FFmpeg và thêm vào PATH hệ thống để các tính năng video hoạt động trơn tru.
) else (
    echo   -> Đã tìm thấy FFmpeg.
)

where agy >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️ Chưa thấy Google CLI `agy` trong PATH.
    echo    Cài: curl -fsSL https://antigravity.google/cli/install.sh ^| bash
    echo    hoặc thêm agy.exe vào PATH ^(%%LOCALAPPDATA%%\agy^).
) else (
    echo   -> Đã tìm thấy Google CLI agy.
)

REM 4. Khởi tạo môi trường ảo Python và cài đặt thư viện
echo [4/4] Khởi tạo Python venv và cài đặt thư viện...
if not exist "venv" (
    echo   -> Đang tạo thư mục môi trường ảo venv...
    python -m venv venv
)

echo   -> Cài đặt gói thư viện Python từ requirements.txt...
call venv\Scripts\python.exe -m pip install -U pip setuptools wheel
call venv\Scripts\pip.exe install -r requirements.txt

REM 5. Cài đặt và build frontend
echo   -> Cài đặt gói thư viện Frontend và build giao diện...
cd frontend
call npm install
call npm run build
cd ..

REM 6. Khởi tạo và làm sạch thư mục Data mới
echo   -> Đang khởi tạo thư mục dữ liệu Data mới...
call venv\Scripts\python.exe clean_data.py

REM 7. Tạo file .env nếu chưa có
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo   -> Đã tạo file .env từ template .env.example.
    )
)

echo ======================================================
echo ✅ CÀI ĐẶT HOÀN TẤT THÀNH CÔNG!
echo Để khởi chạy ứng dụng, vui lòng nhấp đúp vào: start.bat
echo ======================================================
pause
