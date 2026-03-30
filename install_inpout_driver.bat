@echo off
echo =====================================================
echo  Instalacion driver InpOutx64 - DEFYVISION Metalconf
echo  Ejecutar como ADMINISTRADOR
echo =====================================================
echo.

:: Verificar que se esta ejecutando como admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Este script debe ejecutarse como Administrador.
    echo Click derecho -> "Ejecutar como administrador"
    pause
    exit /b 1
)

set DLL_PATH=%~dp0InpOutBinaries_1501\Win32\InstallDriver.exe

if not exist "%DLL_PATH%" (
    echo ERROR: No se encontro InstallDriver.exe en:
    echo   %DLL_PATH%
    pause
    exit /b 1
)

echo Instalando driver...
"%DLL_PATH%"

if %errorLevel% equ 0 (
    echo.
    echo Driver instalado correctamente.
    echo Ya podes correr: test_gpio.py (como Administrador)
) else (
    echo.
    echo ERROR al instalar el driver (codigo: %errorLevel%)
)

echo.
pause
