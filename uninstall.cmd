@echo off
rem Uninstall jfixpass (Windows).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0java\uninstall.ps1" %*
exit /b %ERRORLEVEL%
