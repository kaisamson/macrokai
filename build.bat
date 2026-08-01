@echo off
REM Builds MacroKai (run this ON Windows).
REM Not required if you're using the GitHub Actions workflow instead.
REM
REM Uses --onedir (a folder, not a single .exe) and --noupx, since a
REM self-extracting --onefile exe (especially one that also does global
REM keyboard/mouse hooks) is much more likely to get flagged by antivirus
REM heuristics as a dropper/keylogger.

pip install -r requirements.txt
pip install pyinstaller

pyinstaller --onedir --noupx --noconsole --name MacroKai --version-file version_info.txt macro_tool.py

echo.
echo Done! Find your app at dist\MacroKai\MacroKai.exe
pause
