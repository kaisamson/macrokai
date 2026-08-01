# MacroKai

A tiny, minimal desktop app for recording and replaying mouse/keyboard input.

## Download (Windows)

Grab the latest `MacroKai-windows.zip` from the **[Releases](../../releases)**
page, unzip it anywhere, and run `MacroKai.exe` inside the extracted folder.
No install, no Python needed.

> **Note:** Windows SmartScreen or your antivirus may still flag it, since
> it's an unsigned build that does global keyboard/mouse hooks and injects
> synthetic clicks — the same behavior pattern as a keylogger, which is
> exactly what heuristic AV engines are watching for, even though this is
> just an automation tool. Click "More info" → "Run anyway" if you trust the
> source. The build already avoids the biggest false-positive triggers
> (a self-extracting `--onefile` bundle and UPX compression), but the only
> way to eliminate this entirely is to sign the exe with a paid code-signing
> certificate — not something this project does. If it gets flagged, you can
> submit it to Microsoft as a false positive:
> https://www.microsoft.com/en-us/wdsi/filesubmission

## Using it

| Action | Hotkey | Button |
|---|---|---|
| Play / Stop the macro selected in the dropdown (toggle) | **F1** | "Play" / "Stop" |
| Record / Stop && Save (toggle; stopping prompts you to name & save it) | **F2** | "Record" / "Stop && Save" |
| Start / Stop auto-clicking (toggle) | **F3** | "Start Auto Click" / "Stop Auto Click" |

- The hotkeys are **global** — they work even if the app window isn't
  focused, so you can record/replay actions in any other program.
- Each hotkey/button above **toggles** — pressing it again while that
  action is running stops it. Starting one action (record, play, auto
  click) automatically stops whichever of the others is running.
- While recording, a running clock shows elapsed time. While playing, a
  clock shows your position within the recording (`current / total`).
  While auto-clicking, it shows elapsed time and the click count.
- Playback **loops continuously** until you stop it: after each pass, the
  mouse is moved back to the position it was in when playback started,
  then the macro runs again.
- If you pause before hitting stop while recording, that trailing wait is
  preserved and replayed too (not just the time between your last two
  actions).
- The **Auto Click** section lets you set a click speed (in clicks per
  second) and then start/stop clicking at the current mouse position with
  F3 or its button.
- Recordings are saved as `.json` files in a `macros` folder created next to
  the exe (or script).
- The dropdown at the top always reflects what's in that folder.
- **Right-click** an item in the dropdown to **Rename** or **Delete** it.

## Running from source instead

```
pip install -r requirements.txt
python macro_tool.py
```

## Building it yourself

**Automatically (recommended):** this repo has a GitHub Actions workflow
(`.github/workflows/build.yml`) that builds MacroKai on a Windows runner.
To cut a release:

```
git tag v1.0.0
git push --tags
```

That triggers the workflow, builds it, zips it up, and publishes it to a
new GitHub Release automatically. You can also run the workflow manually
from the **Actions** tab (it'll just leave you a downloadable build
artifact instead of a release).

**Locally on Windows:** run `build.bat`, or manually:

```
pip install -r requirements.txt pyinstaller
pyinstaller --onedir --noupx --noconsole --name MacroKai --version-file version_info.txt macro_tool.py
```

The app will be in `dist\MacroKai\MacroKai.exe`.

## Platform notes

- **Windows**: works out of the box.
- **macOS**: go to System Settings → Privacy & Security → Accessibility
  (and Input Monitoring), and allow your terminal / Python to control the
  computer, or the global hooks won't fire. (You'd need to build the exe
  equivalent separately on macOS — PyInstaller builds aren't cross-platform.)
- **Linux**: works under X11; Wayland has limited support for global input
  hooks depending on your desktop environment.
