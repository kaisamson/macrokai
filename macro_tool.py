"""
MacroKai - Macro Recorder / Player
--------------------------------
Record mouse + keyboard input, save it to a file, and play it back.

Global hotkeys (work even when the window isn't focused):
    F1   -> Play / Stop toggle (plays the macro selected in the dropdown,
            looping until you press F1 again)
    F2   -> Record / Stop && Save toggle (stopping asks you to name & save it)
    F3   -> Auto Click Start / Stop toggle (clicks at the configured speed)

Recordings are stored as plain JSON files in a "macros" folder that sits
next to this script. Right-click an item in the list to rename or delete it.

Setup:
    pip install pynput
    python macro_tool.py

Note (macOS): you'll need to grant "Accessibility" and "Input Monitoring"
permissions to your terminal / Python for global hooks to work.
"""

import json
import os
import sys
import time
import threading

import tkinter as tk
from tkinter import ttk

try:
    from pynput import mouse, keyboard
    from pynput.keyboard import Key
except ImportError:
    print("Missing dependency. Please run:  pip install pynput")
    sys.exit(1)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    try:
        # Without this, Windows can silently rescale the coordinates pynput
        # sends vs. what a fullscreen game/monitor actually uses, so
        # recorded clicks can land in the wrong spot (or seem to do nothing).
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # ----------------------------------------------------------------------
    # Low-level mouse injection via SendInput.
    #
    # pynput's Windows backend clicks with the legacy mouse_event() API,
    # which a lot of fullscreen games (Roblox included) don't reliably pick
    # up. SendInput is the modern API and is what games actually listen for,
    # so we use it directly for mouse moves/clicks/scroll on Windows.
    # ----------------------------------------------------------------------

    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _INPUTUnion(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("_u",)
        _fields_ = [("type", wintypes.DWORD), ("_u", _INPUTUnion)]

    INPUT_MOUSE = 0
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_ABSOLUTE = 0x8000
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000

    _BUTTON_DOWN = {
        "left": MOUSEEVENTF_LEFTDOWN,
        "right": MOUSEEVENTF_RIGHTDOWN,
        "middle": MOUSEEVENTF_MIDDLEDOWN,
    }
    _BUTTON_UP = {
        "left": MOUSEEVENTF_LEFTUP,
        "right": MOUSEEVENTF_RIGHTUP,
        "middle": MOUSEEVENTF_MIDDLEUP,
    }

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    def _send_mouse_input(flags, dx=0, dy=0, data=0):
        mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
        inp = INPUT(type=INPUT_MOUSE, mi=mi)
        # SendInput returns the number of events it actually queued (0 on
        # failure, e.g. transient UIPI/driver contention under system load).
        # Retry a few times rather than silently dropping the click/move.
        for attempt in range(3):
            sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
            if sent == 1:
                return
            time.sleep(0.005)

    def win_move(x, y):
        gsm = ctypes.windll.user32.GetSystemMetrics
        vleft, vtop = gsm(SM_XVIRTUALSCREEN), gsm(SM_YVIRTUALSCREEN)
        vwidth, vheight = gsm(SM_CXVIRTUALSCREEN), gsm(SM_CYVIRTUALSCREEN)
        nx = int(((x - vleft) * 65536) / max(vwidth, 1))
        ny = int(((y - vtop) * 65536) / max(vheight, 1))
        _send_mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)

    def win_click(button, pressed):
        flags = _BUTTON_DOWN[button] if pressed else _BUTTON_UP[button]
        _send_mouse_input(flags)

    def win_scroll(dx, dy):
        if dy:
            _send_mouse_input(MOUSEEVENTF_WHEEL, data=int(dy * 120))
        if dx:
            _send_mouse_input(MOUSEEVENTF_HWHEEL, data=int(dx * 120))


def move_mouse(mouse_ctl, x, y):
    if IS_WINDOWS:
        win_move(x, y)
    else:
        mouse_ctl.position = (x, y)


def click_mouse(mouse_ctl, button_name, pressed):
    if IS_WINDOWS:
        win_click(button_name, pressed)
    else:
        btn = getattr(mouse.Button, button_name)
        if pressed:
            mouse_ctl.press(btn)
        else:
            mouse_ctl.release(btn)


def scroll_mouse(mouse_ctl, dx, dy):
    if IS_WINDOWS:
        win_scroll(dx, dy)
    else:
        mouse_ctl.scroll(dx, dy)


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DIR = os.path.join(APP_DIR, "macros")
os.makedirs(MACRO_DIR, exist_ok=True)

HOTKEYS = {
    Key.f1: "play_toggle",
    Key.f2: "record_toggle",
    Key.f3: "autoclick_toggle",
}

MOVE_THROTTLE = 0.02  # seconds between recorded mouse-move samples

# Minimum real time a key/button is held down during playback. Recordings
# (or a playback pass that's fallen behind and is catching up) can otherwise
# reproduce a press+release faster than a laggy target app's input poll
# interval, so the app never sees the key as "down" at all.
MIN_HOLD_S = 0.05


def _wait_min_hold(pressed_at, stop_event):
    if pressed_at is None:
        return
    remaining = MIN_HOLD_S - (time.time() - pressed_at)
    while remaining > 0 and not stop_event.is_set():
        step = min(0.02, remaining)
        time.sleep(step)
        remaining -= step


# --------------------------------------------------------------------------
# Key (de)serialization helpers
# --------------------------------------------------------------------------

def key_to_token(key):
    """Turn a pynput key object into a small JSON-safe string."""
    if hasattr(key, "char") and key.char is not None:
        return "c:" + key.char
    # Special key (Key.shift, Key.enter, ...)
    name = str(key).split(".")[-1]
    return "s:" + name


def token_to_key(token):
    kind, val = token.split(":", 1)
    if kind == "c":
        return val  # pynput accepts plain chars directly
    return getattr(Key, val)


# --------------------------------------------------------------------------
# Recorder
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self):
        self.events = []
        self.recording = False
        self._start_time = 0.0
        self._last_move_t = 0.0
        self._mouse_listener = None
        self._kb_listener = None

    def start(self):
        self.events = []
        self.recording = True
        self._start_time = time.time()
        self._last_move_t = 0.0

        self._mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._kb_listener_internal = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._kb_listener_internal.start()

    def stop(self):
        duration = time.time() - self._start_time
        self.recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if hasattr(self, "_kb_listener_internal") and self._kb_listener_internal:
            self._kb_listener_internal.stop()
            self._kb_listener_internal = None
        # Captured separately from the last event's timestamp so trailing
        # idle time (waiting before you hit stop) is preserved on playback.
        return self.events, round(duration, 4)

    def _t(self):
        return round(time.time() - self._start_time, 4)

    def elapsed(self):
        if not self.recording:
            return 0.0
        return time.time() - self._start_time

    def _on_move(self, x, y):
        if not self.recording:
            return
        now = self._t()
        if now - self._last_move_t < MOVE_THROTTLE:
            return
        self._last_move_t = now
        self.events.append({"type": "move", "t": now, "x": x, "y": y})

    def _on_click(self, x, y, button, pressed):
        if not self.recording:
            return
        self.events.append({
            "type": "click", "t": self._t(),
            "x": x, "y": y, "button": button.name, "pressed": pressed,
        })

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        self.events.append({
            "type": "scroll", "t": self._t(), "x": x, "y": y, "dx": dx, "dy": dy,
        })

    def _on_key_press(self, key):
        if key in HOTKEYS:
            return  # never record our own hotkeys
        if not self.recording:
            return
        self.events.append({"type": "kdown", "t": self._t(), "k": key_to_token(key)})

    def _on_key_release(self, key):
        if key in HOTKEYS:
            return
        if not self.recording:
            return
        self.events.append({"type": "kup", "t": self._t(), "k": key_to_token(key)})


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------

class Player:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self.playing = False
        self.current_t = 0.0
        self.total_t = 0.0

    def play(self, events, duration, on_done=None):
        self._stop_event.clear()
        self.playing = True
        self.current_t = 0.0
        # duration is the full recording length (captured when the user hit
        # stop), which may run longer than the last event's timestamp if the
        # user just waited before ending the recording.
        self.total_t = max(duration, events[-1]["t"] if events else 0.0)

        def run():
            mouse_ctl = mouse.Controller()
            kb_ctl = keyboard.Controller()
            origin = mouse_ctl.position

            while not self._stop_event.is_set():
                last_t = 0.0
                pass_start = time.time()
                # Wall-clock time each key/button was pressed, keyed by
                # identifier, so releases can be delayed to guarantee a
                # minimum hold — a press+release recorded (or replayed) faster
                # than a laggy target app's input poll interval can otherwise
                # cancel out before the app ever registers it.
                down_times = {}
                for ev in events:
                    if self._stop_event.is_set():
                        break
                    wait = ev["t"] - last_t
                    last_t = ev["t"]
                    slept = 0.0
                    while slept < wait:
                        if self._stop_event.is_set():
                            break
                        step = min(0.02, wait - slept)
                        time.sleep(step)
                        slept += step
                        # Keep the displayed clock moving even during long
                        # gaps between events, instead of only at each event.
                        self.current_t = min(time.time() - pass_start, self.total_t)
                    if self._stop_event.is_set():
                        break

                    self.current_t = ev["t"]

                    try:
                        if ev["type"] == "move":
                            move_mouse(mouse_ctl, ev["x"], ev["y"])
                        elif ev["type"] == "click":
                            move_mouse(mouse_ctl, ev["x"], ev["y"])
                            # Give the target window a moment to register the
                            # cursor move before the button event — some
                            # fullscreen games/apps drop clicks that land
                            # before the move is processed.
                            time.sleep(0.01)
                            ident = ("click", ev["button"])
                            if ev["pressed"]:
                                down_times[ident] = time.time()
                                click_mouse(mouse_ctl, ev["button"], True)
                            else:
                                _wait_min_hold(down_times.pop(ident, None), self._stop_event)
                                click_mouse(mouse_ctl, ev["button"], False)
                        elif ev["type"] == "scroll":
                            move_mouse(mouse_ctl, ev["x"], ev["y"])
                            scroll_mouse(mouse_ctl, ev["dx"], ev["dy"])
                        elif ev["type"] == "kdown":
                            down_times[("key", ev["k"])] = time.time()
                            kb_ctl.press(token_to_key(ev["k"]))
                        elif ev["type"] == "kup":
                            ident = ("key", ev["k"])
                            _wait_min_hold(down_times.pop(ident, None), self._stop_event)
                            kb_ctl.release(token_to_key(ev["k"]))
                    except Exception:
                        pass  # keep playback resilient to odd events

                if self._stop_event.is_set():
                    break

                # Wait out any trailing idle time after the last event so a
                # pause you left at the end of the recording is honored too.
                trailing = self.total_t - last_t
                slept = 0.0
                while slept < trailing:
                    if self._stop_event.is_set():
                        break
                    step = min(0.02, trailing - slept)
                    time.sleep(step)
                    slept += step
                    self.current_t = min(last_t + slept, self.total_t)
                if self._stop_event.is_set():
                    break

                # Loop again: put the mouse back where it started before
                # the next pass so every repetition begins from the same place.
                self.current_t = self.total_t
                move_mouse(mouse_ctl, *origin)
                time.sleep(0.05)

            self.playing = False
            if on_done:
                on_done()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self.playing = False


# --------------------------------------------------------------------------
# Auto Clicker
# --------------------------------------------------------------------------

class AutoClicker:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False
        self.click_count = 0
        self._start_time = 0.0

    def start(self, cps, button="left"):
        self._stop_event.clear()
        self.running = True
        self.click_count = 0
        self._start_time = time.time()
        interval = 1.0 / cps

        def run():
            mouse_ctl = mouse.Controller()
            while not self._stop_event.is_set():
                click_mouse(mouse_ctl, button, True)
                click_mouse(mouse_ctl, button, False)
                self.click_count += 1
                slept = 0.0
                while slept < interval:
                    if self._stop_event.is_set():
                        break
                    step = min(0.02, interval - slept)
                    time.sleep(step)
                    slept += step
            self.running = False

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def elapsed(self):
        if not self.running:
            return 0.0
        return time.time() - self._start_time

    def stop(self):
        self._stop_event.set()
        self.running = False


# --------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------

def list_macro_names():
    names = [f[:-5] for f in os.listdir(MACRO_DIR) if f.endswith(".json")]
    return sorted(names, key=str.lower)


def macro_path(name):
    return os.path.join(MACRO_DIR, name + ".json")


def save_macro(name, events, duration):
    with open(macro_path(name), "w") as f:
        json.dump({"duration": duration, "events": events}, f)


def load_macro(name):
    with open(macro_path(name), "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        # Older macro files were just a bare list of events with no
        # recorded trailing idle time.
        events = data
        duration = events[-1]["t"] if events else 0.0
        return events, duration
    return data["events"], data["duration"]


def sanitize_name(name):
    name = name.strip()
    for bad in '\\/:*?"<>|':
        name = name.replace(bad, "")
    return name


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("MacroKai")
        root.resizable(False, False)

        self.recorder = Recorder()
        self.player = Player()
        self.autoclicker = AutoClicker()
        self.selected_macro = tk.StringVar(value="")
        self._dropdown = None
        self._dropdown_listbox = None
        self._context_target = None

        pad = {"padx": 8, "pady": 3}

        self.status_var = tk.StringVar(value="Idle")
        status = tk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10, "bold"))
        status.pack(pady=(8, 2))

        self.clock_var = tk.StringVar(value="")
        clock = tk.Label(root, textvariable=self.clock_var, font=("Consolas", 9))
        clock.pack(pady=(0, 4))

        columns = tk.Frame(root)
        columns.pack(fill="both", expand=True)

        # ---- left column: macro recorder/player -----------------------
        macro_col = tk.Frame(columns)
        macro_col.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=4)

        tk.Label(macro_col, text="Macro", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        self.combo_btn = tk.Frame(macro_col, relief="groove", bd=1, cursor="hand2")
        self.combo_btn.pack(fill="x", pady=(2, 4))

        self.combo_label = tk.Label(self.combo_btn, textvariable=self.selected_macro, anchor="w")
        self.combo_label.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.combo_arrow = tk.Label(self.combo_btn, text="▾")
        self.combo_arrow.pack(side="right", padx=(0, 4))

        for w in (self.combo_btn, self.combo_label, self.combo_arrow):
            w.bind("<Button-1>", lambda e: self._toggle_dropdown())
            w.bind("<Button-3>", self._on_button_right_click)

        self.refresh_list()

        self.play_btn = tk.Button(macro_col, text="Play (F1)", width=16, command=self.toggle_play)
        self.play_btn.pack(fill="x", pady=(0, 3))

        self.rec_btn = tk.Button(macro_col, text="Record (F2)", width=16, command=self.toggle_record)
        self.rec_btn.pack(fill="x")

        ttk.Separator(columns, orient="vertical").pack(side="left", fill="y", pady=4)

        # ---- right column: auto click -----------------------------------
        ac_col = tk.Frame(columns)
        ac_col.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=4)

        tk.Label(ac_col, text="Auto Click", font=("Segoe UI", 9, "bold")).pack(anchor="w")

        speed_row = tk.Frame(ac_col)
        speed_row.pack(fill="x", pady=(2, 4))
        tk.Label(speed_row, text="Clicks/sec:", font=("Segoe UI", 8)).pack(side="left")
        self.cps_var = tk.StringVar(value="10")
        self.cps_entry = tk.Entry(speed_row, textvariable=self.cps_var, width=5)
        self.cps_entry.pack(side="left", padx=(4, 0))

        self.autoclick_btn = tk.Button(
            ac_col, text="Start Auto Click (F3)", width=16, command=self.toggle_autoclick
        )
        self.autoclick_btn.pack(fill="x")

        # context menu for right-click rename/delete
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Rename", command=self._rename_selected)
        self.menu.add_command(label="Delete", command=self._delete_selected)

        # clicking anywhere else in the main window closes the dropdown
        # (the dropdown and the right-click menu are separate toplevels, so
        # this never fires for clicks inside either of them)
        root.bind("<Button-1>", self._maybe_close_dropdown_on_click, add="+")

        # global hotkey listener (separate from recorder's own listener)
        self._hotkey_listener = keyboard.Listener(on_press=self._on_global_key)
        self._hotkey_listener.start()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- small modal dialogs ----------------------------------------------
    #
    # Custom instead of tkinter.messagebox/simpledialog so we control exactly
    # where they appear (always over the main window, not screen-centered)
    # and that they always render above everything else, including the
    # macro dropdown popup.

    def _center_over_root(self, win):
        self.root.update_idletasks()
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        win.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _new_dialog(self, title):
        win = tk.Toplevel(self.root)
        # Stay withdrawn until positioned — Windows applies its own default
        # placement to a transient window the moment it's first shown, which
        # otherwise clobbers our geometry() call.
        win.withdraw()
        win.title(title)
        win.resizable(False, False)
        win.transient(self.root)
        return win

    def _run_dialog(self, win):
        self._center_over_root(win)
        win.deiconify()
        win.attributes("-topmost", True)
        win.grab_set()
        win.focus_set()
        win.wait_window()

    def _show_message(self, title, message):
        win = self._new_dialog(title)
        tk.Label(win, text=message, padx=16, pady=12, wraplength=240, justify="left").pack()
        tk.Button(win, text="OK", width=8, command=win.destroy).pack(pady=(0, 12))
        win.bind("<Return>", lambda e: win.destroy())
        win.bind("<Escape>", lambda e: win.destroy())
        self._run_dialog(win)

    def _ask_yes_no(self, title, message):
        result = {"value": False}
        win = self._new_dialog(title)

        def yes():
            result["value"] = True
            win.destroy()

        tk.Label(win, text=message, padx=16, pady=12, wraplength=240, justify="left").pack()
        row = tk.Frame(win)
        row.pack(pady=(0, 12))
        tk.Button(row, text="Yes", width=8, command=yes).pack(side="left", padx=6)
        tk.Button(row, text="No", width=8, command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: yes())
        win.bind("<Escape>", lambda e: win.destroy())
        self._run_dialog(win)
        return result["value"]

    def _ask_string(self, title, prompt, initial=""):
        result = {"value": None}
        win = self._new_dialog(title)

        def ok(event=None):
            result["value"] = var.get()
            win.destroy()

        tk.Label(win, text=prompt, padx=16).pack(pady=(12, 4))
        var = tk.StringVar(value=initial)
        entry = tk.Entry(win, textvariable=var, width=24)
        entry.pack(padx=16)
        entry.select_range(0, "end")
        entry.focus_set()
        row = tk.Frame(win)
        row.pack(pady=12)
        tk.Button(row, text="OK", width=8, command=ok).pack(side="left", padx=6)
        tk.Button(row, text="Cancel", width=8, command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        self._run_dialog(win)
        return result["value"]

    # ---- list management -------------------------------------------------

    def refresh_list(self, select=None):
        names = list_macro_names()
        if select and select in names:
            self.selected_macro.set(select)
        elif names and self.selected_macro.get() not in names:
            self.selected_macro.set(names[0])
        elif not names:
            self.selected_macro.set("")

    def _toggle_dropdown(self):
        if self._dropdown is not None:
            self._close_dropdown()
        else:
            self._open_dropdown()

    def _open_dropdown(self):
        names = list_macro_names()
        if not names:
            return
        x = self.combo_btn.winfo_rootx()
        y = self.combo_btn.winfo_rooty() + self.combo_btn.winfo_height()
        width = self.combo_btn.winfo_width()
        height = min(150, 20 * len(names) + 4)

        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True)
        win.wm_geometry(f"{width}x{height}+{x}+{y}")

        lb = tk.Listbox(win, activestyle="none", exportselection=False)
        lb.pack(fill="both", expand=True)
        for n in names:
            lb.insert("end", n)
        current = self.selected_macro.get()
        if current in names:
            lb.selection_set(names.index(current))

        lb.bind("<ButtonRelease-1>", self._on_dropdown_select)
        lb.bind("<Button-3>", self._on_dropdown_right_click)
        win.bind("<Escape>", lambda e: self._close_dropdown())

        self._dropdown = win
        self._dropdown_listbox = lb
        lb.focus_set()

    def _close_dropdown(self):
        if self._dropdown is not None:
            self._dropdown.destroy()
            self._dropdown = None
            self._dropdown_listbox = None

    def _maybe_close_dropdown_on_click(self, event):
        if self._dropdown is None:
            return
        if event.widget in (self.combo_btn, self.combo_label, self.combo_arrow):
            return
        self._close_dropdown()

    def _on_dropdown_select(self, event):
        lb = self._dropdown_listbox
        if lb is None:
            return
        idx = lb.nearest(event.y)
        if idx >= 0:
            self.selected_macro.set(lb.get(idx))
        self._close_dropdown()

    def _on_dropdown_right_click(self, event):
        lb = self._dropdown_listbox
        if lb is None:
            return
        idx = lb.nearest(event.y)
        if idx < 0:
            return
        lb.selection_clear(0, "end")
        lb.selection_set(idx)
        self._context_target = lb.get(idx)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _on_button_right_click(self, event):
        name = self.selected_macro.get()
        if not name:
            return
        self._context_target = name
        self.menu.tk_popup(event.x_root, event.y_root)

    def _rename_selected(self):
        old = self._context_target
        if not old:
            return
        self._close_dropdown()
        new = self._ask_string("Rename", "New name:", initial=old)
        if not new:
            return
        new = sanitize_name(new)
        if not new or new == old:
            return
        if os.path.exists(macro_path(new)):
            self._show_message("Macro", f'"{new}" already exists.')
            return
        os.rename(macro_path(old), macro_path(new))
        if self.selected_macro.get() == old:
            self.refresh_list(select=new)
        else:
            self.refresh_list()
        self._context_target = None

    def _delete_selected(self):
        name = self._context_target
        if not name:
            return
        self._close_dropdown()
        if not self._ask_yes_no("Delete macro", f'Delete "{name}"?'):
            return
        os.remove(macro_path(name))
        if self.selected_macro.get() == name:
            self.selected_macro.set("")
        self.refresh_list()
        self._context_target = None

    # ---- recording ---------------------------------------------------------

    def toggle_record(self):
        if self.recorder.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.recorder.recording:
            return
        if self.player.playing:
            self.stop_playback()
        if self.autoclicker.running:
            self.stop_autoclick()
        self.recorder.start()
        self.status_var.set("Recording...")
        self.rec_btn.config(text="Stop && Save (F2)")
        self._tick_recording()

    def _tick_recording(self):
        if not self.recorder.recording:
            return
        self.clock_var.set(f"{self.recorder.elapsed():6.1f}s")
        self.root.after(100, self._tick_recording)

    def stop_recording(self):
        if not self.recorder.recording:
            return
        events, duration = self.recorder.stop()
        self.status_var.set("Idle")
        self.clock_var.set("")
        self.rec_btn.config(text="Record (F2)")
        if not events:
            self._show_message("Macro", "Nothing was recorded.")
            return
        name = self._ask_string("Save macro", "Name this recording:")
        if not name:
            return
        name = sanitize_name(name)
        if not name:
            return
        save_macro(name, events, duration)
        self.refresh_list(select=name)

    # ---- playback -----------------------------------------------------------

    def toggle_play(self):
        if self.player.playing:
            self.stop_playback()
        else:
            self.play_selected()

    def play_selected(self):
        if self.recorder.recording:
            return
        name = self.selected_macro.get()
        if not name:
            self._show_message("Macro", "No recording selected.")
            return
        if self.player.playing:
            return
        if self.autoclicker.running:
            self.stop_autoclick()
        events, duration = load_macro(name)
        self.status_var.set(f"Playing: {name}")
        self.play_btn.config(text="Stop (F1)")

        def done():
            self.root.after(0, lambda: (
                self.status_var.set("Idle"),
                self.clock_var.set(""),
                self.play_btn.config(text="Play (F1)"),
            ))

        self.player.play(events, duration, on_done=done)
        self._tick_playback()

    def _tick_playback(self):
        if not self.player.playing:
            return
        self.clock_var.set(f"{self.player.current_t:6.1f}s / {self.player.total_t:6.1f}s")
        self.root.after(100, self._tick_playback)

    def stop_playback(self):
        if self.player.playing:
            self.player.stop()
            self.status_var.set("Idle")
            self.clock_var.set("")
            self.play_btn.config(text="Play (F1)")

    # ---- auto click ---------------------------------------------------------

    def toggle_autoclick(self):
        if self.autoclicker.running:
            self.stop_autoclick()
        else:
            self.start_autoclick()

    def start_autoclick(self):
        if self.autoclicker.running:
            return
        if self.recorder.recording:
            return
        if self.player.playing:
            self.stop_playback()
        try:
            cps = float(self.cps_var.get())
            if cps <= 0:
                raise ValueError
        except ValueError:
            self._show_message("Auto Click", "Speed must be a positive number.")
            return
        self.autoclicker.start(cps)
        self.status_var.set("Auto-clicking...")
        self.autoclick_btn.config(text="Stop Auto Click (F3)")
        self._tick_autoclick()

    def _tick_autoclick(self):
        if not self.autoclicker.running:
            return
        self.clock_var.set(f"{self.autoclicker.elapsed():6.1f}s ({self.autoclicker.click_count} clicks)")
        self.root.after(100, self._tick_autoclick)

    def stop_autoclick(self):
        if self.autoclicker.running:
            self.autoclicker.stop()
            self.status_var.set("Idle")
            self.clock_var.set("")
            self.autoclick_btn.config(text="Start Auto Click (F3)")

    # ---- global hotkeys -------------------------------------------------

    def _on_global_key(self, key):
        action = HOTKEYS.get(key)
        if not action:
            return
        # marshal back onto the tkinter main thread
        if action == "play_toggle":
            self.root.after(0, self.toggle_play)
        elif action == "record_toggle":
            self.root.after(0, self.toggle_record)
        elif action == "autoclick_toggle":
            self.root.after(0, self.toggle_autoclick)

    def _on_close(self):
        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        if self.recorder.recording:
            self.recorder.stop()
        if self.player.playing:
            self.player.stop()
        if self.autoclicker.running:
            self.autoclicker.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
