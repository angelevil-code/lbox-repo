@echo off
chcp 65001 >nul
if exist "%USERPROFILE%\.workbuddy-ai\ca-bundle-extended.pem" set "SSL_CERT_FILE=%USERPROFILE%\.workbuddy-ai\ca-bundle-extended.pem"
cd /d "%~dp0"
setlocal

set "GH="
where gh >nul 2>&1 && set "GH=gh"
if not defined GH if exist "%USERPROFILE%\.workbuddy-ai\binaries\gh\bin\gh.exe" set "GH=%USERPROFILE%\.workbuddy-ai\binaries\gh\bin\gh.exe"
if not defined GH if exist "%ProgramFiles%\GitHub CLI\gh.exe" set "GH=%ProgramFiles%\GitHub CLI\gh.exe"

if not defined GH (
  echo [ERROR] gh not found.
  echo Install it first:  winget install GitHub.cli
  echo.
  pause
  exit /b 1
)

echo ==================================================
echo    LBOX  -  STEP 1 : Login to GitHub
echo ==================================================
echo.

"%GH%" auth status >nul 2>&1
if not errorlevel 1 (
  echo Already logged in. You can run 2-deploy.bat now.
  echo.
  pause
  exit /b 0
)

echo Answer the 4 questions exactly like this:
echo.
echo   1^) account .............. GitHub.com
echo   2^) protocol ............. HTTPS
echo   3^) authenticate git ..... Yes
echo   4^) how to login ......... Login with a web browser
echo.
echo Then an 8-char code appears, like A1B2-C3D4.
echo Press Enter, paste the code in browser, click Authorize.
echo.
echo Press any key to start ...
pause >nul
echo.

"%GH%" auth login

echo.
if errorlevel 1 (
  echo [FAILED] Not logged in. Run this file again.
) else (
  echo [OK] Login done. Now run 2-deploy.bat
)
echo.
pause
