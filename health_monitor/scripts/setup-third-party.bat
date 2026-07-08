@echo off
REM health_monitor 第三方依赖下载脚本 (Windows)
REM 下载 CivetWeb 嵌入到 third_party/

set THIRD_PARTY=%~dp0..\third_party
set CIVETWEB_DIR=%THIRD_PARTY%\civetweb

if not exist "%CIVETWEB_DIR%" mkdir "%CIVETWEB_DIR%"
if not exist "%CIVETWEB_DIR%\include" mkdir "%CIVETWEB_DIR%\include"

echo ==^> 下载 CivetWeb 1.17...

powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('https://raw.githubusercontent.com/civetweb/civetweb/master/include/civetweb.h', '%CIVETWEB_DIR%\include\civetweb.h'); Write-Host '  civetweb.h OK'"
powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('https://raw.githubusercontent.com/civetweb/civetweb/master/src/civetweb.c', '%CIVETWEB_DIR%\civetweb.c'); Write-Host '  civetweb.c OK'"

echo.
echo ==^> 下载 .inl 依赖文件...
for %%f in (md5 sha1 handle_form response sort match timer) do (
    powershell -Command "$wc = New-Object System.Net.WebClient; $wc.DownloadFile('https://raw.githubusercontent.com/civetweb/civetweb/master/src/%%f.inl', '%CIVETWEB_DIR%\%%f.inl'); Write-Host '  %%f.inl OK'"
)

echo.
echo ==^> 第三方依赖已下载，现在可以执行 cmake -B build ^&^& cmake --build build
