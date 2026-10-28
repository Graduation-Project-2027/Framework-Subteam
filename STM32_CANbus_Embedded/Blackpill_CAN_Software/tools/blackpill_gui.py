#!/usr/bin/env python3
"""
Blackpill CAN Tool: build and flash the AMR body controller (STM32F411CEU6 Blackpill + MCP2515).

- CAN bitrate and MCP2515 crystal (written to platformio.ini), with the resulting bit timing
- Build, Clean, Upload over SWD (ST-Link) or USB DFU (optionally rebooting the board into DFU first)
- ST-Link and DFU device checks with hints for the usual driver and wiring problems
- Serial terminal (USB CDC) with command history, auto-reconnect and a live controller status panel

Requirements: Python 3.8+ with tkinter and PlatformIO Core (installed with the VS Code PlatformIO
extension). The terminal uses pyserial from PlatformIO's own Python; dfu-util and OpenOCD come with
PlatformIO's STM32 platform.

Run:  python tools/blackpill_gui.py      or double-click "Blackpill Tool.bat" in the project folder.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

ROOT       = Path(__file__).resolve().parent.parent
PIO_INI    = ROOT / "platformio.ini"
CONFIG_H   = ROOT / "include" / "config.h"
ENV        = "blackpill"
ENV_DFU    = "blackpill_dfu"
FIRMWARE   = ROOT / ".pio" / "build" / ENV / "firmware.bin"
PREFS_FILE = ROOT / ".pio" / "blackpill_tool.json"
PIO_HOME   = Path(os.environ.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio")))
IS_WIN     = os.name == "nt"
NO_WINDOW  = 0x08000000 if IS_WIN else 0     # subprocess.CREATE_NO_WINDOW

APP_VIDPID = "0483:5740"                     # USB CDC of the firmware
DFU_VIDPID = "0483:df11"                     # STM32 system bootloader
FLASH_ADDR = "0x08000000"

BITRATES = [1000000, 500000, 250000, 125000]
CRYSTALS = [8000000, 16000000, 20000000]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Palette (matches the mission control GUI: ink, warm neutrals, one signal red for danger)
INK, MUTED, PAPER, PANEL, LINE = "#1d1d1b", "#6b6a64", "#f4f3ef", "#ffffff", "#dcdad2"
RED, GREEN, AMBER = "#d6001c", "#1f7a3a", "#a86400"
TERM_BG, TERM_FG = "#141517", "#d7d7d2"


# ----------------------------------------------------------------------------------------------------------------------
# CAN bit timing (mirror of MCP2515::computeTiming in lib/MCP2515/src/MCP2515.cpp)
# ----------------------------------------------------------------------------------------------------------------------
LAYOUTS = [(16, 4, 6, 5, 4), (10, 2, 4, 3, 2), (8, 1, 3, 3, 2)]   # tq, prop, ps1, ps2, sjw


def can_timing(crystal: int, bitrate: int):
    if bitrate <= 0 or crystal % (2 * bitrate):
        return None
    ratio = crystal // (2 * bitrate)
    for tq, prop, ps1, ps2, sjw in LAYOUTS:
        if ratio % tq == 0 and ratio // tq - 1 <= 63:
            return {"tq": tq, "brp": ratio // tq - 1, "sample": 100.0 * (1 + prop + ps1) / tq, "sjw": sjw}
    return None


def read_can_settings():
    text = PIO_INI.read_text(encoding="utf-8")
    b = re.search(r"-D\s*CAN_BITRATE=(\d+)", text)
    c = re.search(r"-D\s*MCP2515_CRYSTAL_HZ=(\d+)", text)
    return (int(b.group(1)) if b else 1000000), (int(c.group(1)) if c else 16000000)


def write_can_settings(bitrate: int, crystal: int) -> bool:
    """Rewrites the two -D lines in platformio.ini. Returns True if the file changed."""
    text = PIO_INI.read_text(encoding="utf-8")
    new = re.sub(r"(-D\s*CAN_BITRATE=)\d+", rf"\g<1>{bitrate}", text)
    new = re.sub(r"(-D\s*MCP2515_CRYSTAL_HZ=)\d+", rf"\g<1>{crystal}", new)
    if new == text:
        return False
    PIO_INI.write_text(new, encoding="utf-8", newline="")
    return True


# ----------------------------------------------------------------------------------------------------------------------
# tools
# ----------------------------------------------------------------------------------------------------------------------
def find_pio():
    for p in (PIO_HOME / "penv" / "Scripts" / "pio.exe", PIO_HOME / "penv" / "bin" / "pio"):
        if p.exists():
            return str(p)
    return shutil.which("pio") or shutil.which("platformio")


def find_penv_python():
    for p in (PIO_HOME / "penv" / "Scripts" / "python.exe", PIO_HOME / "penv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return None


def find_openocd():
    base = PIO_HOME / "packages" / "tool-openocd"
    exe = base / "bin" / ("openocd.exe" if IS_WIN else "openocd")
    if exe.exists():
        return str(exe), base / "scripts"
    w = shutil.which("openocd")
    return (w, None) if w else (None, None)


def find_dfu_util():
    exe = PIO_HOME / "packages" / "tool-dfuutil" / "bin" / ("dfu-util.exe" if IS_WIN else "dfu-util")
    return str(exe) if exe.exists() else shutil.which("dfu-util")


# Runs in PlatformIO's Python (has pyserial). argv: port|auto, auto-reconnect 0/1, vid:pid.
# Lines from stdin are written to the port; "@@ ..." lines are status messages for the GUI.
SERIAL_SCRIPT = r"""
import os, sys, threading, time
import serial
from serial.tools import list_ports

want, auto, vidpid = sys.argv[1], sys.argv[2] == "1", sys.argv[3].lower()
lock = threading.Lock()
cur = {"p": None}

def writer():
    for line in sys.stdin:
        with lock:
            p = cur["p"]
            if p is not None:
                try:
                    p.write(line.replace("\n", "\r\n").encode("utf-8", "replace"))
                except Exception:
                    pass
    os._exit(0)

threading.Thread(target=writer, daemon=True).start()

def find():
    ports = list(list_ports.comports())
    if want != "auto":
        return want if any(p.device == want for p in ports) else None
    for p in ports:
        if p.vid is not None and ("%04x:%04x" % (p.vid, p.pid)) == vidpid:
            return p.device
    return None

waiting = False
while True:
    dev = find()
    if dev is None:
        if not auto:
            print("@@ error port not found: %s" % want, flush=True); sys.exit(1)
        if not waiting:
            print("@@ waiting for %s" % ("the controller (USB %s)" % vidpid if want == "auto" else want), flush=True)
            waiting = True
        time.sleep(0.4); continue
    try:
        p = serial.Serial(dev, 115200, timeout=0.05)
    except Exception as e:
        if not auto:
            print("@@ error cannot open %s: %s" % (dev, e), flush=True); sys.exit(1)
        time.sleep(0.5); continue
    waiting = False
    with lock:
        cur["p"] = p
    print("@@ connected %s" % dev, flush=True)
    buf = b""
    try:
        while True:
            buf += p.read(512)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                sys.stdout.write(line.decode("utf-8", "replace").rstrip("\r") + "\n")
            sys.stdout.flush()
    except Exception as e:
        with lock:
            cur["p"] = None
        try:
            p.close()
        except Exception:
            pass
        print("@@ disconnected (%s)" % e.__class__.__name__, flush=True)
        if not auto:
            sys.exit(1)
        time.sleep(0.3)
"""

LIST_PORTS_SCRIPT = ("from serial.tools import list_ports\n"
                     "for p in sorted(list_ports.comports()):\n"
                     "    v = '%04x:%04x' % (p.vid, p.pid) if p.vid is not None else ''\n"
                     "    print(p.device + '|' + v + '|' + (p.description or ''))")

SEND_LINE_SCRIPT = ("import sys, serial\n"
                    "p = serial.Serial(sys.argv[1], 115200, timeout=0.5)\n"
                    "p.write(sys.argv[2].encode() + b'\\r\\n'); p.flush(); p.close()")

HINTS = [
    (re.compile(r"No DFU capable USB device|Cannot open DFU device|LIBUSB_ERROR", re.I),
     "DFU: on Windows the bootloader needs the WinUSB driver once. Run Zadig (zadig.akeo.ie), choose "
     "'STM32 BOOTLOADER' (0483:DF11), install WinUSB, then retry."),
    (re.compile(r"open failed|No ST-?LINK|unable to find.*st-?link|libusb_open\(\) failed", re.I),
     "ST-Link not found: check its USB cable and driver (ST STSW-LINK009) and that no other program uses it."),
    (re.compile(r"init mode failed|unable to connect to the target|Target voltage: 0\.|target not halted|"
                r"Error connecting DP", re.I),
     "ST-Link cannot reach the STM32: check SWDIO, SWCLK, GND and 3V3, power the Blackpill, or hold NRST while "
     "starting the upload."),
    (re.compile(r"crystal too slow", re.I),
     "An 8 MHz MCP2515 crystal reaches at most 500 kbit/s: pick 500 kbit/s or a 16 MHz crystal."),
    (re.compile(r"could not open port|Access is denied|PermissionError", re.I),
     "The COM port is in use: disconnect the terminal or close other serial programs."),
]


def classify(line: str):
    low = line.lower()
    if re.search(r"\berror\b|failed|\[failed\]|cannot|not found", low) and "0 error" not in low:
        return "err"
    if "warning" in low:
        return "warn"
    if re.search(r"\[success\]|success|verified ok|file downloaded successfully|\bdone\b", low):
        return "ok"
    return None


def kill_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            proc.kill()
    except Exception:
        pass


# ----------------------------------------------------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Blackpill CAN Tool")
        # Sizes below are in 96-dpi pixels; scale them for high-DPI displays.
        self.ui = max(1.0, self.winfo_fpixels("1i") / 96.0)
        w = min(int(1240 * self.ui), int(self.winfo_screenwidth() * 0.92))
        h = min(int(820 * self.ui), int(self.winfo_screenheight() * 0.88))
        self.geometry(f"{w}x{h}")
        self.minsize(int(980 * min(self.ui, 1.25)), int(640 * min(self.ui, 1.25)))
        self.configure(background=PAPER)

        self.pio = find_pio()
        self.penv_python = find_penv_python()
        self.openocd, self.openocd_scripts = find_openocd()
        self.dfu_util = find_dfu_util()

        self.q: queue.Queue = queue.Queue()
        self.task_thread: threading.Thread | None = None
        self.task_proc: subprocess.Popen | None = None
        self.task_title = ""
        self.task_t0 = 0.0
        self.cancel = threading.Event()
        self.mon_proc: subprocess.Popen | None = None
        self.mon_lock = threading.Lock()
        self.mon_connected = False
        self.history: list[str] = []
        self.hist_pos = 0
        self.last_status = 0.0
        self.ports: list[tuple[str, str, str]] = []

        prefs = self._load_prefs()
        bitrate, crystal = read_can_settings()
        self.v_bitrate = tk.StringVar(value=self._fmt_bitrate(bitrate))
        self.v_crystal = tk.StringVar(value=f"{crystal // 1000000} MHz")
        self.v_method = tk.StringVar(value=prefs.get("method", "swd"))
        self.v_dfu_reboot = tk.BooleanVar(value=prefs.get("dfu_reboot", True))
        self.v_open_term = tk.BooleanVar(value=prefs.get("open_term", True))
        self.v_auto = tk.BooleanVar(value=prefs.get("auto_reconnect", True))
        self.v_hide_stream = tk.BooleanVar(value=prefs.get("hide_stream", True))
        self.v_port = tk.StringVar(value="Auto: AMR body controller")

        self._style()
        self._build()
        self._update_timing()
        self._update_method()
        self._log_tools()
        self.refresh_ports()
        self.after(40, self._poll)
        self.after(500, self._tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------ prefs
    def _load_prefs(self):
        try:
            return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_prefs(self):
        try:
            PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PREFS_FILE.write_text(json.dumps({
                "method": self.v_method.get(), "dfu_reboot": self.v_dfu_reboot.get(),
                "open_term": self.v_open_term.get(), "auto_reconnect": self.v_auto.get(),
                "hide_stream": self.v_hide_stream.get()}, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------ look
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        base = ("Segoe UI", 10) if IS_WIN else ("Helvetica", 10)
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=base[0], size=base[1])
        s.configure(".", background=PAPER, foreground=INK, bordercolor=LINE, lightcolor=PAPER, darkcolor=PAPER,
                    troughcolor=LINE, focuscolor=INK, font=base)
        s.configure("TFrame", background=PAPER)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=PAPER, foreground=INK)
        s.configure("Panel.TLabel", background=PANEL)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=(base[0], 9))
        s.configure("Head.TLabel", background=PANEL, foreground=INK, font=(base[0], 10, "bold"))
        s.configure("Title.TLabel", background=PAPER, foreground=INK, font=(base[0], 15, "bold"))
        s.configure("Sub.TLabel", background=PAPER, foreground=MUTED)
        s.configure("Key.TLabel", background=PANEL, foreground=MUTED)
        s.configure("Val.TLabel", background=PANEL, foreground=INK, font=(base[0], 10, "bold"))
        s.configure("Warn.TLabel", background=PANEL, foreground=RED, font=(base[0], 9))
        s.configure("State.TLabel", background=PANEL, font=(base[0], 15, "bold"))
        s.configure("Chip.TLabel", background=PAPER, foreground=MUTED, font=(base[0], 9))
        s.configure("TButton", background=PANEL, foreground=INK, bordercolor="#c9c7bf", lightcolor=PANEL,
                    darkcolor=PANEL, padding=(12, 6), relief="solid", borderwidth=1, width=0)
        s.map("TButton", background=[("disabled", PAPER), ("pressed", LINE), ("active", "#eceae3")],
              foreground=[("disabled", "#a9a79f")], bordercolor=[("disabled", LINE)])
        s.configure("Accent.TButton", background=INK, foreground="#ffffff", bordercolor=INK, lightcolor=INK,
                    darkcolor=INK)
        s.map("Accent.TButton", background=[("disabled", "#8d8c86"), ("pressed", "#000000"), ("active", "#38372f")],
              foreground=[("disabled", "#e9e8e2")], bordercolor=[("disabled", "#8d8c86")])
        s.configure("Danger.TButton", background=RED, foreground="#ffffff", bordercolor=RED, lightcolor=RED,
                    darkcolor=RED)
        s.map("Danger.TButton", background=[("disabled", "#e6a2aa"), ("pressed", "#a30015"), ("active", "#b8001a")],
              bordercolor=[("disabled", "#e6a2aa")])
        s.configure("Small.TButton", padding=(9, 3), font=(base[0], 9))
        self._indicators(s)
        s.configure("TRadiobutton", background=PANEL, foreground=INK, padding=(0, 2))
        s.map("TRadiobutton", background=[("active", PANEL)], foreground=[("disabled", "#a9a79f")])
        s.configure("TCheckbutton", background=PANEL, foreground=INK, padding=(0, 2))
        s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("disabled", "#a9a79f")])
        s.configure("TCombobox", fieldbackground=PANEL, background=PANEL, arrowcolor=INK, padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], selectbackground=[("readonly", PANEL)],
              selectforeground=[("readonly", INK)], background=[("readonly", PANEL)])
        s.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", background=PAPER, foreground=MUTED, padding=(16, 7), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", INK)])
        s.configure("TSeparator", background=LINE)
        s.configure("TEntry", fieldbackground=PANEL, padding=5)

    def _indicators(self, s: ttk.Style):
        """Checkbox and radio indicators drawn at the display's DPI (clam's built-in ones stay 9 px)."""
        n = self.px(15)
        c = (n - 1) / 2.0

        def seg_dist(px, py, ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5

        def make(fn):
            gap = self.px(7)   # image element padding is ignored by clam's layout, so the gap is part of the image
            im = tk.PhotoImage(master=self, width=n + gap, height=n)
            im.put(" ".join("{" + " ".join(fn(x, y) if x < n else PANEL for x in range(n + gap)) + "}"
                            for y in range(n)))
            return im

        def box(fill, edge, tick):
            def fn(x, y):
                if x == 0 or y == 0 or x == n - 1 or y == n - 1:
                    return edge
                if tick:
                    k = n / 15.0
                    d = min(seg_dist(x, y, 3.6 * k, 7.6 * k, 6.2 * k, 10.2 * k),
                            seg_dist(x, y, 6.2 * k, 10.2 * k, 11.4 * k, 4.4 * k))
                    if d <= 1.0 * k:
                        return "#ffffff"
                return fill
            return make(fn)

        def ring(fill, edge, dot):
            def fn(x, y):
                r = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
                if r > c + 0.3:
                    return PANEL
                if r > c - 0.9:
                    return edge
                if dot and r <= n * 0.24:
                    return dot
                return fill
            return make(fn)

        edge, edge_off = "#8f8d85", "#cfcdc5"
        self._imgs = {
            "cb": box("#ffffff", edge, False), "cb_on": box(INK, INK, True),
            "cb_dis": box(PAPER, edge_off, False), "cb_on_dis": box("#a9a79f", "#a9a79f", True),
            "rb": ring("#ffffff", edge, None), "rb_on": ring("#ffffff", INK, INK),
            "rb_dis": ring(PAPER, edge_off, None), "rb_on_dis": ring(PAPER, edge_off, "#a9a79f"),
        }
        i = self._imgs
        s.element_create("Bp.check", "image", i["cb"], ("disabled", "selected", i["cb_on_dis"]),
                         ("disabled", i["cb_dis"]), ("selected", i["cb_on"]), sticky="")
        s.element_create("Bp.radio", "image", i["rb"], ("disabled", "selected", i["rb_on_dis"]),
                         ("disabled", i["rb_dis"]), ("selected", i["rb_on"]), sticky="")
        for cls, ind in (("TCheckbutton", "Bp.check"), ("TRadiobutton", "Bp.radio")):
            pre = cls[1:]
            s.layout(cls, [(f"{pre}.padding", {"sticky": "nswe", "children": [
                (ind, {"side": "left", "sticky": ""}),
                (f"{pre}.focus", {"side": "left", "sticky": "w", "children": [
                    (f"{pre}.label", {"sticky": "nswe"})]})]})])

    def px(self, n: int) -> int:
        return int(n * self.ui)

    def _section(self, parent, title):
        box = ttk.Frame(parent, style="Panel.TFrame", padding=(14, 12))
        box.pack(fill="x", pady=(0, 10))
        ttk.Label(box, text=title, style="Head.TLabel").pack(anchor="w", pady=(0, 8))
        return box

    def _build(self):
        # header
        head = ttk.Frame(self, padding=(18, 12, 18, 10))
        head.pack(fill="x")
        ttk.Label(head, text="Blackpill CAN Tool", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="   AMR body controller · STM32F411CEU6 + MCP2515", style="Sub.TLabel").pack(side="left",
                                                                                                         pady=(4, 0))
        self.chips = ttk.Frame(head)
        self.chips.pack(side="right")
        for name, ok in (("PlatformIO", self.pio), ("OpenOCD", self.openocd), ("dfu-util", self.dfu_util),
                         ("pyserial", self.penv_python)):
            ttk.Label(self.chips, text=("● " if ok else "○ ") + name, style="Chip.TLabel",
                      foreground=GREEN if ok else RED).pack(side="left", padx=(12, 0))

        body = ttk.Frame(self, padding=(18, 0, 18, 0))
        body.pack(fill="both", expand=True)

        # sidebar
        side = ttk.Frame(body, width=self.px(340))
        side.pack(side="left", fill="y", padx=(0, 14))
        side.pack_propagate(False)
        self._build_build_box(side)
        self._build_upload_box(side)
        self._build_status_box(side)

        # tabs
        self.nb = ttk.Notebook(body)
        self.nb.pack(side="left", fill="both", expand=True)
        self.out_tab = ttk.Frame(self.nb, style="Panel.TFrame")
        self.term_tab = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(self.out_tab, text="Build output")
        self.nb.add(self.term_tab, text="Serial terminal")
        self._build_output_tab()
        self._build_terminal_tab()

        # status bar
        bar = ttk.Frame(self, padding=(18, 6))
        bar.pack(fill="x")
        self.status_lbl = ttk.Label(bar, text="Ready", style="Sub.TLabel")
        self.status_lbl.pack(side="left")
        ttk.Button(bar, text="Open project folder", style="Small.TButton",
                   command=lambda: self._open(ROOT)).pack(side="right")
        ttk.Button(bar, text="Open config.h", style="Small.TButton",
                   command=lambda: self._open(CONFIG_H)).pack(side="right", padx=(0, 6))

    def _build_build_box(self, side):
        box = self._section(side, "Build")
        grid = ttk.Frame(box, style="Panel.TFrame")
        grid.pack(fill="x")
        ttk.Label(grid, text="CAN bitrate", style="Key.TLabel").grid(row=0, column=0, sticky="w", pady=2)
        cb = ttk.Combobox(grid, textvariable=self.v_bitrate, state="readonly", width=14,
                          values=[self._fmt_bitrate(b) for b in BITRATES])
        cb.grid(row=0, column=1, sticky="e", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._update_timing())
        ttk.Label(grid, text="MCP2515 crystal", style="Key.TLabel").grid(row=1, column=0, sticky="w", pady=2)
        cc = ttk.Combobox(grid, textvariable=self.v_crystal, state="readonly", width=14,
                          values=[f"{c // 1000000} MHz" for c in CRYSTALS])
        cc.grid(row=1, column=1, sticky="e", pady=2)
        cc.bind("<<ComboboxSelected>>", lambda _e: self._update_timing())
        grid.columnconfigure(0, weight=1)
        self.timing_lbl = ttk.Label(box, text="", style="Muted.TLabel", wraplength=self.px(290), justify="left")
        self.timing_lbl.pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self.btn_build = ttk.Button(row, text="Build", style="Accent.TButton", command=self.do_build)
        self.btn_build.pack(side="left")
        self.btn_clean = ttk.Button(row, text="Clean", command=self.do_clean)
        self.btn_clean.pack(side="left", padx=(6, 0))

    def _build_upload_box(self, side):
        box = self._section(side, "Upload")
        r = ttk.Frame(box, style="Panel.TFrame")
        r.pack(fill="x")
        ttk.Radiobutton(r, text="SWD (ST-Link)", value="swd", variable=self.v_method,
                        command=self._update_method).pack(side="left")
        ttk.Radiobutton(r, text="USB DFU", value="dfu", variable=self.v_method,
                        command=self._update_method).pack(side="left", padx=(14, 0))
        self.chk_dfu = ttk.Checkbutton(box, text="Reboot into DFU over USB first", variable=self.v_dfu_reboot,
                                       command=self._save_prefs)
        self.chk_dfu.pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(box, text="Open the terminal after upload", variable=self.v_open_term,
                        command=self._save_prefs).pack(anchor="w", pady=(2, 0))
        self.method_lbl = ttk.Label(box, text="", style="Muted.TLabel", wraplength=self.px(290), justify="left")
        self.method_lbl.pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self.btn_upload = ttk.Button(row, text="Build + Upload", style="Accent.TButton", command=self.do_upload)
        self.btn_upload.pack(side="left")
        self.btn_stop = ttk.Button(row, text="Stop", command=self.stop_task, state="disabled")
        self.btn_stop.pack(side="left", padx=(6, 0))
        row2 = ttk.Frame(box, style="Panel.TFrame")
        row2.pack(fill="x", pady=(8, 0))
        self.btn_probe = ttk.Button(row2, text="Check ST-Link", style="Small.TButton", command=self.do_probe_stlink)
        self.btn_probe.pack(side="left")
        self.btn_dfu_list = ttk.Button(row2, text="Find DFU device", style="Small.TButton", command=self.do_list_dfu)
        self.btn_dfu_list.pack(side="left", padx=(6, 0))

    def _build_status_box(self, side):
        box = self._section(side, "Controller")
        self.state_lbl = ttk.Label(box, text="Not connected", style="State.TLabel", foreground=MUTED)
        self.state_lbl.pack(anchor="w")
        self.state_sub = ttk.Label(box, text="Connect the serial terminal to see live status.", style="Muted.TLabel",
                                   wraplength=self.px(290), justify="left")
        self.state_sub.pack(anchor="w", pady=(0, 8))
        grid = ttk.Frame(box, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.rows = {}
        for i, (key, label) in enumerate((("power", "Hoverboard power"), ("button", "E-stop button"),
                                          ("host", "Pi (HB_CMD)"), ("wheels", "Wheels"),
                                          ("hb", "Hoverboard latch"), ("can", "CAN"), ("frames", "Frames"))):
            ttk.Label(grid, text=label, style="Key.TLabel").grid(row=i, column=0, sticky="w", pady=1)
            v = ttk.Label(grid, text="–", style="Val.TLabel")
            v.grid(row=i, column=1, sticky="e", pady=1)
            self.rows[key] = v
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self.btn_estop = ttk.Button(row, text="E-stop", style="Danger.TButton",
                                    command=lambda: self.term_send("estop"))
        self.btn_estop.pack(side="left")
        self.btn_release = ttk.Button(row, text="Release", command=lambda: self.term_send("release"))
        self.btn_release.pack(side="left", padx=(6, 0))
        self._set_status_buttons(False)

    def _text(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        mono = ("Consolas", 10) if IS_WIN else ("Menlo", 10)
        t = tk.Text(frame, wrap="none", font=mono, background=TERM_BG, foreground=TERM_FG, insertbackground=TERM_FG,
                    relief="flat", borderwidth=0, padx=12, pady=10, undo=False, state="disabled",
                    selectbackground="#3a3d44")
        ys = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        for tag, color in (("info", "#8ab4f8"), ("err", "#f28b82"), ("ok", "#81c995"), ("warn", "#fdd663"),
                           ("cmd", "#ffffff"), ("tx", "#c9a0ff"), ("hint", "#fdd663"), ("dim", "#7d7f84")):
            t.tag_configure(tag, foreground=color)
        t.tag_configure("cmd", font=(mono[0], 10, "bold"))
        return t

    def _build_output_tab(self):
        bar = ttk.Frame(self.out_tab, style="Panel.TFrame", padding=(10, 8))
        bar.pack(fill="x")
        self.task_lbl = ttk.Label(bar, text="No task running", style="Muted.TLabel")
        self.task_lbl.pack(side="left")
        ttk.Button(bar, text="Clear", style="Small.TButton",
                   command=lambda: self._clear(self.out_text)).pack(side="right")
        self.out_text = self._text(self.out_tab)

    def _build_terminal_tab(self):
        bar = ttk.Frame(self.term_tab, style="Panel.TFrame", padding=(10, 8))
        bar.pack(fill="x")
        ttk.Label(bar, text="Port", style="Key.TLabel").pack(side="left")
        self.port_cb = ttk.Combobox(bar, textvariable=self.v_port, state="readonly", width=34)
        self.port_cb.pack(side="left", padx=(6, 4))
        ttk.Button(bar, text="Refresh", style="Small.TButton", command=self.refresh_ports).pack(side="left")
        self.btn_connect = ttk.Button(bar, text="Connect", style="Accent.TButton", command=self.toggle_monitor)
        self.btn_connect.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar, text="Auto-reconnect", variable=self.v_auto,
                        command=self._save_prefs).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(bar, text="Hide @S status lines", variable=self.v_hide_stream,
                        command=self._save_prefs).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Clear", style="Small.TButton",
                   command=lambda: self._clear(self.term_text)).pack(side="right")

        quick = ttk.Frame(self.term_tab, style="Panel.TFrame", padding=(10, 0, 10, 6))
        quick.pack(fill="x")
        self.quick_btns = []
        for cmd in ("status", "can", "stats", "log rx", "log all", "log off", "selftest", "version", "help"):
            b = ttk.Button(quick, text=cmd, style="Small.TButton", command=lambda c=cmd: self.term_send(c))
            b.pack(side="left", padx=(0, 4))
            self.quick_btns.append(b)
        self.term_text = self._text(self.term_tab)

        entry_row = ttk.Frame(self.term_tab, style="Panel.TFrame", padding=(10, 8))
        entry_row.pack(fill="x")
        self.entry = ttk.Entry(entry_row, font=("Consolas", 10) if IS_WIN else ("Menlo", 10))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self._send_entry())
        self.entry.bind("<Up>", lambda _e: self._history(-1))
        self.entry.bind("<Down>", lambda _e: self._history(1))
        self.btn_send = ttk.Button(entry_row, text="Send", command=self._send_entry)
        self.btn_send.pack(side="left", padx=(6, 0))
        self._set_term_controls(False)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _fmt_bitrate(b: int) -> str:
        return f"{b // 1000000} Mbit/s" if b >= 1000000 else f"{b // 1000} kbit/s"

    def _bitrate(self) -> int:
        v = self.v_bitrate.get()
        n = int(v.split()[0])
        return n * 1000000 if "Mbit" in v else n * 1000

    def _crystal(self) -> int:
        return int(self.v_crystal.get().split()[0]) * 1000000

    def _update_timing(self):
        t = can_timing(self._crystal(), self._bitrate())
        if t is None:
            self.timing_lbl.configure(
                text=f"Not reachable: a {self._crystal() // 1000000} MHz crystal cannot run "
                     f"{self.v_bitrate.get()}. Use 500 kbit/s or a 16 MHz crystal.", style="Warn.TLabel")
            return
        note = f"{t['tq']} time quanta, sample point {t['sample']:.1f} %, SJW {t['sjw']}."
        if self._bitrate() != 1000000:
            note += " The hoverboard (CAN_BITRATE) and the Pi (ip link … bitrate) must use the same bitrate."
        self.timing_lbl.configure(text=note, style="Muted.TLabel")

    def _update_method(self):
        dfu = self.v_method.get() == "dfu"
        self.chk_dfu.configure(state="normal" if dfu else "disabled")
        self.method_lbl.configure(text=(
            "USB-C cable only. The tool reboots a running board into the bootloader; otherwise hold BOOT0, "
            "tap NRST, release BOOT0." if dfu else
            "ST-Link to the 4-pin SWD header: SWDIO (PA13), SWCLK (PA14), GND, 3V3."))
        self._save_prefs()

    def _open(self, path: Path):
        try:
            if IS_WIN:
                os.startfile(str(path))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Open", str(e))

    @staticmethod
    def _clear(t: tk.Text):
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.configure(state="disabled")

    @staticmethod
    def _append(t: tk.Text, text: str, tag=None):
        at_end = t.yview()[1] > 0.995
        t.configure(state="normal")
        t.insert("end", text + "\n", tag or ())
        lines = int(t.index("end-1c").split(".")[0])
        if lines > 6000:
            t.delete("1.0", f"{lines - 5000}.0")
        t.configure(state="disabled")
        if at_end:
            t.see("end")

    def post(self, text: str, tag=None):
        """Thread-safe: queue a line for the build output."""
        self.q.put(("out", text, tag))

    def _log_tools(self):
        self.post(f"Project: {ROOT}", "dim")
        self.post(f"PlatformIO: {self.pio or 'NOT FOUND (install the VS Code PlatformIO extension)'}",
                  "dim" if self.pio else "err")
        self.post(f"dfu-util: {self.dfu_util or 'not installed yet (PlatformIO installs it on the first DFU upload)'}",
                  "dim")
        self.post(f"OpenOCD: {self.openocd or 'not installed yet (installed with the first SWD upload)'}", "dim")

    # ------------------------------------------------------------------ queue pump
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "out":
                    self._append(self.out_text, item[1], item[2])
                elif kind == "mon":
                    self._on_mon_line(item[1])
                elif kind == "mon_exit":
                    if item[1] is self.mon_proc:
                        self._on_mon_exit()
                elif kind == "done":
                    self._on_task_done(*item[1:])
                elif kind == "ports":
                    self._set_ports(item[1])
                elif kind == "call":
                    item[1]()
        except queue.Empty:
            pass
        self.after(40, self._poll)

    def _tick(self):
        if self.task_thread is not None:
            self.status_lbl.configure(text=f"{self.task_title}… {time.time() - self.task_t0:.0f} s")
        if self.mon_connected and self.last_status and time.time() - self.last_status > 1.5:
            self._show_no_data("No status from the controller", "Is the firmware running? Type 'stream on'.")
        self.after(500, self._tick)

    # ------------------------------------------------------------------ tasks
    def _busy(self, busy: bool):
        st = "disabled" if busy else "normal"
        for b in (self.btn_build, self.btn_clean, self.btn_upload, self.btn_probe, self.btn_dfu_list):
            b.configure(state=st)
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def run_pipeline(self, title: str, steps, on_success=None):
        if self.task_thread is not None:
            return
        if not self.pio:
            messagebox.showerror(title, "PlatformIO was not found. Install the PlatformIO extension in VS Code.")
            return
        self.cancel.clear()
        self.task_title, self.task_t0 = title, time.time()
        self._busy(True)
        self.task_lbl.configure(text=title)
        self.nb.select(self.out_tab)
        self.post("")
        self.post(f"━━ {title} ━━", "info")

        def work():
            ok = True
            for step in steps:
                if self.cancel.is_set():
                    ok = False
                    break
                ok = self._run_cmd(*step[1:]) if step[0] == "cmd" else step[1]()
                if not ok:
                    break
            self.q.put(("done", title, ok, time.time() - self.task_t0, on_success))

        self.task_thread = threading.Thread(target=work, daemon=True)
        self.task_thread.start()

    def _run_cmd(self, args, ok_fn=None) -> bool:
        shown = " ".join(f'"{a}"' if " " in a else a for a in args)
        self.post(f"$ {shown}", "cmd")
        try:
            proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                    env={**os.environ, "PYTHONUNBUFFERED": "1", "PLATFORMIO_FORCE_ANSI": "false"})
        except OSError as e:
            self.post(f"Cannot start: {e}", "err")
            return False
        self.task_proc = proc
        lines = []
        for raw in iter(proc.stdout.readline, b""):
            line = ANSI_RE.sub("", raw.decode("utf-8", "replace").rstrip("\r\n"))
            lines.append(line)
            self.post(line, classify(line))
        code = proc.wait()
        self.task_proc = None
        if self.cancel.is_set():
            return False
        output = "\n".join(lines)
        ok = ok_fn(code, output) if ok_fn else code == 0
        if not ok:
            self._hints(output)
        return ok

    def _hints(self, output: str):
        for rx, hint in HINTS:
            if rx.search(output):
                self.post(f"Hint: {hint}", "hint")

    def _on_task_done(self, title, ok, secs, on_success):
        self.task_thread = None
        self._busy(False)
        cancelled = self.cancel.is_set()
        msg = f"{title}: {'stopped' if cancelled else 'done' if ok else 'FAILED'} ({secs:.1f} s)"
        self.post(f"━━ {msg} ━━", "warn" if cancelled else "ok" if ok else "err")
        self.status_lbl.configure(text=msg)
        self.task_lbl.configure(text=msg)
        if ok and on_success:
            on_success()

    def stop_task(self):
        self.cancel.set()
        kill_tree(self.task_proc)

    def _apply_can_settings(self) -> bool:
        if can_timing(self._crystal(), self._bitrate()) is None:
            messagebox.showerror("CAN settings", self.timing_lbl.cget("text"))
            return False
        try:
            if write_can_settings(self._bitrate(), self._crystal()):
                self.post(f"platformio.ini: CAN_BITRATE={self._bitrate()}, MCP2515_CRYSTAL_HZ={self._crystal()}", "info")
        except OSError as e:
            messagebox.showerror("platformio.ini", str(e))
            return False
        return True

    def do_build(self):
        if self._apply_can_settings():
            self.run_pipeline("Build", [("cmd", [self.pio, "run", "-e", ENV])])

    def do_clean(self):
        self.run_pipeline("Clean", [("cmd", [self.pio, "run", "-e", ENV, "-t", "clean"])])

    def do_upload(self):
        if not self._apply_can_settings():
            return
        after = self._after_upload if self.v_open_term.get() else None
        if self.v_method.get() == "swd":
            self.run_pipeline("Upload over SWD", [("cmd", [self.pio, "run", "-e", ENV, "-t", "upload"])], after)
            return
        if not self.dfu_util:   # let PlatformIO install dfu-util and do the whole job
            steps = []
            if self.v_dfu_reboot.get():
                steps.append(("call", self._reboot_to_dfu))
            steps.append(("cmd", [self.pio, "run", "-e", ENV_DFU, "-t", "upload"]))
            self.run_pipeline("Upload over DFU", steps, after)
            return
        steps = [("cmd", [self.pio, "run", "-e", ENV])]
        if self.v_dfu_reboot.get():
            steps.append(("call", self._reboot_to_dfu))
        steps.append(("call", lambda: self._wait_dfu(12)))
        steps.append(("cmd", [self.dfu_util, "-d", DFU_VIDPID, "-a", "0", "-s", f"{FLASH_ADDR}:leave",
                              "-D", str(FIRMWARE)], self._dfu_ok))
        self.run_pipeline("Upload over DFU", steps, after)

    @staticmethod
    def _dfu_ok(code, output) -> bool:
        # dfu-util 0.9 often reports an error on the final status request after ":leave" although the
        # image was written and verified.
        return code == 0 or "File downloaded successfully" in output

    def _after_upload(self):
        self._log_term("Upload finished; connecting to the controller…", "info")
        self.nb.select(self.term_tab)
        if self.mon_proc is None:
            self.after(800, lambda: self.start_monitor(force_auto=True))

    def _dfu_present(self) -> bool:
        try:
            r = subprocess.run([self.dfu_util, "-l"], capture_output=True, timeout=8, creationflags=NO_WINDOW)
            return DFU_VIDPID in (r.stdout + r.stderr).decode("utf-8", "replace").lower()
        except Exception:
            return False

    def _reboot_to_dfu(self) -> bool:
        if self.dfu_util and self._dfu_present():
            self.post("A DFU device is already connected.", "ok")
            return True
        if self.mon_connected:
            self.post("Sending 'dfu' through the open terminal.", "info")
            self.term_send("dfu", from_worker=True)
            time.sleep(0.4)
            return True
        port = self._find_app_port()
        if not port:
            self.post("Controller not found on USB. If the board has no firmware yet, put it in DFU mode by hand: "
                      "hold BOOT0, tap NRST, release BOOT0.", "warn")
            return True
        self.post(f"Sending 'dfu' to {port}.", "info")
        try:
            subprocess.run([self.penv_python, "-c", SEND_LINE_SCRIPT, port, "dfu"], capture_output=True, timeout=8,
                           creationflags=NO_WINDOW)
        except Exception as e:
            self.post(f"Could not send 'dfu': {e}", "warn")
        time.sleep(0.4)
        return True

    def _wait_dfu(self, timeout: float) -> bool:
        self.post("Waiting for the STM32 bootloader (USB 0483:DF11)…", "info")
        t0 = time.time()
        while time.time() - t0 < timeout and not self.cancel.is_set():
            if self._dfu_present():
                self.post(f"DFU device found after {time.time() - t0:.1f} s.", "ok")
                return True
            time.sleep(0.5)
        if not self.cancel.is_set():
            self.post("No DFU device appeared.", "err")
            self.post("Hint: hold BOOT0, tap NRST, release BOOT0 and press Upload again. On Windows the bootloader "
                      "needs the WinUSB driver once (Zadig: 'STM32 BOOTLOADER' → WinUSB).", "hint")
        return False

    def do_probe_stlink(self):
        if not self.openocd:
            self.post("OpenOCD is installed with the first SWD upload; upload once over SWD or install the "
                      "PlatformIO STM32 platform.", "warn")
            return
        iface = "interface/stlink.cfg"
        if self.openocd_scripts and not (self.openocd_scripts / iface).exists():
            iface = "interface/stlink-v2.cfg"
        args = [self.openocd]
        if self.openocd_scripts:
            args += ["-s", str(self.openocd_scripts)]
        args += ["-f", iface, "-f", "target/stm32f4x.cfg", "-c", "init", "-c", "exit"]
        self.run_pipeline("Check ST-Link", [("cmd", args, lambda code, out: code == 0 and "Cortex-M4" in out)])

    def do_list_dfu(self):
        if not self.dfu_util:
            self.post("dfu-util is installed with the first DFU upload.", "warn")
            return

        def ok(code, out):
            found = DFU_VIDPID in out.lower()
            self.post("STM32 bootloader found." if found else "No STM32 bootloader in DFU mode.",
                      "ok" if found else "warn")
            return found
        self.run_pipeline("Find DFU device", [("cmd", [self.dfu_util, "-l"], ok)])

    # ------------------------------------------------------------------ ports
    def refresh_ports(self):
        if not self.penv_python:
            return

        def work():
            try:
                r = subprocess.run([self.penv_python, "-c", LIST_PORTS_SCRIPT], capture_output=True, timeout=15,
                                   creationflags=NO_WINDOW)
                ports = [tuple((ln.split("|", 2) + ["", ""])[:3]) for ln in r.stdout.decode().splitlines() if ln]
            except Exception:
                ports = []
            self.q.put(("ports", ports))
        threading.Thread(target=work, daemon=True).start()

    def _set_ports(self, ports):
        self.ports = ports
        vals = ["Auto: AMR body controller"]
        for dev, vidpid, desc in ports:
            mark = "  ← controller" if vidpid == APP_VIDPID else ""
            vals.append(f"{dev}  {desc}{mark}")
        self.port_cb.configure(values=vals)
        if self.v_port.get() not in vals:
            self.v_port.set(vals[0])

    def _selected_port(self) -> str:
        v = self.v_port.get()
        return "auto" if v.startswith("Auto") else v.split()[0]

    def _find_app_port(self):
        try:
            r = subprocess.run([self.penv_python, "-c", LIST_PORTS_SCRIPT], capture_output=True, timeout=15,
                               creationflags=NO_WINDOW)
            for ln in r.stdout.decode().splitlines():
                dev, vidpid, _ = (ln.split("|", 2) + ["", ""])[:3]
                if vidpid == APP_VIDPID:
                    return dev
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ terminal
    def _log_term(self, text, tag=None):
        self._append(self.term_text, text, tag)

    def _set_term_controls(self, connected: bool):
        st = "normal" if connected else "disabled"
        for b in self.quick_btns + [self.btn_send]:
            b.configure(state=st)
        self.entry.configure(state=st)
        self._set_status_buttons(connected)

    def _set_status_buttons(self, on: bool):
        self.btn_estop.configure(state="normal" if on else "disabled")
        self.btn_release.configure(state="normal" if on else "disabled")

    def toggle_monitor(self):
        if self.mon_proc is not None:
            self.stop_monitor()
        else:
            self.start_monitor()

    def start_monitor(self, force_auto=False):
        if self.mon_proc is not None:
            return
        if not self.penv_python:
            messagebox.showerror("Serial terminal", "PlatformIO's Python (with pyserial) was not found.")
            return
        port = "auto" if force_auto else self._selected_port()
        auto = "1" if (self.v_auto.get() or force_auto) else "0"
        try:
            proc = subprocess.Popen([self.penv_python, "-u", "-c", SERIAL_SCRIPT, port, auto, APP_VIDPID],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                    creationflags=NO_WINDOW)
        except OSError as e:
            messagebox.showerror("Serial terminal", str(e))
            return
        self.mon_proc = proc
        self.btn_connect.configure(text="Disconnect", style="TButton")
        self._log_term(f"Opening {'the controller (auto)' if port == 'auto' else port}…", "dim")

        def reader():
            for raw in iter(proc.stdout.readline, b""):
                self.q.put(("mon", raw.decode("utf-8", "replace").rstrip("\r\n")))
            self.q.put(("mon_exit", proc))
        threading.Thread(target=reader, daemon=True).start()

    def stop_monitor(self):
        proc, self.mon_proc = self.mon_proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            kill_tree(proc)
        self._on_mon_exit()
        self._log_term("Disconnected.", "dim")

    def _on_mon_exit(self):
        self.mon_proc = None
        self.mon_connected = False
        self.btn_connect.configure(text="Connect", style="Accent.TButton")
        self._set_term_controls(False)
        self._show_no_data("Not connected", "Connect the serial terminal to see live status.")

    def _on_mon_line(self, line: str):
        if line.startswith("@@ "):
            msg = line[3:]
            if msg.startswith("connected"):
                self.mon_connected = True
                self._set_term_controls(True)
                self._log_term(f"Connected to {msg.split(' ', 1)[1]}.", "ok")
                self.last_status = time.time()
                self.term_send("version", echo=False)
                self.term_send("stream on", echo=False)
            elif msg.startswith("disconnected"):
                self.mon_connected = False
                self._set_term_controls(False)
                self._log_term("Port closed (board reset or unplugged). Waiting for it to come back…"
                               if self.v_auto.get() else "Port closed.", "warn")
                self._show_no_data("Disconnected", "Waiting for the controller to come back on USB.")
            elif msg.startswith("waiting"):
                self._log_term(msg[0].upper() + msg[1:] + "…", "dim")
            else:
                self._log_term(msg, "err")
                self._hints(msg)
            return
        if line.startswith("@S "):
            self._on_stream(line)
            if self.v_hide_stream.get():
                return
            self._log_term(line, "dim")
            return
        if line.startswith("stream ") and self.v_hide_stream.get():
            return
        tag = None
        low = line.lower()
        if any(k in low for k in ("e-stop latched", "button pressed", "failed", "refused", "lost", "bus-off")):
            tag = "err"
        elif any(k in low for k in ("released", "ready", "passed", "link up")):
            tag = "ok"
        elif any(k in low for k in ("relay", "warning")):
            tag = "warn"
        self._log_term(line, tag)

    def term_send(self, text: str, echo=True, from_worker=False):
        proc = self.mon_proc
        if proc is None or proc.poll() is not None:
            return
        if echo:
            if from_worker:
                self.q.put(("call", lambda: self._log_term(f"> {text}", "tx")))
            else:
                self._log_term(f"> {text}", "tx")
        try:
            with self.mon_lock:
                proc.stdin.write((text + "\n").encode())
                proc.stdin.flush()
        except Exception:
            pass

    def _send_entry(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.hist_pos = len(self.history)
        self.term_send(text)

    def _history(self, step):
        if not self.history:
            return "break"
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + step))
        self.entry.delete(0, "end")
        if self.hist_pos < len(self.history):
            self.entry.insert(0, self.history[self.hist_pos])
        return "break"

    # ------------------------------------------------------------------ live status
    def _show_no_data(self, title, sub):
        self.last_status = 0.0
        self.state_lbl.configure(text=title, foreground=MUTED)
        self.state_sub.configure(text=sub)
        for v in self.rows.values():
            v.configure(text="–", foreground=INK)

    def _on_stream(self, line: str):
        d = {}
        for part in line[3:].split():
            k, _, v = part.partition("=")
            d[k] = v
        try:
            g = lambda k: int(d.get(k, "0"))
            self.last_status = time.time()
            causes = {0: "none", 1: "host request", 2: "e-stop button", 3: "host timeout"}
            if g("active"):
                self.state_lbl.configure(text="E-STOP ACTIVE", foreground=RED)
                self.state_sub.configure(text=f"Cause: {causes.get(g('cause'), '?')}. Release from the Pi "
                                              f"(/estop false) or with Release.")
            else:
                self.state_lbl.configure(text="Running", foreground=GREEN)
                self.state_sub.configure(text=f"E-stop released. Up {g('up') // 1000} s.")
            self._row("power", "Relay closed" if g("power") else "Relay OPEN", None if g("power") else RED)
            self._row("button", "PRESSED" if g("button") else "Released", RED if g("button") else None)
            host = g("host_ms")
            armed = "armed" if g("armed") else "not armed"
            self._row("host", f"never seen, {armed}" if host < 0 else f"{host} ms ago, {armed}",
                      AMBER if host < 0 or host > 500 else None)
            self._row("wheels", f"{g('rpm_l')} / {g('rpm_r')} rpm" if g("motion") else "no data",
                      None if g("motion") else MUTED)
            self._row("hb", ("latched" if g("hb_estop") else "not latched") if g("hb") else "no data",
                      None if g("hb") else MUTED)
            self._row("can", f"ok · TEC {g('tec')} REC {g('rec')}" if g("can") else "MCP2515 NOT READY",
                      (AMBER if g("tec") > 96 or g("rec") > 96 else None) if g("can") else RED)
            self._row("frames", f"rx {g('rx')} · tx {g('tx')}")
        except ValueError:
            pass

    def _row(self, key, text, color=None):
        self.rows[key].configure(text=text, foreground=color or INK)

    # ------------------------------------------------------------------ close
    def on_close(self):
        if self.task_thread is not None and not messagebox.askyesno("Quit", "A task is still running. Stop it and quit?"):
            return
        self.stop_task()
        if self.mon_proc is not None:
            self.stop_monitor()
        self._save_prefs()
        self.destroy()


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
