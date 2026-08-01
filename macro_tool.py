"""
Minimal Macro Recorder / Player
--------------------------------
Record mouse + keyboard input, save it to a file, and play it back.

Global hotkeys (work even when the window isn't focused):
    F9   -> Start recording
    F10  -> Stop recording (you'll be asked to name & save it)
    F11  -> Play the macro currently selected in the dropdown
    F12  -> Stop playback

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
from tkinter import ttk, simpledialog, messagebox

try:
    from pynput import mouse, keyboard
    from pynput.keyboard import Key
except ImportError:
    print("Missing dependency. Please run:  pip install pynput")
    sys.exit(1)


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_DIR = os.path.join(APP_DIR, "macros")
os.makedirs(MACRO_DIR, exist_ok=True)

HOTKEYS = {
    Key.f9: "record_start",
    Key.f10: "record_stop",
    Key.f11: "play_start",
    Key.f12: "play_stop",
}

MOVE_THROTTLE = 0.02  # seconds between recorded mouse-move samples


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
        self.recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if hasattr(self, "_kb_listener_internal") and self._kb_listener_internal:
            self._kb_listener_internal.stop()
            self._kb_listener_internal = None
        return self.events

    def _t(self):
        return round(time.time() - self._start_time, 4)

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

    def play(self, events, on_done=None):
        self._stop_event.clear()
        self.playing = True

        def run():
            mouse_ctl = mouse.Controller()
            kb_ctl = keyboard.Controller()
            last_t = 0.0
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
                if self._stop_event.is_set():
                    break

                try:
                    if ev["type"] == "move":
                        mouse_ctl.position = (ev["x"], ev["y"])
                    elif ev["type"] == "click":
                        mouse_ctl.position = (ev["x"], ev["y"])
                        btn = getattr(mouse.Button, ev["button"])
                        if ev["pressed"]:
                            mouse_ctl.press(btn)
                        else:
                            mouse_ctl.release(btn)
                    elif ev["type"] == "scroll":
                        mouse_ctl.position = (ev["x"], ev["y"])
                        mouse_ctl.scroll(ev["dx"], ev["dy"])
                    elif ev["type"] == "kdown":
                        kb_ctl.press(token_to_key(ev["k"]))
                    elif ev["type"] == "kup":
                        kb_ctl.release(token_to_key(ev["k"]))
                except Exception:
                    pass  # keep playback resilient to odd events

            self.playing = False
            if on_done:
                on_done()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self.playing = False


# --------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------

def list_macro_names():
    names = [f[:-5] for f in os.listdir(MACRO_DIR) if f.endswith(".json")]
    return sorted(names, key=str.lower)


def macro_path(name):
    return os.path.join(MACRO_DIR, name + ".json")


def save_macro(name, events):
    with open(macro_path(name), "w") as f:
        json.dump(events, f)


def load_macro(name):
    with open(macro_path(name), "r") as f:
        return json.load(f)


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
        root.title("Macro")
        root.geometry("300x230")
        root.resizable(False, False)

        self.recorder = Recorder()
        self.player = Player()

        pad = {"padx": 12, "pady": 6}

        self.status_var = tk.StringVar(value="Idle")
        status = tk.Label(root, textvariable=self.status_var, font=("Segoe UI", 11, "bold"))
        status.pack(pady=(14, 4))

        hint = tk.Label(
            root,
            text="F9 Record   F10 Stop   F11 Play   F12 Stop",
            font=("Segoe UI", 8), fg="#777",
        )
        hint.pack()

        list_frame = tk.Frame(root)
        list_frame.pack(fill="x", **pad)

        self.combo = ttk.Combobox(list_frame, state="readonly")
        self.combo.pack(side="left", fill="x", expand=True)
        self.combo.bind("<Button-3>", self._on_right_click)
        self.refresh_list()

        btn_frame = tk.Frame(root)
        btn_frame.pack(fill="x", **pad)

        self.play_btn = tk.Button(btn_frame, text="Play (F11)", width=12, command=self.play_selected)
        self.play_btn.pack(side="left", padx=(0, 6))

        self.stop_btn = tk.Button(btn_frame, text="Stop (F12)", width=12, command=self.stop_playback)
        self.stop_btn.pack(side="left")

        rec_frame = tk.Frame(root)
        rec_frame.pack(fill="x", **pad)

        self.rec_btn = tk.Button(rec_frame, text="Record (F9)", width=12, command=self.start_recording)
        self.rec_btn.pack(side="left", padx=(0, 6))

        self.rec_stop_btn = tk.Button(rec_frame, text="Stop && Save (F10)", width=15, command=self.stop_recording)
        self.rec_stop_btn.pack(side="left")

        # context menu for right-click rename/delete
        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Rename", command=self._rename_selected)
        self.menu.add_command(label="Delete", command=self._delete_selected)

        # global hotkey listener (separate from recorder's own listener)
        self._hotkey_listener = keyboard.Listener(on_press=self._on_global_key)
        self._hotkey_listener.start()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- list management -------------------------------------------------

    def refresh_list(self, select=None):
        names = list_macro_names()
        self.combo["values"] = names
        if select and select in names:
            self.combo.set(select)
        elif names and not self.combo.get():
            self.combo.current(0)
        elif not names:
            self.combo.set("")

    def _on_right_click(self, event):
        if not self.combo["values"]:
            return
        self.menu.tk_popup(event.x_root, event.y_root)

    def _rename_selected(self):
        old = self.combo.get()
        if not old:
            return
        new = simpledialog.askstring("Rename", "New name:", initialvalue=old, parent=self.root)
        if not new:
            return
        new = sanitize_name(new)
        if not new or new == old:
            return
        if os.path.exists(macro_path(new)):
            messagebox.showerror("Macro", f'"{new}" already exists.')
            return
        os.rename(macro_path(old), macro_path(new))
        self.refresh_list(select=new)

    def _delete_selected(self):
        name = self.combo.get()
        if not name:
            return
        if not messagebox.askyesno("Delete macro", f'Delete "{name}"?'):
            return
        os.remove(macro_path(name))
        self.combo.set("")
        self.refresh_list()

    # ---- recording ---------------------------------------------------------

    def start_recording(self):
        if self.recorder.recording:
            return
        if self.player.playing:
            self.stop_playback()
        self.recorder.start()
        self.status_var.set("Recording...")

    def stop_recording(self):
        if not self.recorder.recording:
            return
        events = self.recorder.stop()
        self.status_var.set("Idle")
        if not events:
            messagebox.showinfo("Macro", "Nothing was recorded.")
            return
        name = simpledialog.askstring("Save macro", "Name this recording:", parent=self.root)
        if not name:
            return
        name = sanitize_name(name)
        if not name:
            return
        save_macro(name, events)
        self.refresh_list(select=name)

    # ---- playback -----------------------------------------------------------

    def play_selected(self):
        if self.recorder.recording:
            return
        name = self.combo.get()
        if not name:
            messagebox.showinfo("Macro", "No recording selected.")
            return
        if self.player.playing:
            return
        events = load_macro(name)
        self.status_var.set(f"Playing: {name}")

        def done():
            self.root.after(0, lambda: self.status_var.set("Idle"))

        self.player.play(events, on_done=done)

    def stop_playback(self):
        if self.player.playing:
            self.player.stop()
            self.status_var.set("Idle")

    # ---- global hotkeys -------------------------------------------------

    def _on_global_key(self, key):
        action = HOTKEYS.get(key)
        if not action:
            return
        # marshal back onto the tkinter main thread
        if action == "record_start":
            self.root.after(0, self.start_recording)
        elif action == "record_stop":
            self.root.after(0, self.stop_recording)
        elif action == "play_start":
            self.root.after(0, self.play_selected)
        elif action == "play_stop":
            self.root.after(0, self.stop_playback)

    def _on_close(self):
        try:
            self._hotkey_listener.stop()
        except Exception:
            pass
        if self.recorder.recording:
            self.recorder.stop()
        if self.player.playing:
            self.player.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
