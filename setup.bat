@echo off
chcp 65001 >nul
echo ========================================
echo   TG Forum Clone - Setup Windows
echo ========================================
echo.

cd /d "%~dp0"

if not exist "config\settings.json" (
    echo [1/3] Tao config\settings.json ...
    copy "config\settings.example.json" "config\settings.json"
) else (
    echo [1/3] config\settings.json da co
)

echo [2/3] Nang cap Telethon ...
pip install --upgrade "telethon>=1.40.0,<2.0.0"

echo [3/3] Cai dependencies ...
pip install -r requirements.txt

echo.
echo ========================================
echo   Xong! Chay: python run.py
echo   Mo trinh duyet: http://localhost:8080
echo ========================================
pause
