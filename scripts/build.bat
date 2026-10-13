@echo off
REM Build Bootleg Catalog Manager for Windows
REM Output: dist\bootleg-catalog-manager\bootleg-catalog-manager.exe

echo Building Bootleg Catalog Manager...
.venv\Scripts\pyinstaller.exe bootleg-catalog-manager.spec --noconfirm
if %ERRORLEVEL% neq 0 (
    echo BUILD FAILED
    exit /b 1
)
echo.
echo Build complete!
echo Output: dist\bootleg-catalog-manager\bootleg-catalog-manager.exe
