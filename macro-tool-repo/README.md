# Macro Tool

A tiny, minimal desktop app for recording and replaying mouse/keyboard input.

## Download (Windows)

Grab the latest `MacroTool.exe` from the **[Releases](../../releases)** page —
no install, no Python needed. Just download and double-click.

> **Note:** Windows SmartScreen or your antivirus may flag the exe the first
> time, since it's an unsigned PyInstaller build. Click "More info" → "Run
> anyway" if you trust the source. This is normal for small unsigned tools.

## Using it

| Action | Hotkey | Button |
|---|---|---|
| Start recording | **F9** | "Record" |
| Stop recording (prompts you to name & save it) | **F10** | "Stop && Save" |
| Play the macro selected in the dropdown | **F11** | "Play" |
| Stop playback | **F12** | "Stop" |

- The hotkeys are **global** — they work even if the app window isn't
  focused, so you can record/replay actions in any other program.
- Recordings are saved as `.json` files in a `macros` folder created next to
  the exe (or script).
- The dropdown at the top always reflects what's in that folder.
- **Right-click** an item in the dropdown to **Rename** or **Delete** it.

## Running from source instead

```
pip install -r requirements.txt
python macro_tool.py
```

## Building the .exe yourself

**Automatically (recommended):** this repo has a GitHub Actions workflow
(`.github/workflows/build.yml`) that builds `MacroTool.exe` on a Windows
runner. To cut a release:

```
git tag v1.0.0
git push --tags
```

That triggers the workflow, builds the exe, and publishes it to a new
GitHub Release automatically. You can also run the workflow manually from
the **Actions** tab (it'll just leave you a downloadable build artifact
instead of a release).

**Locally on Windows:** run `build.bat`, or manually:

```
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --noconsole --name MacroTool macro_tool.py
```

The exe will be in `dist\MacroTool.exe`.

## Platform notes

- **Windows**: works out of the box.
- **macOS**: go to System Settings → Privacy & Security → Accessibility
  (and Input Monitoring), and allow your terminal / Python to control the
  computer, or the global hooks won't fire. (You'd need to build the exe
  equivalent separately on macOS — PyInstaller builds aren't cross-platform.)
- **Linux**: works under X11; Wayland has limited support for global input
  hooks depending on your desktop environment.
