@echo off
rem Starts the Blackpill CAN Tool (tools\blackpill_gui.py) without a console window.
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0tools\blackpill_gui.py" & exit /b)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0tools\blackpill_gui.py" & exit /b)
echo Python 3 with tkinter is required: https://www.python.org/downloads/ (tick "tcl/tk and IDLE" and "Add to PATH").
pause
