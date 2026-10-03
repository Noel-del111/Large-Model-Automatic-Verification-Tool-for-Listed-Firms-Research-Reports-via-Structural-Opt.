@echo off
call "%~dp0..\..\pdfparse\run.cmd" %*
exit /b %ERRORLEVEL%
