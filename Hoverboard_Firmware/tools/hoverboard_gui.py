#!/usr/bin/env python3
"""
Hoverboard Firmware Tool

- choose the build variant (PlatformIO environment)
- edit Inc/config.h with switches, drop-down menus and text boxes
- build, upload with ST-Link, clean, full chip erase and unlock (OpenOCD)
- build/upload logs and a serial monitor in one window

Requirements: Python 3.8+ with tkinter (included in the python.org Windows installer) and PlatformIO Core
(installed with the VS Code PlatformIO extension). The serial monitor uses pyserial from PlatformIO's own Python.

Run:  python tools/hoverboard_gui.py      or double-click "Hoverboard Tool.bat" in the project folder.
"""
from __future__ import annotations

import configparser
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

ROOT      = Path(__file__).resolve().parent.parent
CONFIG_H  = ROOT / "Inc" / "config.h"
PIO_INI   = ROOT / "platformio.ini"
PIO_HOME  = Path(os.environ.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio")))
IS_WIN    = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0     # subprocess.CREATE_NO_WINDOW

# ----------------------------------------------------------------------------------------------------------------------
# config.h model
# ----------------------------------------------------------------------------------------------------------------------
HIDDEN_SECTIONS = ("VARIANT SELECTION", "DO-NOT-TOUCH", "APPLY DEFAULT", "VALIDATE", "INPUT FORMAT")
CONSTANT_NAMES  = {"CONFIG_H", "COM_CTRL", "SIN_CTRL", "FOC_CTRL", "OPEN_MODE", "VLT_MODE", "SPD_MODE", "TRQ_MODE"}

BAUDS = [(b, b) for b in ("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")]
ENUMS = {   # name: ([(value, label), ...], free text allowed)
    "CTRL_TYP_SEL":      ([("COM_CTRL", "COM_CTRL - Commutation"), ("SIN_CTRL", "SIN_CTRL - Sinusoidal"),
                           ("FOC_CTRL", "FOC_CTRL - Field Oriented")], False),
    "CTRL_MOD_REQ":      ([("OPEN_MODE", "OPEN_MODE - Open"), ("VLT_MODE", "VLT_MODE - Voltage"),
                           ("SPD_MODE", "SPD_MODE - Speed (FOC)"), ("TRQ_MODE", "TRQ_MODE - Torque (FOC)")], False),
    "BOARD_VARIANT":     ([("0", "0 - Default board"), ("1", "1 - Alternate pinout")], False),
    "CAN_PINS":          ([("1", "1 - PB8 RX / PB9 TX (remap)"), ("0", "0 - PA11 RX / PA12 TX")], False),
    "CAN_BITRATE":       ([("1000000", "1000000 - 1 Mbit/s"), ("500000", "500000 - 500 kbit/s"),
                           ("250000", "250000 - 250 kbit/s"), ("125000", "125000 - 125 kbit/s")], False),
    "USART2_BAUD":       (BAUDS, True),
    "USART3_BAUD":       (BAUDS, True),
    "USART2_WORDLENGTH": ([("UART_WORDLENGTH_8B", "8 bit"), ("UART_WORDLENGTH_9B", "9 bit")], False),
    "USART3_WORDLENGTH": ([("UART_WORDLENGTH_8B", "8 bit"), ("UART_WORDLENGTH_9B", "9 bit")], False),
}
PRIORITY_OPTS = [("0", "0 - Primary input"), ("1", "1 - Auxiliary input")]
INPUT_TYPES   = [("0", "0 Disabled"), ("1", "1 Normal pot"), ("2", "2 Mid-rest pot"), ("3", "3 Auto-detect")]
SECTION_NAMES = {"BOARD VARIANT": "Board variant", "BATTERY": "Battery", "TEMPERATURE": "Temperature",
                 "MOTOR CONTROL": "Motor control", "DEFAULT SETTINGS": "Default settings",
                 "CRUISE CONTROL SETTINGS": "Cruise control", "DEBUG SERIAL": "Debug serial", "DEBUG LCD": "Debug LCD",
                 "UART SETIINGS": "UART", "UART SETTINGS": "UART", "CAN SETTINGS": "CAN bus"}


def section_label(title: str) -> str:
    m = re.match(r"VARIANT_(\w+)", title)
    if m:
        return "Variant: " + m.group(1)
    return SECTION_NAMES.get(title, title.capitalize())

SECTION_RE = re.compile(r"^//\s*#{3,}\s*(.*?)\s*#{3,}\s*$")
DEFINE_RE  = re.compile(r"^(?P<indent>[ \t]*)(?P<off>//[ \t]*)?#define[ \t]+(?P<name>[A-Za-z_]\w*)(?P<rest>.*)$")
COND_RE    = re.compile(r"^\s*#\s*(?P<kw>ifdef|ifndef|if|elif|else|endif)\b(?P<expr>.*)$")
NUM_RE     = re.compile(r"^-?(0[xX][0-9A-Fa-f]+|\d+(\.\d+)?)[uUlLfF]*$")
INT_RE     = re.compile(r"^-?\d+$")
TUPLE_RE   = re.compile(r"^\s*-?\d+\s*(,\s*-?\d+\s*){4}$")
ANSI_RE    = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class Define:
    idx: int                # line index in config.h
    indent: str
    off_prefix: str         # "" if active in the file, e.g. "// " if commented out
    name: str
    sp1: str                # whitespace between name and value
    value: str              # "" for flags
    comment: str            # trailing "// ..." or ""
    comment_col: int        # column of the trailing comment, -1 if none
    section: str
    context: str            # surrounding #if conditions, e.g. "DUAL_INPUTS"
    kind: str = "text"      # flag | bool01 | enum | number | text | tuple
    options: list = field(default_factory=list)
    free_text: bool = False
    was_off: bool = False
    en_var: object = None
    val_vars: list = field(default_factory=list)
    widgets: list = field(default_factory=list)
    value_widgets: list = field(default_factory=list)
    name_label: object = None
    err_label: object = None
    show_context: bool = True

    @property
    def enabled_in_file(self) -> bool:
        return self.off_prefix == ""

    @property
    def description(self) -> str:
        return self.comment.lstrip("/").strip()


def _cond_label(kw: str, expr: str):
    expr = re.sub(r"//.*$", "", expr).replace("\\", " ").strip()
    if kw == "ifdef":
        label = expr
    elif kw == "ifndef":
        label = "!" + expr
    else:
        label = re.sub(r"defined\s*\(\s*(\w+)\s*\)", r"\1", expr)
    label = re.sub(r"\s+", " ", label).strip()
    if re.fullmatch(r"VARIANT_\w+", label) or label in ("!CONFIG_H", "!PLATFORMIO"):
        return None         # implied by the tab (variant section) or not interesting
    return label


def _classify(d: Define) -> None:
    v = d.value
    if v == "":
        d.kind = "flag"
    elif d.name in ENUMS:
        d.kind, (d.options, d.free_text) = "enum", ENUMS[d.name]
    elif TUPLE_RE.match(v):
        d.kind = "tuple"
    elif re.match(r"^(CONTROL|SIDEBOARD_SERIAL)_", d.name) and v in ("0", "1"):
        d.kind, d.options = "enum", PRIORITY_OPTS
    elif v in ("0", "1") and (d.name.endswith(("_ENA", "_ENABLE")) or d.name == "BEEPS_BACKWARD"
                              or re.search(r"\b(1 or 0|0 or 1)\b", d.comment)):
        d.kind = "bool01"
    elif NUM_RE.match(v):
        d.kind = "number"
    else:
        d.kind = "text"


def parse_config(text: str):
    """Returns (lines with line endings, [Define], [visible section titles in file order])."""
    lines = text.splitlines(keepends=True)
    defines = []
    sections = []
    section = None
    cond = []
    in_block = False
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")
        s = line.strip()
        i += 1
        if in_block:
            if "*/" in s:
                in_block = False
            continue
        if s.startswith("/*") and "*/" not in s:
            in_block = True
            continue

        m = SECTION_RE.match(s)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).upper()
            if title.startswith("END OF") or title == section:
                section = None
            else:
                section = title
                if not any(h in title for h in HIDDEN_SECTIONS) and title not in sections:
                    sections.append(title)
            continue

        mc = COND_RE.match(line)
        if mc:
            kw, expr = mc.group("kw"), mc.group("expr")
            while expr.rstrip().endswith("\\") and i < len(lines):     # line continuation
                expr = expr.rstrip()[:-1] + " " + lines[i].strip()
                i += 1
            if kw in ("if", "ifdef", "ifndef"):
                cond.append(_cond_label(kw, expr))
            elif kw == "elif" and cond:
                cond[-1] = _cond_label("if", expr)
            elif kw == "else" and cond and cond[-1] is not None:
                cond[-1] = "not (" + cond[-1] + ")"
            elif kw == "endif" and cond:
                cond.pop()
            continue

        md = DEFINE_RE.match(line)
        if not md or section is None or any(h in section for h in HIDDEN_SECTIONS) or md.group("name") in CONSTANT_NAMES:
            continue
        rest = md.group("rest")
        cpos = rest.find("//")
        value_part, comment = (rest, "") if cpos < 0 else (rest[:cpos], rest[cpos:])
        d = Define(idx=i - 1, indent=md.group("indent"), off_prefix=md.group("off") or "", name=md.group("name"),
                   sp1=value_part[:len(value_part) - len(value_part.lstrip())], value=value_part.strip(),
                   comment=comment, comment_col=(len(line) - len(rest) + cpos) if cpos >= 0 else -1,
                   section=section, context=" & ".join(c for c in cond if c))
        d.was_off = not d.enabled_in_file
        _classify(d)
        defines.append(d)
    return lines, defines, sections


def render_define(d: Define, enabled: bool, value: str) -> str:
    off = "" if enabled else (d.off_prefix or "// ")
    head = d.indent + off + "#define " + d.name
    if value != "":
        head += (d.sp1 or " ") + value
    if d.comment:
        head += " " * max(1, d.comment_col - len(head)) + d.comment
    return head


# ----------------------------------------------------------------------------------------------------------------------
# platformio.ini helpers
# ----------------------------------------------------------------------------------------------------------------------
DEFAULT_ENV_RE = re.compile(r"^(?P<off>;?)(?P<sp>\s*)default_envs\s*=\s*(?P<env>[^\s;]+)(?P<tail>.*)$")


def read_envs():
    text = PIO_INI.read_text(encoding="utf-8")
    envs = re.findall(r"^\[env:([^\]]+)\]", text, re.M)
    descr, active = {}, None
    for line in text.splitlines():
        m = DEFAULT_ENV_RE.match(line.strip())
        if m:
            descr[m.group("env")] = m.group("tail").strip().lstrip(";").strip()
            if not m.group("off"):
                active = m.group("env")
    cp = configparser.ConfigParser(inline_comment_prefixes=(";",), strict=False, interpolation=None)
    try:
        cp.read_string(text)
    except configparser.Error:
        cp = None
    return envs, descr, active, cp


def set_default_env(env: str) -> bool:
    """Activate the 'default_envs = <env>' line in platformio.ini and comment out the others."""
    with open(PIO_INI, "r", encoding="utf-8", newline="") as f:
        lines = f.read().splitlines(keepends=True)
    found, changed = False, False
    for i, ln in enumerate(lines):
        body = ln.rstrip("\r\n")
        m = DEFAULT_ENV_RE.match(body)
        if not m:
            continue
        if m.group("env") == env:
            found = True
            new = body[1:] if m.group("off") else body
        else:
            new = body if m.group("off") else ";" + body
        if new != body:
            lines[i], changed = new + ln[len(body):], True
    if not found:
        return False
    if changed:
        with open(PIO_INI, "w", encoding="utf-8", newline="") as f:
            f.write("".join(lines))
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
        return str(exe), str(base / "scripts")
    w = shutil.which("openocd")
    return (w, None) if w else (None, None)


SERIAL_MONITOR_SCRIPT = r"""
import sys, serial
try:
    p = serial.Serial(sys.argv[1], int(sys.argv[2]), timeout=0.1)
except Exception as e:
    print("Cannot open %s: %s" % (sys.argv[1], e)); sys.exit(1)
print("--- connected to %s @ %s baud ---" % (p.port, p.baudrate), flush=True)
buf = b""
while True:
    try:
        buf += p.read(512)
    except Exception as e:
        print("--- port error: %s ---" % e, flush=True); sys.exit(1)
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        sys.stdout.write(line.decode("utf-8", "replace").rstrip("\r") + "\n")
    sys.stdout.flush()
"""

LIST_PORTS_SCRIPT = ("from serial.tools import list_ports\n"
                     "for p in sorted(list_ports.comports()): print(p.device + '|' + (p.description or ''))")


# ----------------------------------------------------------------------------------------------------------------------
# small widgets
# ----------------------------------------------------------------------------------------------------------------------
class ToolTip:
    def __init__(self, widget, text_fn):
        self.widget, self.text_fn, self.tip, self.after_id = widget, text_fn, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.after_id = self.widget.after(450, self._show)

    def _cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self):
        text = self.text_fn()
        if not text:
            return
        x, y = self.widget.winfo_rootx() + 16, self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=text, justify="left", background="#ffffe6", relief="solid", borderwidth=1,
                 wraplength=620, padx=6, pady=4).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, height=560)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(8, 6))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self.on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def on_wheel(self, e):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        return "break"


# ----------------------------------------------------------------------------------------------------------------------
# main application
# ----------------------------------------------------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Hoverboard Firmware Tool  -  " + str(ROOT))
        self.geometry("1320x900")
        self.minsize(980, 640)

        self.pio = find_pio()
        self.penv_python = find_penv_python()
        self.openocd, self.openocd_scripts = find_openocd()

        self.q = queue.Queue()
        self.task_proc = None
        self.task_title = ""
        self.mon_proc = None
        self.backup_done = False
        self.dirty = False
        self.lines = []
        self.defines = []
        self.sections = []
        self.pages = {}             # section title -> ScrollFrame
        self.section_items = {}     # section title -> [Define]
        self.env_descr_map = {}
        self.pio_cfg = None

        self.env_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.autoscroll = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._build_ui()
        self._load_envs()
        self.load_config()
        self.after(50, self._poll_queue)
        self.after(200, self.refresh_ports)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._log_tool_info()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        style.configure("Name.TLabel", font=("Consolas", 10, "bold"))
        style.configure("Off.TLabel", font=("Consolas", 10), foreground="#8a8a8a")
        style.configure("Desc.TLabel", foreground="#505050")
        style.configure("Ctx.TLabel", foreground="#7a4fb0")
        style.configure("Dirty.TLabel", foreground="#c0392b", font=("Segoe UI", 9, "bold"))
        style.configure("Err.TLabel", foreground="#c0392b")
        style.configure("Hdr.TLabel", foreground="#7a4fb0", font=("Segoe UI", 9, "italic"))
        style.configure("Bad.TEntry", fieldbackground="#ffd6d6")
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
        from tkinter import font as tkfont
        style.configure("Treeview", rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + 10)

        # row 1: variant + config file actions
        top = ttk.Frame(self, padding=(8, 8, 8, 2))
        top.pack(fill="x")
        ttk.Label(top, text="Variant:").pack(side="left")
        self.env_combo = ttk.Combobox(top, textvariable=self.env_var, state="readonly", width=24)
        self.env_combo.pack(side="left", padx=(4, 6))
        self.env_combo.bind("<<ComboboxSelected>>", self.on_env_changed)
        self.env_descr = ttk.Label(top, text="", foreground="#505050")
        self.env_descr.pack(side="left")
        self.btn_reload = ttk.Button(top, text="Reload", command=self.reload_config)
        self.btn_reload.pack(side="right", padx=2)
        self.btn_save = ttk.Button(top, text="Save config", command=self.save_config, style="Accent.TButton")
        self.btn_save.pack(side="right", padx=2)
        self.dirty_label = ttk.Label(top, text="", style="Dirty.TLabel")
        self.dirty_label.pack(side="right", padx=10)

        # row 2: firmware / programmer actions
        acts = ttk.Frame(self, padding=(8, 2, 8, 4))
        acts.pack(fill="x")
        self.btn_build = ttk.Button(acts, text="Build", command=self.do_build)
        self.btn_upload = ttk.Button(acts, text="Upload (ST-Link)", command=self.do_upload, style="Accent.TButton")
        self.btn_clean = ttk.Button(acts, text="Clean", command=self.do_clean)
        self.btn_erase = ttk.Button(acts, text="Full chip erase", command=self.do_erase)
        self.btn_unlock = ttk.Button(acts, text="Unlock chip", command=self.do_unlock)
        self.btn_stop = ttk.Button(acts, text="Stop", command=self.stop_task, state="disabled")
        for b in (self.btn_build, self.btn_upload, self.btn_clean):
            b.pack(side="left", padx=2)
        ttk.Separator(acts, orient="vertical").pack(side="left", fill="y", padx=8)
        for b in (self.btn_erase, self.btn_unlock):
            b.pack(side="left", padx=2)
        ttk.Separator(acts, orient="vertical").pack(side="left", fill="y", padx=8)
        self.btn_stop.pack(side="left", padx=2)
        ttk.Button(acts, text="x", width=2, command=lambda: self.filter_var.set("")).pack(side="right")
        ttk.Entry(acts, textvariable=self.filter_var, width=26).pack(side="right", padx=4)
        ttk.Label(acts, text="Filter options:").pack(side="right")
        self.filter_var.trace_add("write", lambda *_: self.apply_filter())

        ToolTip(self.btn_save, lambda: "Write the changed options to Inc/config.h (a backup config.h.bak is made once).")
        ToolTip(self.btn_build, lambda: "Compile the selected variant (pio run). Config errors (#error) show up here.")
        ToolTip(self.btn_upload, lambda: "Build and flash the selected variant with the ST-Link (pio run -t upload).")
        ToolTip(self.btn_erase, lambda: "Mass-erase the whole flash with OpenOCD via ST-Link. Also wipes the saved "
                                        "input calibration (EEPROM emulation).")
        ToolTip(self.btn_unlock, lambda: "Remove the read-out protection (stm32f1x unlock). Needed once for a stock "
                                         "hoverboard board. Erases the chip. Power-cycle the board afterwards.")

        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(6, 2)).pack(fill="x",
                                                                                                        side="bottom")
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        # config editor: section list on the left, options of the selected section on the right
        content = ttk.Frame(paned)
        paned.add(content, weight=3)
        self.sidebar = ttk.Treeview(content, show="tree", selectmode="browse", takefocus=False)
        self.sidebar.column("#0", width=210, stretch=False)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.bind("<<TreeviewSelect>>", self._on_section_selected)
        self.page_host = ttk.Frame(content)
        self.page_host.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.page_host.rowconfigure(0, weight=1)
        self.page_host.columnconfigure(0, weight=1)

        logf = ttk.Frame(paned)
        paned.add(logf, weight=2)
        lbar = ttk.Frame(logf)
        lbar.pack(fill="x", pady=(4, 2))
        ttk.Label(lbar, text="Serial monitor:").pack(side="left")
        self.port_combo = ttk.Combobox(lbar, textvariable=self.port_var, width=36)
        self.port_combo.pack(side="left", padx=4)
        ttk.Button(lbar, text="Refresh", command=self.refresh_ports).pack(side="left")
        ttk.Combobox(lbar, textvariable=self.baud_var, width=8, values=[b for b, _ in BAUDS]).pack(side="left", padx=4)
        self.btn_mon = ttk.Button(lbar, text="Connect", command=self.toggle_monitor)
        self.btn_mon.pack(side="left")
        ttk.Button(lbar, text="Clear log", command=lambda: self.log_text.delete("1.0", "end")).pack(side="right")
        ttk.Checkbutton(lbar, text="Auto-scroll", variable=self.autoscroll).pack(side="right", padx=6)

        tf = ttk.Frame(logf)
        tf.pack(fill="both", expand=True)
        self.log_text = tk.Text(tf, wrap="none", font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4",
                                insertbackground="#d4d4d4", undo=False, height=8)
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.log_text.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        for tag, color in (("cmd", "#569cd6"), ("info", "#9a9a9a"), ("ok", "#6a9955"), ("err", "#f44747"),
                           ("warn", "#d7ba7d"), ("serial", "#4ec9b0")):
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.tag_configure("cmd", font=("Consolas", 9, "bold"))

    # ------------------------------------------------------------------ logging
    def log(self, text: str, tag=None):
        if tag is None:
            low = text.lower()
            if re.search(r"\berror\b|\bfailed\b|\*\*\*", low):
                tag = "err"
            elif "warning" in low:
                tag = "warn"
            elif "success" in low or "programming finished" in low or "verified ok" in low:
                tag = "ok"
        self.log_text.insert("end", text + "\n", tag or ())
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 6000:
            self.log_text.delete("1.0", "%d.0" % (lines - 5000))
        if self.autoscroll.get():
            self.log_text.see("end")

    def _log_tool_info(self):
        self.log("Project:    %s" % ROOT, "info")
        self.log("PlatformIO: %s" % (self.pio or "NOT FOUND - install the PlatformIO extension / Core"),
                 "info" if self.pio else "err")
        self.log("OpenOCD:    %s" % (self.openocd or "NOT FOUND (installed by PlatformIO on the first ST-Link upload)"),
                 "info" if self.openocd else "warn")

    def _poll_queue(self):
        try:
            for _ in range(500):
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    tag, text = payload
                    self.log(text, "serial" if tag == "mon" else None)
                elif kind == "exit":
                    self._on_proc_exit(*payload)
                elif kind == "ports":
                    self._set_ports(payload)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    # ------------------------------------------------------------------ variants
    def _load_envs(self):
        try:
            envs, self.env_descr_map, active, self.pio_cfg = read_envs()
        except OSError as e:
            messagebox.showerror("platformio.ini", str(e))
            envs, active = [], None
        self.env_combo["values"] = envs
        self.env_var.set(active if active in envs else (envs[0] if envs else ""))
        self._update_env_info()

    def _update_env_info(self):
        env = self.env_var.get()
        self.env_descr.configure(text=self.env_descr_map.get(env, ""))
        if self.pio_cfg is not None and self.pio_cfg.has_section("env:" + env):
            sec = self.pio_cfg["env:" + env]
            if sec.get("monitor_speed"):
                self.baud_var.set(sec.get("monitor_speed").strip())
            if sec.get("monitor_port") and not self.port_var.get():
                self.port_var.set(sec.get("monitor_port").strip())

    def on_env_changed(self, _e=None):
        env = self.env_var.get()
        self._update_env_info()
        try:
            if set_default_env(env):
                self.log("platformio.ini: default_envs = %s" % env, "info")
        except OSError as e:
            self.log("Could not update platformio.ini: %s" % e, "err")
        self._update_sections(select_variant=True)
        self.apply_filter()

    @staticmethod
    def _variant_of_section(title: str):
        m = re.match(r"(VARIANT_\w+)", title)
        return m.group(1) if m else None

    def _section_visible(self, title: str) -> bool:
        env = self.env_var.get()
        known = {self._variant_of_section(t) for t in self.pages} - {None}
        v = self._variant_of_section(title)
        return v is None or v == env or env not in known

    def _update_sections(self, select_variant=False):
        """Show the general sections and only the selected variant's section, in file order."""
        pos = 0
        for title in self.pages:
            if self._section_visible(title):
                self.sidebar.move(title, "", pos)
                pos += 1
            else:
                self.sidebar.detach(title)
        sel = self.sidebar.selection()
        target = None
        if select_variant:
            target = next((t for t in self.pages if self._variant_of_section(t) == self.env_var.get()), None)
        if target is None and (not sel or not self._section_visible(sel[0])):
            target = next((t for t in self.pages if self._section_visible(t)), None)
        if target:
            self.sidebar.selection_set(target)
            self.sidebar.see(target)

    def _on_section_selected(self, _e=None):
        sel = self.sidebar.selection()
        if sel and sel[0] in self.pages:
            self.pages[sel[0]].tkraise()

    # ------------------------------------------------------------------ config.h
    def load_config(self):
        try:
            with open(CONFIG_H, "r", encoding="utf-8", newline="") as f:
                text = f.read()
        except OSError as e:
            messagebox.showerror("config.h", "Cannot read %s:\n%s" % (CONFIG_H, e))
            return
        self.lines, self.defines, self.sections = parse_config(text)
        for page in self.pages.values():
            page.destroy()
        self.sidebar.delete(*self.sidebar.get_children(""))
        self.pages.clear()
        self.section_items.clear()
        for d in self.defines:
            self.section_items.setdefault(d.section, []).append(d)
        for title in self.sections:
            items = self.section_items.get(title)
            if not items:
                continue
            sf = ScrollFrame(self.page_host)
            sf.grid(row=0, column=0, sticky="nsew")
            self.pages[title] = sf
            self.sidebar.insert("", "end", iid=title, text=section_label(title))
            self._build_rows(sf, title, items)
        self._update_sections(select_variant=True)
        self.set_dirty(False)
        self.apply_filter()
        self.status_var.set("Loaded Inc/config.h  (%d options)" % len(self.defines))

    def reload_config(self):
        if self.dirty and not messagebox.askyesno("Reload", "Discard unsaved changes and reload config.h from disk?"):
            return
        self.load_config()
        self.log("config.h reloaded", "info")

    def _build_rows(self, sf: ScrollFrame, title: str, items):
        g = sf.inner
        g.columnconfigure(3, weight=1)
        ttk.Label(g, text=section_label(title), font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=4,
                                                                                  sticky="w")
        # a condition shared by every option of the section is shown once in the header instead of on every row
        common = items[0].context if all(d.context == items[0].context for d in items) else ""
        hdr = "Hover an option for its full description.  Purple [condition] = option is only used when it is met."
        if common:
            hdr = "All options below are only used if: %s.   " % common + hdr
        ttk.Label(g, text=hdr, style="Hdr.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for r, d in enumerate(items, start=2):
            d.show_context = bool(d.context) and d.context != common
            d.widgets, d.value_widgets, d.val_vars = [], [], []
            d.en_var = None
            if d.kind == "flag" or d.was_off:
                d.en_var = tk.BooleanVar(value=d.enabled_in_file)
                cb = ttk.Checkbutton(g, variable=d.en_var, command=lambda d=d: self._on_enable(d))
                cb.grid(row=r, column=0, sticky="w", padx=(0, 4), pady=1)
                d.widgets.append(cb)
            name = ttk.Label(g, text=d.name)
            name.grid(row=r, column=1, sticky="w", padx=(0, 10), pady=1)
            d.widgets.append(name)
            d.name_label = name

            vf = ttk.Frame(g)
            vf.grid(row=r, column=2, sticky="w", padx=(0, 10), pady=1)
            d.widgets.append(vf)
            self._build_value(vf, d, sf)

            descr = d.description
            df = ttk.Frame(g)
            df.grid(row=r, column=3, sticky="we", pady=1)
            if d.show_context:
                ctx = d.context if len(d.context) <= 40 else d.context[:37] + "..."
                ttk.Label(df, text="[%s]  " % ctx, style="Ctx.TLabel").pack(side="left")
            short = descr if len(descr) <= 110 else descr[:107] + "..."
            ttk.Label(df, text=short, style="Desc.TLabel").pack(side="left", fill="x")
            d.widgets.append(df)

            def tip(d=d):
                return "%s\n\n%s%s\n\nconfig.h line %d" % (d.name, d.description or "(no description)",
                                                           ("\n\nOnly used if: " + d.context) if d.context else "",
                                                           d.idx + 1)
            ToolTip(name, tip)
            ToolTip(df, tip)
            self._refresh_row_state(d)

    def _build_value(self, vf, d: Define, sf: ScrollFrame):
        def track(var):
            var.trace_add("write", lambda *_: self.on_value_changed(d))
            d.val_vars.append(var)
            return var

        def no_wheel(w):        # mouse wheel scrolls the page instead of changing combobox values
            w.bind("<MouseWheel>", sf.on_wheel)

        if d.kind == "flag":
            return
        if d.kind == "bool01":
            var = track(tk.StringVar(value=d.value))
            w = ttk.Checkbutton(vf, variable=var, onvalue="1", offvalue="0", text="1 = on")
            w.pack(side="left")
            d.value_widgets.append(w)
        elif d.kind == "enum":
            labels = [lbl for _, lbl in d.options]
            cur = next((lbl for v, lbl in d.options if v == d.value), d.value)
            if cur not in labels:
                labels.append(cur)
            var = track(tk.StringVar(value=cur))
            w = ttk.Combobox(vf, textvariable=var, values=labels, width=28,
                             state="normal" if d.free_text else "readonly")
            w.pack(side="left")
            no_wheel(w)
            d.value_widgets.append(w)
        elif d.kind == "tuple":
            parts = [p.strip() for p in d.value.split(",")]
            cur = next((lbl for v, lbl in INPUT_TYPES if v == parts[0]), parts[0])
            var = track(tk.StringVar(value=cur))
            w = ttk.Combobox(vf, textvariable=var, values=[lbl for _, lbl in INPUT_TYPES], width=15, state="readonly")
            w.pack(side="left")
            no_wheel(w)
            d.value_widgets.append(w)
            for lbl, val in zip(("min", "mid", "max", "dband"), parts[1:]):
                ttk.Label(vf, text=" " + lbl, foreground="#707070").pack(side="left")
                e = ttk.Entry(vf, textvariable=track(tk.StringVar(value=val)), width=6)
                e.pack(side="left")
                d.value_widgets.append(e)
        else:
            width = 12 if d.kind == "number" else max(14, min(40, len(d.value) + 4))
            e = ttk.Entry(vf, textvariable=track(tk.StringVar(value=d.value)), width=width)
            e.pack(side="left")
            d.value_widgets.append(e)
        d.err_label = ttk.Label(vf, text="", style="Err.TLabel")
        d.err_label.pack(side="left", padx=(6, 0))

    def _on_enable(self, d: Define):
        self._refresh_row_state(d)
        self.on_value_changed(d)

    def _refresh_row_state(self, d: Define):
        en = d.en_var.get() if d.en_var is not None else True
        d.name_label.configure(style="Name.TLabel" if en else "Off.TLabel")
        for w in d.value_widgets:
            try:
                if isinstance(w, ttk.Combobox):
                    w.configure(state=("normal" if d.free_text else "readonly") if en else "disabled")
                else:
                    w.state(["!disabled"] if en else ["disabled"])
            except tk.TclError:
                pass

    def current_value(self, d: Define):
        """Returns (enabled, value, error message or None)."""
        en = d.en_var.get() if d.en_var is not None else d.enabled_in_file
        if d.kind == "flag":
            return en, "", None
        raw = [v.get().strip() for v in d.val_vars]
        if d.kind == "bool01":
            return en, raw[0], None
        if d.kind == "enum":
            val = next((v for v, lbl in d.options if lbl == raw[0]), raw[0])
            return en, val, (None if val else "empty value")
        if d.kind == "tuple":
            typ = next((v for v, lbl in INPUT_TYPES if lbl == raw[0]), raw[0])
            fields = [typ] + raw[1:]
            if not all(INT_RE.match(x) for x in fields):
                return en, d.value, "TYPE, MIN, MID, MAX and DEADBAND must be integers"
            orig = [p.strip() for p in d.value.split(",")]
            if [int(x) for x in fields] == [int(x) for x in orig]:
                return en, d.value, None
            return en, ", ".join(fields), None
        if d.kind == "number" and not NUM_RE.match(raw[0]):
            return en, raw[0], "not a number"
        if raw[0] == "":
            return en, raw[0], "empty value"
        return en, raw[0], None

    def on_value_changed(self, d: Define):
        en, _, err = self.current_value(d)
        err = err if en else None
        for w in d.value_widgets:
            if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox):
                w.configure(style="Bad.TEntry" if err else "TEntry")
        if d.err_label is not None:
            d.err_label.configure(text=("\u2716 " + err) if err else "")
        self.set_dirty(any(self._is_changed(x) for x in self.defines))

    def _is_changed(self, d: Define) -> bool:
        en, val, _ = self.current_value(d)
        return en != d.enabled_in_file or val != d.value

    def set_dirty(self, dirty: bool):
        self.dirty = dirty
        n = sum(1 for d in self.defines if self._is_changed(d)) if dirty else 0
        self.dirty_label.configure(text=("\u25cf %d unsaved change%s" % (n, "" if n == 1 else "s")) if dirty else "")

    def apply_filter(self):
        f = self.filter_var.get().strip().lower()
        for title, items in self.section_items.items():
            hits = 0
            for d in items:
                show = not f or f in d.name.lower() or f in d.description.lower() or f in d.context.lower()
                hits += show
                for w in d.widgets:
                    if show:
                        w.grid()
                    else:
                        w.grid_remove()
            if title in self.pages:
                label = section_label(title) + ("   (%d)" % hits if f else "")
                self.sidebar.item(title, text=label, tags=("nohit",) if f and not hits else ())
        self.sidebar.tag_configure("nohit", foreground="#a0a0a0")

    def save_config(self, quiet=False) -> bool:
        errors, changes = [], []
        for d in self.defines:
            en, val, err = self.current_value(d)
            if err and en:
                errors.append("%s (line %d): %s" % (d.name, d.idx + 1, err))
            elif en != d.enabled_in_file or val != d.value:
                changes.append((d, en, val))
        if errors:
            messagebox.showerror("Invalid values", "Fix these values first:\n\n" + "\n".join(errors[:20]))
            return False
        if not changes:
            if not quiet:
                self.log("config.h: nothing to save", "info")
            return True
        try:
            with open(CONFIG_H, "r", encoding="utf-8", newline="") as f:
                disk = f.read()
            if disk.splitlines(keepends=True) != self.lines:
                if not messagebox.askyesno("config.h changed on disk",
                                           "config.h was modified outside this tool since it was loaded.\n"
                                           "Saving now overwrites those external changes.\n\nOverwrite?"):
                    return False
            if not self.backup_done:
                shutil.copy2(CONFIG_H, CONFIG_H.with_suffix(".h.bak"))
                self.backup_done = True
                self.log("Backup written: Inc/config.h.bak", "info")
            lines = list(self.lines)
            for d, en, val in changes:
                body = lines[d.idx].rstrip("\r\n")
                lines[d.idx] = render_define(d, en, val) + lines[d.idx][len(body):]
            with open(CONFIG_H, "w", encoding="utf-8", newline="") as f:
                f.write("".join(lines))
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return False
        self.lines = lines
        for d, en, val in changes:
            self.log("config.h:%-4d %s%s %s" % (d.idx + 1, "" if en else "(disabled) ", d.name, val), "ok")
            if en:
                d.off_prefix = ""
            elif not d.off_prefix:
                d.off_prefix = "// "
            d.value = val
        self.set_dirty(False)
        self.status_var.set("Saved %d change(s) to Inc/config.h" % len(changes))
        return True

    def _ensure_saved(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel("Unsaved changes", "Save config.h changes before continuing?")
        if ans is None:
            return False
        return self.save_config(quiet=True) if ans else True

    # ------------------------------------------------------------------ processes
    def _spawn(self, args, tag, title):
        env = dict(os.environ, PLATFORMIO_DISABLE_COLOR="true", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        shown = [a if len(a) < 120 else "<script>" for a in args]
        self.log("$ " + " ".join('"%s"' % a if " " in a else a for a in shown), "cmd")
        try:
            proc = subprocess.Popen(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, creationflags=NO_WINDOW)
        except OSError as e:
            self.log("Failed to start %s: %s" % (title, e), "err")
            return None
        threading.Thread(target=self._reader, args=(proc, tag, title), daemon=True).start()
        return proc

    def _reader(self, proc, tag, title):
        for raw in iter(proc.stdout.readline, b""):
            self.q.put(("line", (tag, ANSI_RE.sub("", raw.decode("utf-8", "replace")).rstrip("\r\n"))))
        self.q.put(("exit", (tag, title, proc.wait())))

    @staticmethod
    def _kill(proc):
        if proc is not None and proc.poll() is None:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=NO_WINDOW)
            else:
                proc.terminate()

    def _on_proc_exit(self, tag, title, code):
        if tag == "task":
            self.task_proc = None
            ok = code == 0
            self.log("=== %s %s (exit code %d) ===" % (title, "finished" if ok else "FAILED", code), "ok" if ok else "err")
            self.status_var.set("%s %s" % (title, "finished" if ok else "failed"))
            self._set_task_buttons(False)
        else:
            self.mon_proc = None
            self.btn_mon.configure(text="Connect")
            self.log("--- serial monitor closed ---", "info")

    def _set_task_buttons(self, running: bool):
        for b in (self.btn_build, self.btn_upload, self.btn_clean, self.btn_erase, self.btn_unlock, self.btn_reload):
            b.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    def run_task(self, args, title):
        if self.task_proc is not None:
            messagebox.showinfo("Busy", "%s is still running." % self.task_title)
            return
        self.task_proc = self._spawn(args, "task", title)
        if self.task_proc:
            self.task_title = title
            self._set_task_buttons(True)
            self.status_var.set(title + " running...")

    def stop_task(self):
        if self.task_proc is not None:
            self.log("Stopping %s..." % self.task_title, "warn")
            self._kill(self.task_proc)

    def _need_pio(self) -> bool:
        if not self.pio:
            messagebox.showerror("PlatformIO not found", "PlatformIO Core was not found in %s.\n"
                                 "Install the PlatformIO IDE extension in VS Code." % PIO_HOME)
            return False
        if not self.env_var.get():
            messagebox.showerror("No variant", "Select a variant first.")
            return False
        return True

    def do_build(self):
        if self._need_pio() and self._ensure_saved():
            self.run_task([self.pio, "run", "-e", self.env_var.get()], "Build " + self.env_var.get())

    def do_upload(self):
        if self._need_pio() and self._ensure_saved():
            self.run_task([self.pio, "run", "-e", self.env_var.get(), "-t", "upload"], "Upload " + self.env_var.get())

    def do_clean(self):
        if self._need_pio():
            self.run_task([self.pio, "run", "-e", self.env_var.get(), "-t", "clean"], "Clean " + self.env_var.get())

    def _openocd_args(self, commands):
        args = [self.openocd]
        if self.openocd_scripts:
            args += ["-s", self.openocd_scripts]
        args += ["-f", "interface/stlink.cfg", "-f", "target/stm32f1x.cfg"]
        for c in commands:
            args += ["-c", c]
        return args

    def _need_openocd(self) -> bool:
        if not self.openocd:
            messagebox.showerror("OpenOCD not found", "OpenOCD was not found.\nDo one Upload first: PlatformIO "
                                 "installs OpenOCD (tool-openocd) automatically.")
            return False
        return True

    def do_erase(self):
        if not self._need_openocd():
            return
        if not messagebox.askyesno("Full chip erase", "Erase the ENTIRE flash of the STM32 via ST-Link?\n\n"
                                   "The firmware and the saved input calibration will be lost.\n"
                                   "The motors will not work until you upload again."):
            return
        self.run_task(self._openocd_args(["init", "reset halt", "stm32f1x mass_erase 0", "reset halt", "shutdown"]),
                      "Full chip erase")

    def do_unlock(self):
        if not self._need_openocd():
            return
        if not messagebox.askyesno("Unlock", "Remove the read-out protection of the STM32 via ST-Link?\n\n"
                                   "Needed once for a stock hoverboard board. This ERASES the whole chip.\n"
                                   "Power-cycle the board (disconnect the battery) afterwards, then Upload."):
            return
        self.run_task(self._openocd_args(["init", "reset halt", "stm32f1x unlock 0", "reset halt", "shutdown"]),
                      "Unlock")

    # ------------------------------------------------------------------ serial monitor
    def refresh_ports(self):
        if not self.penv_python:
            return

        def work():
            try:
                out = subprocess.run([self.penv_python, "-c", LIST_PORTS_SCRIPT], capture_output=True, timeout=15,
                                     creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
            except (OSError, subprocess.TimeoutExpired):
                out = ""
            self.q.put(("ports", [ln.strip() for ln in out.splitlines() if ln.strip()]))
        threading.Thread(target=work, daemon=True).start()

    def _set_ports(self, ports):
        values = [p.replace("|", "  -  ", 1) for p in ports]
        self.port_combo["values"] = values
        cur = self.port_var.get().split()[0] if self.port_var.get().strip() else ""
        match = next((v for v in values if v.split()[0] == cur), None)
        if match:
            self.port_var.set(match)
        elif values and not cur:
            self.port_var.set(values[0])

    def toggle_monitor(self):
        if self.mon_proc is not None:
            self._kill(self.mon_proc)
            return
        if not self.penv_python:
            messagebox.showerror("Serial monitor", "PlatformIO's Python (with pyserial) was not found.")
            return
        port = self.port_var.get().split()[0] if self.port_var.get().strip() else ""
        baud = self.baud_var.get().strip()
        if not port or not baud.isdigit():
            messagebox.showerror("Serial monitor", "Select a COM port and a numeric baud rate.")
            return
        self.mon_proc = self._spawn([self.penv_python, "-u", "-c", SERIAL_MONITOR_SCRIPT, port, baud], "mon",
                                    "Serial monitor")
        if self.mon_proc:
            self.btn_mon.configure(text="Disconnect")

    # ------------------------------------------------------------------ close
    def on_close(self):
        if self.dirty:
            ans = messagebox.askyesnocancel("Unsaved changes", "Save config.h changes before closing?")
            if ans is None or (ans and not self.save_config(quiet=True)):
                return
        if self.task_proc is not None:
            if not messagebox.askyesno("Task running", "%s is still running. Stop it and close?" % self.task_title):
                return
            self._kill(self.task_proc)
        self._kill(self.mon_proc)
        self.destroy()


def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    if not CONFIG_H.exists() or not PIO_INI.exists():
        print("Run this tool from the firmware project (expected %s and %s)." % (CONFIG_H, PIO_INI))
        sys.exit(1)
    App().mainloop()


if __name__ == "__main__":
    main()
