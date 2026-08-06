@echo off
REM Installs the Silicon Labs CP210x USB-to-UART VCP driver -- what the HART
REM modem's USB chip needs to show up as a COM port. Double-click, approve the
REM UAC prompt, done.
REM
REM Fetches the current signed driver straight from Silicon Labs at run time
REM (never bundled in the app or repo) so this always installs whatever is
REM current and WHQL-signed, and doesn't redistribute their files ourselves --
REM see https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers
setlocal

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "DRIVER_URL=https://www.silabs.com/documents/public/software/CP210x_Universal_Windows_Driver.zip"
set "WORKDIR=%TEMP%\cp210x_driver_%RANDOM%"
set "ZIPFILE=%WORKDIR%\driver.zip"

echo.
echo Downloading the official Silicon Labs CP210x driver...
mkdir "%WORKDIR%" >nul 2>&1
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%DRIVER_URL%' -OutFile '%ZIPFILE%' } catch { exit 1 }"
if not exist "%ZIPFILE%" (
    echo.
    echo Download failed -- check your internet connection, or install manually from:
    echo   https://www.silabs.com/software-and-tools/usb-to-uart-bridge-vcp-drivers
    pause
    exit /b 1
)

echo Extracting...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIPFILE%' -DestinationPath '%WORKDIR%\extracted' -Force"

echo Installing (WHQL-signed, from Silicon Labs' own package)...
pnputil /add-driver "%WORKDIR%\extracted\silabser.inf" /install

echo.
echo Done. Plug in the HART modem -- it should appear as a COM port in Device
echo Manager. Still not showing up? Unplug and replug it once.
pause

rmdir /s /q "%WORKDIR%" >nul 2>&1
endlocal
