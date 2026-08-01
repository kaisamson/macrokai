@echo off
REM Builds MacroKai.exe locally (run this ON Windows).
REM Not required if you're using the GitHub Actions workflow instead.

pip install -r requirements.txt
pip install pyinstaller

pyinstaller --onefile --noconsole --name MacroKai macro_tool.py

echo.
echo Done! Find your exe at dist\MacroKai.exe
pause
