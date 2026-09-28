@echo off
rem note2xopp - drop .note files, or folders holding them, onto this file.
rem Each notebook gets a .xopp beside it, ready to open in Xournal++.

rem ============================== settings ==============================
rem Path to python.exe. Leave as "python" if it's on your PATH; otherwise
rem put the full path here, e.g. C:\Python312\python.exe
set "NOTE2XOPP_PYTHON=C:\Program Files\Python312\python.exe"

rem Redo notebooks that already have a .xopp beside them: 0 = no, 1 = yes.
set "NOTE2XOPP_FORCE=0"

rem The conversion logic is in mobiscribe_to_xopp.py and note2xopp.ps1,
rem both must sit beside this file.
rem ======================================================================

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0note2xopp.ps1" %*
pause
