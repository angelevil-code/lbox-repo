@echo off
chcp 65001 >nul
if exist "%USERPROFILE%\.workbuddy-ai\ca-bundle-extended.pem" set "SSL_CERT_FILE=%USERPROFILE%\.workbuddy-ai\ca-bundle-extended.pem"
cd /d "%~dp0"

set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe" set "PY=%USERPROFILE%\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"

if not defined PY (
  echo [ERROR] python not found.
  echo Install it first:  https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo ==================================================
echo    LBOX  -  STEP 2 : Deploy to GitHub Pages
echo ==================================================
echo.

"%PY%" deploy.py %*

echo.
pause
