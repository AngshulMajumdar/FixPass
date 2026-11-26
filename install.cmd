@echo off
rem Install jfixpass (Java) on Windows. Options go to java\install.ps1, e.g. -Prefix D:\tools\jfixpass
rem cfixpass (C) needs Linux or macOS; on Windows use WSL and run install.sh there.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0java\install.ps1" %*
exit /b %ERRORLEVEL%
