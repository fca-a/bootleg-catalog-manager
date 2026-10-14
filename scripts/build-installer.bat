@echo off
REM Build the Windows installer. Requires Inno Setup 6 on PATH.
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Error: .venv not found. Run scripts\build.bat first.
    exit /b 1
)

if not exist "dist\bootleg-catalog-manager\bootleg-catalog-manager.exe" (
    echo Error: PyInstaller output not found. Run scripts\build.bat first.
    echo Expected: dist\bootleg-catalog-manager\bootleg-catalog-manager.exe
    exit /b 1
)

echo Building installer...
iscc installer.iss
if errorlevel 1 (
    echo Inno Setup compiler failed.
    exit /b 1
)

echo.
echo Installer written to installer-output\
dir /b installer-output\*.exe
