import time
import math
import random
import threading
import tkinter as tk
from tkinter import ttk
import ctypes
from ctypes import wintypes
from PIL import Image, ImageDraw, ImageTk   # pip install pillow
from pynput import keyboard
from pynput.keyboard import Controller
from pynput.mouse import Controller as MouseController, Button

kb = Controller()
mouse = MouseController()
running = False          # True while automation is active
session_start = None     # timestamp when automation last started, used for the timer

# Windows virtual key codes, used for the Ctrl+Tab / Alt+Tab combos
VK_CONTROL = 0x11
VK_MENU = 0x12   # Alt
VK_TAB = 0x09
KEYUP = 0x0002

# ---- screen capture protection (SetWindowDisplayAffinity) ----
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001              # captured as a black box
WDA_EXCLUDEFROMCAPTURE = 0x00000011   # fully invisible to capture (Win 10 2004+)

user32 = ctypes.windll.user32
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

def protect_window(tk_window):
    # Tk's winfo_id() is an inner child window; its parent is the real top-level HWND
    child = tk_window.winfo_id()
    hwnd = user32.GetParent(child) or child
    if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)  # fallback for older Windows

# Live settings, read by the background thread and updated by the GUI
settings = {
    "key": "ctrl",          # which key to spam: "ctrl", "w", or "none"
    "cps": 1.0,              # presses per second for the main key
    "randomized": False,     # if True, vary the interval instead of a fixed one
    "tab_changer": False,    # Ctrl+Tab every 5s
    "window_changer": False, # Alt+Tab every 5s
    "auto_click": False,     # left click, uses the same cps slider as the key
    "cursor_sway": False,    # moves the cursor in circles
}

# Cursor sway tuning: it glides in a straight line to a random spot, rests, then repeats
SWAY_RANGE = 250                  # farthest it wanders from where it started, in pixels
SWAY_SPEED_RANGE = (400, 1200)    # pixels per second during a move (random each time)
SWAY_PAUSE_RANGE = (0.3, 1.2)     # seconds it rests between moves (random each time)

def new_sway_move(cur):
    # Picks a random spot (relative to where the sway started) and how long the trip takes
    target = (random.randint(-SWAY_RANGE, SWAY_RANGE), random.randint(-SWAY_RANGE, SWAY_RANGE))
    dist = math.hypot(target[0] - cur[0], target[1] - cur[1])
    duration = max(dist / random.uniform(*SWAY_SPEED_RANGE), 0.15)
    return target, duration

# Keys that aren't plain characters need to map to a pynput Key object.
# Add more here later, e.g. "shift": keyboard.Key.shift
SPECIAL_KEYS = {
    "ctrl": keyboard.Key.ctrl,
}

def resolve_key(name):
    # Returns the pynput object for special keys, otherwise the plain character
    return SPECIAL_KEYS.get(name, name)

def next_interval():
    # Base gap between presses, from the slider
    base = 1 / max(settings["cps"], 0.1)
    if settings["randomized"]:
        # Randomize +/-40% around the base interval so it's not perfectly even
        return random.uniform(base * 0.6, base * 1.4)
    return base

def send_combo(mod_vk):
    # Holds a modifier (Ctrl or Alt), taps Tab, then releases the modifier.
    ctypes.windll.user32.keybd_event(mod_vk, 0, 0, 0)        # press modifier
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(VK_TAB, 0, 0, 0)        # press Tab
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(VK_TAB, 0, KEYUP, 0)    # release Tab
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(mod_vk, 0, KEYUP, 0)    # release modifier

def worker():
    # Runs forever in the background thread, checking what to do each loop
    last_main = 0
    last_tab = 0
    last_win = 0
    wait = next_interval()   # current gap before the next main key press
    last_click = 0
    click_wait = next_interval()   # current gap before the next auto click
    sway_off = None   # how far the sway has pushed the cursor from where it started
    sway_from = (0, 0)    # where the current straight-line move started
    sway_to = (0, 0)      # where it's heading
    sway_t0 = 0           # when the current move started
    sway_dur = 1.0        # how long the current move takes
    sway_rest_until = 0   # while now < this, the cursor sits still
    while True:
        # Undo the sway offset when it's turned off or automation stops,
        # so the cursor goes back to where it was
        if sway_off is not None and not (running and settings["cursor_sway"]):
            mouse.move(-sway_off[0], -sway_off[1])
            sway_off = None

        if running:
            now = time.time()

            if settings["auto_click"] and now - last_click >= click_wait:
                mouse.click(Button.left)
                last_click = now
                click_wait = next_interval()  # same cps/randomized rules as the key

            if settings["cursor_sway"]:
                if sway_off is None:
                    sway_off = (0, 0)
                    sway_from = (0, 0)
                    sway_to, sway_dur = new_sway_move(sway_from)
                    sway_t0 = now
                    sway_rest_until = 0
                if now >= sway_rest_until:   # only move when not resting
                    t = min((now - sway_t0) / sway_dur, 1.0)
                    e = t * t * (3 - 2 * t)   # eases in and out, but the path stays a straight line
                    new_off = (round(sway_from[0] + (sway_to[0] - sway_from[0]) * e),
                               round(sway_from[1] + (sway_to[1] - sway_from[1]) * e))
                    dx, dy = new_off[0] - sway_off[0], new_off[1] - sway_off[1]
                    if dx or dy:
                        mouse.move(dx, dy)   # relative move, so you can still nudge the mouse yourself
                    sway_off = new_off
                    if t >= 1.0:
                        # Arrived: rest for a random moment, then pick the next random spot
                        sway_from = sway_to
                        sway_rest_until = now + random.uniform(*SWAY_PAUSE_RANGE)
                        sway_to, sway_dur = new_sway_move(sway_from)
                        sway_t0 = sway_rest_until

            if settings["key"] != "none" and now - last_main >= wait:
                k = resolve_key(settings["key"])
                kb.press(k)
                time.sleep(0.02)
                kb.release(k)
                last_main = now
                wait = next_interval()  # pick a new gap for next time (changes each press if randomized)

            if settings["tab_changer"] and now - last_tab >= 5:
                send_combo(VK_CONTROL)
                last_tab = now

            if settings["window_changer"] and now - last_win >= 5:
                send_combo(VK_MENU)
                last_win = now
        time.sleep(0.01)

threading.Thread(target=worker, daemon=True).start()

# ---- colors / fonts, change these to restyle the whole app ----
BG = "#15171c"
CARD = "#1d2028"
ACCENT = "#5b8cff"
ACCENT_SOFT = "#2a3550"
TEXT = "#e8eaf0"
SUBTEXT = "#8a8f9c"
GREEN = "#3ddc84"
GREEN_HOVER = "#2fc271"   # button color while mouse is over it
RED = "#ff5c5c"
RED_HOVER = "#e14b4b"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI Semibold", 12)
FONT_TIMER = ("Segoe UI Semibold", 20)   # bigger timer font

TOGGLE_OFF = "#4a4f5c"     # grey track when off
TOGGLE_ON = "#0a84ff"      # blue track when on
TOGGLE_MS = 0.18           # slide duration in seconds

def lerp_color(c1, c2, t):
    # Blends two "#rrggbb" colors; t=0 gives c1, t=1 gives c2
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))

TOGGLE_FRAMES = 30   # how many pre-drawn positions the ball can be in
_toggle_cache = []   # shared by every switch, filled the first time one is created

def build_toggle_frames(W, H, PAD):
    # Draws each frame at 4x size, then shrinks it. The shrink blends the edge pixels,
    # which is what gives smooth anti-aliased curves (tkinter's own canvas can't do this).
    S = 4
    resample = getattr(Image, "Resampling", Image).LANCZOS
    frames = []
    for i in range(TOGGLE_FRAMES):
        p = i / (TOGGLE_FRAMES - 1)
        img = Image.new("RGB", (W * S, H * S), CARD)   # background matches the card
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, W * S - 1, H * S - 1), radius=H * S // 2,
                            fill=lerp_color(TOGGLE_OFF, TOGGLE_ON, p))
        x = PAD + p * (W - H)
        size = H - 2 * PAD
        # faint shadow under the ball, then the white ball on top
        d.ellipse((x * S, (PAD + 1) * S, (x + size) * S - 1, (PAD + 1 + size) * S - 1),
                  fill=lerp_color(TOGGLE_OFF, "#000000", 0.35) if p < 0.5 else lerp_color(TOGGLE_ON, "#000000", 0.25))
        d.ellipse((x * S, PAD * S, (x + size) * S - 1, (PAD + size) * S - 1), fill="#ffffff")
        img = img.resize((W, H), resample)
        frames.append(ImageTk.PhotoImage(img))
    return frames

class ToggleSwitch(tk.Canvas):
    # iOS-style slider switch: white ball slides smoothly, track fades grey -> blue
    W, H, PAD = 46, 26, 3

    def __init__(self, parent, variable, command=None, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        if not _toggle_cache:   # build once, after the Tk window exists
            _toggle_cache.extend(build_toggle_frames(self.W, self.H, self.PAD))
        self.var = variable
        self.command = command
        self.pos = 1.0 if variable.get() else 0.0   # 0 = fully off, 1 = fully on
        self._job = None
        self.img = self.create_image(0, 0, anchor="nw", image=_toggle_cache[0])
        self.bind("<Button-1>", self.click)
        self.render()

    def render(self):
        # Picks the pre-drawn frame closest to the current ball position
        idx = round(self.pos * (TOGGLE_FRAMES - 1))
        self.itemconfig(self.img, image=_toggle_cache[idx])

    def click(self, _event=None):
        new = not self.var.get()
        self.var.set(new)
        self.animate_to(1.0 if new else 0.0)
        if self.command:
            self.command(new)

    def animate_to(self, target):
        # Time-based animation, so the speed stays the same even if frames are slow.
        # Clicking mid-slide starts from the ball's current spot, so it never jumps.
        if self._job:
            self.after_cancel(self._job)
        start, t0 = self.pos, time.time()

        def step():
            t = min((time.time() - t0) / TOGGLE_MS, 1.0)
            eased = t * t * (3 - 2 * t)   # smoothstep: slow start, fast middle, slow end
            self.pos = start + (target - start) * eased
            self.render()
            self._job = self.after(10, step) if t < 1.0 else None
        step()

# ---- smooth slider + dropdown (drawn with Pillow, same trick as the switches) ----
SS = 4   # draw 4x bigger, then shrink: that's what smooths the edges
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
SLIDER_TRACK = "#2f3648"   # empty part of the slider track
FIELD_BG = "#222733"       # dropdown button fill
FIELD_BORDER = "#343b4d"
FIELD_BORDER_HOVER = "#4b5875"
MENU_KEY = "#1d2029"       # this exact color turns see-through, which gives the menu rounded corners
MENU_BG = "#262b38"
MENU_BORDER = "#3a4155"
MENU_HOVER = "#333b4f"

class ModernSlider(tk.Canvas):
    # Thin track with a blue fill and a round knob with a grip pattern, like the reference
    W, H = 244, 28
    M = 13          # side margin = glow radius, so the knob and glow never get clipped
    KNOB_R = 10
    TRACK_H = 4

    def __init__(self, parent, from_, to, value, resolution=0.1, command=None, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.lo, self.hi, self.res = from_, to, resolution
        self.value = value
        self.command = command
        self.hover = False
        self.dragging = False
        self.photo = None
        self.img = self.create_image(0, 0, anchor="nw")
        self.bind("<Button-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.render()

    def set_hover(self, on):
        self.hover = on
        self.render()

    def render(self):
        W, H, S, M = self.W, self.H, SS, self.M
        frac = (self.value - self.lo) / (self.hi - self.lo)
        cx, cy = M + frac * (W - 2 * M), H / 2
        th = self.TRACK_H

        img = Image.new("RGB", (W * S, H * S), CARD)
        d = ImageDraw.Draw(img)
        # empty track, then the blue filled part on top of it
        d.rounded_rectangle((M * S, (cy - th / 2) * S, (W - M) * S, (cy + th / 2) * S),
                            radius=th * S / 2, fill=SLIDER_TRACK)
        if cx - M >= 1:
            d.rounded_rectangle((M * S, (cy - th / 2) * S, cx * S, (cy + th / 2) * S),
                                radius=th * S / 2, fill=TOGGLE_ON)
        # soft glow ring while hovering or dragging
        if self.hover or self.dragging:
            g = 13
            d.ellipse(((cx - g) * S, (cy - g) * S, (cx + g) * S, (cy + g) * S),
                      fill=lerp_color(CARD, TOGGLE_ON, 0.30 if self.dragging else 0.18))
        # knob
        r = self.KNOB_R
        d.ellipse(((cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S), fill=TOGGLE_ON)
        # grip dots: 2 columns x 3 rows
        for dx in (-2.4, 2.4):
            for dy in (-3.6, 0, 3.6):
                px, py, pr = (cx + dx) * S, (cy + dy) * S, 1.1 * S
                d.ellipse((px - pr, py - pr, px + pr, py + pr), fill="#ffffff")

        self.photo = ImageTk.PhotoImage(img.resize((W, H), RESAMPLE))
        self.itemconfig(self.img, image=self.photo)

    def set_from_x(self, x):
        frac = min(max((x - self.M) / (self.W - 2 * self.M), 0.0), 1.0)
        raw = self.lo + frac * (self.hi - self.lo)
        v = round(round(raw / self.res) * self.res, 4)   # snap to the step size
        v = min(max(v, self.lo), self.hi)
        changed = v != self.value
        self.value = v
        self.render()
        if changed and self.command:
            self.command(v)

    def on_press(self, e):
        self.dragging = True
        self.set_from_x(e.x)

    def on_drag(self, e):
        self.set_from_x(e.x)

    def on_release(self, e):
        self.dragging = False
        self.render()

def pretty(value):
    # "ctrl" -> "Ctrl", "w" -> "W", "none" -> "None"
    return value.title()

class ModernDropdown(tk.Canvas):
    # Rounded button that opens a floating rounded menu, like the reference
    W, H = 244, 38
    ITEM_H, MPAD = 34, 8

    def __init__(self, parent, options, variable, command=None, bg=CARD):
        super().__init__(parent, width=self.W, height=self.H, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.options, self.var, self.command = options, variable, command
        self.hover = False
        self.popup = None
        self.btn_photos = {}
        self.menu_photos = {}
        self.btn_img = self.create_image(0, 0, anchor="nw")
        self.label = self.create_text(16, self.H / 2, anchor="w", font=FONT_BOLD, fill=TEXT)
        self.bind("<Button-1>", lambda e: self.toggle_menu())
        self.bind("<Enter>", lambda e: self.set_hover(True))
        self.bind("<Leave>", lambda e: self.set_hover(False))
        self.refresh()

    def set_hover(self, on):
        self.hover = on
        self.refresh()

    def button_photo(self, state):
        if state in self.btn_photos:
            return self.btn_photos[state]
        W, H, S = self.W, self.H, SS
        border = {"normal": FIELD_BORDER, "hover": FIELD_BORDER_HOVER, "open": TOGGLE_ON}[state]
        img = Image.new("RGB", (W * S, H * S), CARD)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((S, S, W * S - S - 1, H * S - S - 1), radius=11 * S,
                            fill=FIELD_BG, outline=border, width=2 * S)
        # chevron: points down when closed, flips up when open
        cx, cy = W - 24, H / 2
        k = -1 if state == "open" else 1
        pts = [((cx - 5) * S, (cy - 2.5 * k) * S), (cx * S, (cy + 2.5 * k) * S),
               ((cx + 5) * S, (cy - 2.5 * k) * S)]
        col = TOGGLE_ON if state == "open" else "#9aa3b8"
        d.line(pts, fill=col, width=2 * S, joint="curve")
        for x, y in pts:   # round the line ends
            d.ellipse((x - S, y - S, x + S, y + S), fill=col)
        photo = ImageTk.PhotoImage(img.resize((W, H), RESAMPLE))
        self.btn_photos[state] = photo
        return photo

    def refresh(self):
        state = "open" if self.popup else ("hover" if self.hover else "normal")
        self.itemconfig(self.btn_img, image=self.button_photo(state))
        self.itemconfig(self.label, text=pretty(self.var.get()),
                        fill=TOGGLE_ON if state == "open" else TEXT)

    def menu_photo(self, hi):
        # Card background with rounded corners; hi = index of the hovered row (-1 for none)
        if hi in self.menu_photos:
            return self.menu_photos[hi]
        mw, mh, S = self.W, len(self.options) * self.ITEM_H + 2 * self.MPAD, SS
        img = Image.new("RGB", (mw * S, mh * S), MENU_KEY)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((S // 2, S // 2, mw * S - S // 2 - 1, mh * S - S // 2 - 1),
                            radius=14 * S, fill=MENU_BG, outline=MENU_BORDER, width=S)
        if hi >= 0:
            y0 = self.MPAD + hi * self.ITEM_H
            d.rounded_rectangle((6 * S, (y0 + 1) * S, (mw - 6) * S, (y0 + self.ITEM_H - 1) * S),
                                radius=9 * S, fill=MENU_HOVER)
        photo = ImageTk.PhotoImage(img.resize((mw, mh), RESAMPLE))
        self.menu_photos[hi] = photo
        return photo

    def toggle_menu(self):
        if self.popup:
            self.close_menu()
        else:
            self.open_menu()

    def open_menu(self):
        mw, mh = self.W, len(self.options) * self.ITEM_H + 2 * self.MPAD
        top = tk.Toplevel(self)
        top.overrideredirect(True)                  # no title bar or border
        top.attributes("-topmost", True)
        top.configure(bg=MENU_KEY)
        top.attributes("-transparentcolor", MENU_KEY)   # corners outside the card become see-through
        top.geometry(f"{mw}x{mh}+{self.winfo_rootx()}+{self.winfo_rooty() + self.H + 6}")
        cv = tk.Canvas(top, width=mw, height=mh, bg=MENU_KEY, highlightthickness=0, bd=0)
        cv.pack()
        self.popup, self.menu_canvas, self.menu_hover = top, cv, -1
        self.menu_img = cv.create_image(0, 0, anchor="nw", image=self.menu_photo(-1))

        current = self.var.get()
        for i, opt in enumerate(self.options):
            cy = self.MPAD + i * self.ITEM_H + self.ITEM_H / 2
            sel = opt == current
            cv.create_text(20, cy, anchor="w", text=pretty(opt), font=FONT,
                           fill=TOGGLE_ON if sel else TEXT)
            if sel:
                cv.create_text(mw - 22, cy, anchor="e", text="\u2713",
                               font=("Segoe UI Symbol", 11), fill=TOGGLE_ON)

        cv.bind("<Motion>", self.menu_motion)
        cv.bind("<Leave>", lambda e: self.set_menu_hover(-1))
        cv.bind("<Button-1>", self.menu_click)
        top.bind("<Escape>", lambda e: self.close_menu())
        self.refresh()
        top.after(30, self.grab_popup)
        top.after(50, lambda: self.hide_popup_from_capture(top))

    def hide_popup_from_capture(self, top):
        try:
            protect_window(top)   # same screen-capture protection as the main panel
        except tk.TclError:
            pass

    def grab_popup(self):
        # Grab the mouse so a click anywhere else closes the menu
        if self.popup is None:
            return
        try:
            self.popup.grab_set()
            self.popup.focus_force()
        except tk.TclError:
            pass
        self.popup.after(250, lambda: self.popup and self.popup.bind("<FocusOut>", lambda e: self.close_menu()))

    def row_at(self, y):
        idx = (y - self.MPAD) // self.ITEM_H
        return idx if 0 <= idx < len(self.options) else -1

    def menu_motion(self, e):
        self.set_menu_hover(self.row_at(e.y))

    def set_menu_hover(self, idx):
        if self.popup and idx != self.menu_hover:
            self.menu_hover = idx
            self.menu_canvas.itemconfig(self.menu_img, image=self.menu_photo(idx))

    def menu_click(self, e):
        mw, mh = self.W, len(self.options) * self.ITEM_H + 2 * self.MPAD
        if not (0 <= e.x < mw and 0 <= e.y < mh):
            self.close_menu()   # clicked outside the menu
            return
        idx = self.row_at(e.y)
        if idx >= 0:
            self.var.set(self.options[idx])
            if self.command:
                self.command(self.options[idx])
            self.close_menu()

    def close_menu(self):
        if self.popup is None:
            return
        top, self.popup = self.popup, None
        try:
            top.grab_release()
        except tk.TclError:
            pass
        top.destroy()
        self.refresh()

class Panel(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Control")
        self.geometry("320x600")   # sized to fit the timer + all the slider switches
        self.resizable(False, False)
        self.configure(bg=BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)       # start invisible, fades in below
        self.attributes("-toolwindow", True) # removes the taskbar icon on Windows
        self.after(10, self.fade_in)         # subtle fade-in animation on open
        self.after(100, lambda: protect_window(self))  # hide from screen capture once the window exists

        # ---- header row: title + status dot ----
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=20, pady=(20, 10))
        tk.Label(header, text="Automation", bg=BG, fg=TEXT, font=FONT_TITLE).pack(side="left")
        self.status_dot = tk.Label(header, text="●", bg=BG, fg=SUBTEXT, font=("Segoe UI", 10))
        self.status_dot.pack(side="right")

        # ---- card holding all the controls ----
        card = tk.Frame(self, bg=CARD)
        card.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=18, pady=18)

        # --- key dropdown ---
        tk.Label(inner, text="KEY", bg=CARD, fg=SUBTEXT, font=("Segoe UI", 8)).pack(anchor="w")
        self.key_var = tk.StringVar(value="ctrl")

        self.key_menu = ModernDropdown(inner, ["none", "ctrl", "w"], self.key_var,
                                        command=lambda v: settings.update(key=v))
        self.key_menu.pack(anchor="w", pady=(4, 16))

        # --- speed slider ---
        cps_row = tk.Frame(inner, bg=CARD)
        cps_row.pack(fill="x")
        tk.Label(cps_row, text="SPEED", bg=CARD, fg=SUBTEXT, font=("Segoe UI", 8)).pack(side="left")
        self.cps_label = tk.Label(cps_row, text="1.0 cps", bg=CARD, fg=ACCENT, font=FONT_BOLD)
        self.cps_label.pack(side="right")

        self.cps_slider = ModernSlider(inner, from_=0.2, to=10, value=1.0, resolution=0.1,
                                        command=self.on_cps_change)
        self.cps_slider.pack(anchor="w", pady=(4, 12))

        # --- checkboxes ---
        self.rand_var = tk.BooleanVar()
        self.make_toggle(inner, "Randomized CPS", self.rand_var,
                          lambda v: settings.update(randomized=v))

        self.tab_var = tk.BooleanVar()
        self.make_toggle(inner, "Tab changer · Ctrl+Tab", self.tab_var,
                          lambda v: settings.update(tab_changer=v))

        self.win_var = tk.BooleanVar()
        self.make_toggle(inner, "Window changer · Alt+Tab", self.win_var,
                          lambda v: settings.update(window_changer=v))

        self.click_var = tk.BooleanVar()
        self.make_toggle(inner, "Auto clicker", self.click_var,
                          lambda v: settings.update(auto_click=v))

        self.sway_var = tk.BooleanVar()
        self.make_toggle(inner, "Cursor sway", self.sway_var,
                          lambda v: settings.update(cursor_sway=v))

        # --- session timer ---
        self.timer_label = tk.Label(inner, text="00:00", bg=CARD, fg=ACCENT,
                                     font=FONT_TIMER)
        self.timer_label.pack(pady=(14, 0))

        # --- start/stop button ---
        self.start_btn = tk.Button(self, text="START", bg=GREEN, fg="#0d1117",
                                    font=FONT_BOLD, relief="flat", bd=0, cursor="hand2",
                                    activebackground=GREEN_HOVER, command=self.toggle)
        self.start_btn.pack(fill="x", padx=20, pady=(0, 20), ipady=10)
        # Hover effect: darker green/red while the mouse is over the button
        self.start_btn.bind("<Enter>", self.on_hover)
        self.start_btn.bind("<Leave>", self.on_leave)

        tk.Label(self, text="F6 open window   ·   F7 stop", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 8)).pack(pady=(0, 14))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_timer()  # starts the repeating timer-label update loop

    def withdraw(self):
        # Close the dropdown menu too, so it never gets left floating on screen
        menu = getattr(self, "key_menu", None)
        if menu is not None:
            menu.close_menu()
        super().withdraw()

    def fade_in(self, step=0.0):
        # Gradually raises window opacity from 0 to 0.93 for a smooth appear animation
        step += 0.1
        self.attributes("-alpha", min(step, 0.93))
        if step < 0.93:
            self.after(15, lambda: self.fade_in(step))

    def make_toggle(self, parent, text, var, on_change):
        # Builds one row: label on the left, slider switch on the right
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=6)
        label = tk.Label(row, text=text, bg=CARD, fg=TEXT, font=FONT)
        label.pack(side="left")
        switch = ToggleSwitch(row, var, command=on_change)
        switch.pack(side="right")

        # Clicking the label toggles the switch too
        label.bind("<Button-1>", switch.click)

        # Hover: text brightens to the accent color, back to normal on leave
        def enter(e):
            label.config(fg=ACCENT)
        def leave(e):
            label.config(fg=TEXT)
        label.bind("<Enter>", enter)
        label.bind("<Leave>", leave)

    def on_hover(self, event):
        self.start_btn.config(bg=RED_HOVER if running else GREEN_HOVER)

    def on_leave(self, event):
        self.start_btn.config(bg=RED if running else GREEN)

    def on_cps_change(self, v):
        val = float(v)
        settings["cps"] = val
        self.cps_label.config(text=f"{val:.1f} cps")

    def toggle(self):
        # Called when the Start/Stop button itself is clicked
        global running, session_start
        running = not running
        if running:
            session_start = time.time()   # reset timer each time it starts
            self.update_status()
            self.withdraw()               # hide the window once automation starts
        else:
            self.update_status()

    def stop(self):
        # Used by F7: stops automation and hides the window, like closing it
        global running
        running = False
        self.update_status()
        self.withdraw()

    def update_status(self):
        if running:
            self.start_btn.config(bg=RED, text="STOP  ·  RUNNING")
            self.status_dot.config(fg=GREEN)
        else:
            self.start_btn.config(bg=GREEN, text="START")
            self.status_dot.config(fg=SUBTEXT)

    def update_timer(self):
        # Runs every 500ms to refresh the mm:ss display while running.
        # Keeps showing the final time once stopped, resets only on the next Start.
        if running and session_start is not None:
            elapsed = int(time.time() - session_start)
            minutes, seconds = divmod(elapsed, 60)
            self.timer_label.config(text=f"{minutes:02d}:{seconds:02d}")
        self.after(500, self.update_timer)

    def on_close(self):
        self.stop()

panel = None  # holds the Panel instance once it's created

def toggle_panel():
    # F6: creates the panel the first time, then toggles show/hide after that
    global panel
    if panel is None or not panel.winfo_exists():
        panel = Panel(root)
    elif panel.state() == "withdrawn":
        panel.update_status()   # refresh button/dot/timer before showing
        panel.deiconify()
        panel.lift()
    else:
        panel.withdraw()

def on_press(key):
    # Global hotkey listener: F6 toggles the panel, F7 stops automation and hides it
    if key == keyboard.Key.f6:
        root.after(0, toggle_panel)
    elif key == keyboard.Key.f7:
        if panel is not None and panel.winfo_exists():
            root.after(0, panel.stop)
        else:
            global running
            running = False

listener = keyboard.Listener(on_press=on_press)
listener.start()

# Hidden root window — required by tkinter, but we never show it ourselves
root = tk.Tk()
root.withdraw()
root.mainloop()