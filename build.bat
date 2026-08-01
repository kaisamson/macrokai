@echo off
REM Builds MacroTool.exe locally (run this ON Windows).
REM Not required if you're using the GitHub Actions workflow instead.

pip install -r requirements.txt
pip install pyinstaller

pyinstaller --onefile --noconsole --name MacroTool macro_tool.py

echo.
echo Done! Find your exe at dist\MacroTool.exe
pause
