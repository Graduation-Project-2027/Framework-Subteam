#!/usr/bin/env python3
"""
AMR stack launcher and map library: a small stdlib HTTP service for the GUI.

The GUI's ROS link (rosbridge) only exists while the stack runs, so starting
and stopping the stack, and managing saved maps, cannot go over ROS. This
service is always reachable, so the GUI can do both even when nothing else is
running.

Run it ONCE from a sourced workspace shell (on the Pi 5 for the real robot,
on the laptop for the simulation):

    source install/setup.bash
    python3 src/amr_mission_control/launcher_server.py

Stack (CORS-open):
    GET  /api/status
    GET  /api/logs?after=SEQ
    POST /api/launch?target=real|sim&mode=navigation|mapping&map=NAME&rviz=0|1
    POST /api/stop
    POST /api/rviz?on=1|0

Map library (maps live in src/amr_navigation/maps, or $AMR_MAPS_DIR):
    GET  /api/maps
    GET  /api/maps/NAME/pgm          raw P5 image
    PUT  /api/maps/NAME/pgm          replace the image (edited in the GUI)
    POST /api/maps/save?name=NAME[&overwrite=1]   save the running SLAM map
    POST /api/maps/NAME/rename?to=NEW
    POST /api/maps/NAME/duplicate?to=NEW
    POST /api/maps/NAME/default
    POST /api/maps/NAME/revert      undo all image edits
    POST /api/maps/NAME/delete      move to ~/.ros/amr_map_trash
    POST /api/maps/restore?id=TRASH_ID
    GET  /api/maps/NAME/bundle       zip of the map (fleet map sync)
    PUT  /api/maps/NAME/bundle[?overwrite=1]

Fleet (fleet_server.py on the laptop finds and manages robots):
    GET  /api/robot                  identity + settings of this robot
    POST /api/robot                  JSON body: change settings
    A UDP beacon is broadcast every second on port 8089 ($AMR_DISCOVERY_PORT)
    so the laptop finds this robot without typing addresses.
"""

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import map_bundle

def _env(name, default):
    # The project was renamed AGV -> AMR; still honour the old variable names.
    return os.environ.get(f"AMR_{name}", os.environ.get(f"AGV_{name}", default))


PORT = int(_env("LAUNCHER_PORT", "8088"))
HERE = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = _env("MAPS_DIR", os.path.join(os.path.dirname(HERE), "amr_navigation", "maps"))
STATE_FILE = os.path.expanduser("~/.ros/amr_launcher.json")
ROBOT_FILE = os.path.expanduser("~/.ros/amr_robot.json")
DISCOVERY_PORT = int(_env("DISCOVERY_PORT", "8089"))
ROSBRIDGE_PORT = 9090
TRASH_DIR = os.path.expanduser("~/.ros/amr_map_trash")
LEGACY_FILES = {
    STATE_FILE: os.path.expanduser("~/.ros/agv_launcher.json"),
    TRASH_DIR: os.path.expanduser("~/.ros/agv_map_trash"),
}
MAP_EXTS = (".yaml", ".pgm", ".posegraph", ".data", ".orig.pgm")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_UPLOAD = 64 * 1024 * 1024

RVIZ_CONFIGS = {
    "navigation": ("amr_navigation", "navigation.rviz"),
    "mapping": ("amr_mapping", "mapping.rviz"),
}

_lock = threading.Lock()
_maps_lock = threading.Lock()
_proc = {"popen": None, "target": None, "mode": None, "map": None, "started": None}
_rviz = {"popen": None, "error": None}
_last_exit = {"code": None, "at": None, "mode": None}
_logs = deque(maxlen=600)
_seq = [0]


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ── Persistent launcher state ──────────────────────────────────────────
def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _default_map():
    name = _load_state().get("default_map")
    if name and os.path.exists(_map_path(name, ".yaml")):
        return name
    if os.path.exists(_map_path("Hang1", ".yaml")):
        return "Hang1"
    maps = _list_maps()
    return maps[0]["name"] if maps else None


# ── Logs ───────────────────────────────────────────────────────────────
def _log(text, source="launcher"):
    with _lock:
        _seq[0] += 1
        _logs.append({"seq": _seq[0], "t": time.time(), "source": source, "text": text})
    print(f"[{source}] {text}", flush=True)


def _pump(popen, source):
    for line in iter(popen.stdout.readline, ""):
        line = ANSI_RE.sub("", line.rstrip("\n"))
        if line:
            with _lock:
                _seq[0] += 1
                _logs.append({"seq": _seq[0], "t": time.time(), "source": source, "text": line})
            print(line, flush=True)
    popen.stdout.close()


def _spawn(cmd, source):
    env = dict(os.environ, PYTHONUNBUFFERED="1", RCUTILS_COLORIZED_OUTPUT="0")
    domain = _robot().get("domain_id")
    if domain is not None:
        # One domain per robot keeps robots' ROS graphs apart on shared Wi-Fi.
        env["ROS_DOMAIN_ID"] = str(domain)
    popen = subprocess.Popen(
        ["bash", "-c", cmd], preexec_fn=os.setsid, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
    threading.Thread(target=_pump, args=(popen, source), daemon=True).start()
    return popen


def _kill_group(popen, timeout=12):
    try:
        pgid = os.getpgid(popen.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGINT)   # graceful: ros2 launch shuts its nodes down
    except ProcessLookupError:
        return
    try:
        popen.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGTERM)
            popen.wait(timeout=5)
        except Exception:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass


# ── Stack ──────────────────────────────────────────────────────────────
def _is_running():
    p = _proc["popen"]
    return p is not None and p.poll() is None


def _rviz_running():
    p = _rviz["popen"]
    return p is not None and p.poll() is None


def _watch(popen):
    code = popen.wait()
    with _lock:
        if _proc["popen"] is popen:
            _last_exit.update(code=code, at=time.time(), mode=_proc["mode"])
            _proc.update(popen=None, target=None, mode=None, map=None, started=None)
            stopped_by_user = False
        else:
            stopped_by_user = True
    if not stopped_by_user:
        _log(f"Stack exited with code {code}.")


def _launch(target, mode, map_name, rviz):
    if target not in ("real", "sim"):
        raise ApiError(400, f"Unknown target '{target}'.")
    if mode not in ("navigation", "mapping"):
        raise ApiError(400, f"Unknown mode '{mode}'.")
    sim = " sim:=true" if target == "sim" else ""
    if mode == "navigation":
        map_name = map_name or _default_map()
        if not map_name:
            raise ApiError(400, "No saved map. Start mapping first.")
        _check_name(map_name)
        if not os.path.exists(_map_path(map_name, ".yaml")):
            raise ApiError(404, f"Map '{map_name}' not found.")
        if not os.path.exists(_map_path(map_name, ".posegraph")):
            raise ApiError(400, f"Map '{map_name}' has no pose graph, so the robot can't localize on it.")
        base = shlex.quote(_map_path(map_name, ""))
        nav = f"ros2 launch amr_navigation navigation.launch.py{sim} rviz:=false map_file:={base}"
    else:
        map_name = None
        nav = f"ros2 launch amr_mapping mapping.launch.py{sim} rviz:=false"
    cmd = (f"{nav} & ros2 launch amr_mission_control mission_control.launch.py{sim} "
           "start_gui:=false open_browser:=false; wait")
    with _lock:
        if _is_running():
            raise ApiError(409, "The stack is already running. Stop it first.")
        popen = _spawn(cmd, "stack")
        _proc.update(popen=popen, target=target, mode=mode, map=map_name, started=time.time())
        _last_exit.update(code=None, at=None, mode=None)
    threading.Thread(target=_watch, args=(popen,), daemon=True).start()
    what = f"navigation on {map_name}" if mode == "navigation" else "mapping"
    _log(f"Launching {what} ({'simulation' if target == 'sim' else 'real robot'}).")
    if rviz:
        try:
            _start_rviz()
        except ApiError as e:
            _log(f"RViz not started: {e}")


def _stop():
    _stop_rviz()
    with _lock:
        popen = _proc["popen"]
        _proc.update(popen=None, target=None, mode=None, map=None, started=None)
    if popen is None or popen.poll() is not None:
        return "Nothing was running."
    _log("Stopping the stack…")
    _kill_group(popen)
    _log("Stack stopped.")
    return "Stopped."


def _start_rviz():
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        _rviz["error"] = "This computer has no display. Run RViz on a laptop on the same network."
        raise ApiError(409, _rviz["error"])
    if not _is_running():
        raise ApiError(409, "Start the stack first.")
    if _rviz_running():
        return
    pkg, cfg = RVIZ_CONFIGS[_proc["mode"]]
    sim = " --ros-args -p use_sim_time:=true" if _proc["target"] == "sim" else ""
    cmd = f'rviz2 -d "$(ros2 pkg prefix {pkg})/share/{pkg}/rviz/{cfg}"{sim}'
    _rviz["error"] = None
    _rviz["popen"] = _spawn(cmd, "rviz")
    _log("RViz started.")


def _stop_rviz():
    p = _rviz["popen"]
    _rviz["popen"] = None
    if p is not None and p.poll() is None:
        _kill_group(p, timeout=5)
        _log("RViz closed.")


def _status():
    with _lock:
        running = _is_running()
        return {
            "version": 2,
            "running": running,
            "target": _proc["target"] if running else None,
            "mode": _proc["mode"] if running else None,
            "map": _proc["map"] if running else None,
            "started_at": _proc["started"] if running else None,
            "rviz": {"running": _rviz_running(), "error": _rviz["error"]},
            "display": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
            "last_exit": dict(_last_exit) if _last_exit["code"] is not None else None,
            "default_map": _default_map(),
            "maps_dir": MAPS_DIR,
            "log_seq": _seq[0],
        }


# ── Robot identity and settings (fleet) ────────────────────────────────
# Identity lives in ~/.ros, outside the workspace, so every robot can run an
# identical copy of the repo. A robot without a Robot ID is "unconfigured":
# the fleet lists it under New robots until first-time setup gives it one.
ROBOT_DEFAULTS = {
    "id": None,            # Robot ID, e.g. AMR-03; set at first-time setup
    "name": "",            # optional friendly name
    "domain_id": None,     # ROS_DOMAIN_ID for the stack; None = inherit
    "max_speed": 0.4,      # m/s cap applied when the fleet manager connects
    "priority": 0,         # traffic tie-break: higher goes first
    "notes": "",
}
ROBOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,15}$")
_robot_lock = threading.Lock()


def _machine_key():
    """Stable per-machine key, so the fleet keeps a robot across renames."""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path) as f:
                key = f.read().strip()
            if key:
                return key[:16]
        except OSError:
            pass
    return f"{uuid.getnode():012x}"


def _read_robot_file():
    try:
        with open(ROBOT_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_robot_file(data):
    os.makedirs(os.path.dirname(ROBOT_FILE), exist_ok=True)
    with open(ROBOT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _robot():
    with _robot_lock:
        data = _read_robot_file()
    out = {**ROBOT_DEFAULTS, **data}
    out["configured"] = bool(out["id"])
    out["machine"] = _machine_key()
    return out


def _check_robot_id(rid):
    rid = str(rid or "").strip()
    if not ROBOT_ID_RE.match(rid):
        raise ApiError(400, "Robot ID: 1–16 letters, digits or '-' (e.g. AMR-03).")
    return rid


def _update_robot(body):
    cur = _robot()
    upd = {}
    try:
        if "id" in body:
            upd["id"] = _check_robot_id(body["id"])
        if "name" in body:
            name = str(body["name"]).strip()
            if len(name) > 24:
                raise ApiError(400, "Name must be at most 24 characters.")
            upd["name"] = name
        if "domain_id" in body:
            d = body["domain_id"]
            if d not in (None, ""):
                d = int(d)
                if not 0 <= d <= 101:
                    raise ApiError(400, "ROS domain must be 0–101.")
            upd["domain_id"] = None if d in (None, "") else d
        if "max_speed" in body:
            v = float(body["max_speed"])
            if not 0.1 <= v <= 1.0:
                raise ApiError(400, "Max speed must be 0.1–1.0 m/s.")
            upd["max_speed"] = round(v, 2)
        if "priority" in body:
            pr = int(body["priority"])
            if not -9 <= pr <= 9:
                raise ApiError(400, "Priority must be -9 to 9.")
            upd["priority"] = pr
        if "notes" in body:
            upd["notes"] = str(body["notes"])[:400]
    except (TypeError, ValueError):
        raise ApiError(400, "Settings must be numbers where a number is expected.")
    with _robot_lock:
        data = _read_robot_file()
        data.update(upd)
        _write_robot_file(data)
    if upd.get("id") and upd["id"] != cur["id"]:
        _log(f"Robot ID set to {upd['id']}." if not cur["id"] else f"Robot ID changed {cur['id']} -> {upd['id']}.")
    if "domain_id" in upd and upd["domain_id"] != cur["domain_id"] and _is_running():
        _log("ROS domain changed; it applies the next time the stack starts.")
    return _robot_public()


def _robot_public():
    r = _robot()
    with _lock:
        running = _is_running()
        stack = {"running": running, "mode": _proc["mode"] if running else None,
                 "map": _proc["map"] if running else None,
                 "target": _proc["target"] if running else None}
    return {**r, "host": socket.gethostname(), "launcher": PORT, "rosbridge": ROSBRIDGE_PORT,
            "stack": stack, "default_map": _default_map(), "version": 3}


def _beacon():
    """Announce this robot on the LAN once a second (fleet discovery)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            msg = json.dumps({"leverage": "robot", **_robot_public()}).encode()
            sock.sendto(msg, ("<broadcast>", DISCOVERY_PORT))
        except Exception:
            pass  # no network yet; keep trying
        time.sleep(1.0)


# ── Map library ────────────────────────────────────────────────────────
def _check_name(name):
    if not name or not NAME_RE.match(name):
        raise ApiError(400, "Use 1–40 letters, digits, '-' or '_' (start with a letter or digit).")


def _map_path(name, ext):
    return os.path.join(MAPS_DIR, name + ext)


def _read_yaml(path):
    meta = {}
    with open(path) as f:
        for line in f:
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    origin = [float(x) for x in meta.get("origin", "[0,0,0]").strip("[]").split(",")[:2]]
    return {
        "image": meta.get("image", ""),
        "resolution": float(meta.get("resolution", "0.05")),
        "origin": origin,
        "negate": int(float(meta.get("negate", "0"))),
        "occupied_thresh": float(meta.get("occupied_thresh", "0.65")),
        "free_thresh": float(meta.get("free_thresh", "0.196")),
    }


def _set_yaml_image(path, image):
    with open(path) as f:
        lines = f.readlines()
    with open(path, "w") as f:
        for line in lines:
            f.write(f"image: {image}\n" if line.startswith("image:") else line)


def _pgm_header(data):
    """Return (width, height, maxval, offset) of a binary P5 PGM."""
    if data[:2] != b"P5":
        raise ApiError(400, "Only binary PGM (P5) images are supported.")
    fields, i = [], 2
    while len(fields) < 3:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while data[i:i + 1] not in (b"\n", b""):
                i += 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        fields.append(int(data[i:j]))
        i = j
    return fields[0], fields[1], fields[2], i + 1


def _map_image_path(name):
    yaml_path = _map_path(name, ".yaml")
    img = _read_yaml(yaml_path)["image"] if os.path.exists(yaml_path) else ""
    path = os.path.join(MAPS_DIR, img) if img else _map_path(name, ".pgm")
    return path if os.path.exists(path) else _map_path(name, ".pgm")


def _map_info(name):
    yaml_path = _map_path(name, ".yaml")
    meta = _read_yaml(yaml_path)
    img_path = _map_image_path(name)
    w = h = None
    if os.path.exists(img_path):
        with open(img_path, "rb") as f:
            w, h, _, _ = _pgm_header(f.read(256))
    stamps = [os.path.getmtime(_map_path(name, e)) for e in MAP_EXTS if os.path.exists(_map_path(name, e))]
    res = meta["resolution"]
    return {
        "name": name,
        "width": w,
        "height": h,
        "resolution": res,
        "origin": meta["origin"],
        "negate": meta["negate"],
        "occupied_thresh": meta["occupied_thresh"],
        "free_thresh": meta["free_thresh"],
        "size_m": [round(w * res, 2), round(h * res, 2)] if w else None,
        "has_image": os.path.exists(img_path),
        "has_posegraph": os.path.exists(_map_path(name, ".posegraph")) and os.path.exists(_map_path(name, ".data")),
        "edited": os.path.exists(_map_path(name, ".orig.pgm")),
        "modified": max(stamps) if stamps else None,
        "fingerprint": map_bundle.fingerprint(MAPS_DIR, name),
    }


def _list_maps():
    if not os.path.isdir(MAPS_DIR):
        return []
    out = []
    for f in sorted(os.listdir(MAPS_DIR), key=str.lower):
        if f.endswith(".yaml") and NAME_RE.match(f[:-5]):
            try:
                out.append(_map_info(f[:-5]))
            except Exception as e:  # unreadable map: list it so the user can delete it
                out.append({"name": f[:-5], "error": str(e)})
    return out


def _require_map(name):
    _check_name(name)
    if not os.path.exists(_map_path(name, ".yaml")):
        raise ApiError(404, f"Map '{name}' not found.")


def _require_unused(name, verb):
    if _is_running() and _proc["map"] == name:
        raise ApiError(409, f"'{name}' is in use by the running stack. Stop it before you {verb} it.")


def _save_map(name, overwrite):
    _check_name(name)
    if not _is_running() or _proc["mode"] != "mapping":
        raise ApiError(409, "Start mapping first; only a running SLAM session can be saved.")
    if os.path.exists(_map_path(name, ".yaml")) and not overwrite:
        raise ApiError(409, f"A map called '{name}' already exists.")
    os.makedirs(MAPS_DIR, exist_ok=True)
    base = _map_path(name, "")
    ros_args = ["--ros-args", "-p", "use_sim_time:=true"] if _proc["target"] == "sim" else []
    steps = [
        ("pose graph", ["ros2", "service", "call", "/slam_toolbox/serialize_map",
                        "slam_toolbox/srv/SerializePoseGraph", f"{{filename: '{base}'}}"]),
        ("image", ["ros2", "run", "nav2_map_server", "map_saver_cli", "-f", base, "--fmt", "pgm"] + ros_args),
    ]
    if overwrite and os.path.exists(_map_path(name, ".orig.pgm")):
        os.remove(_map_path(name, ".orig.pgm"))
    for label, cmd in steps:
        _log(f"Saving {label}: {' '.join(shlex.quote(c) for c in cmd)}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise ApiError(504, f"Saving the {label} timed out after 60 s.")
        tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
        for line in tail:
            _log(line, "save")
        if r.returncode != 0:
            raise ApiError(500, f"Saving the {label} failed: {' '.join(tail) or r.returncode}")
    missing = [e for e in (".yaml", ".pgm", ".posegraph", ".data") if not os.path.exists(_map_path(name, e))]
    if missing:
        raise ApiError(500, f"Map saved incompletely, missing {', '.join(missing)}.")
    _log(f"Map '{name}' saved.")
    return _map_info(name)


def _put_pgm(name, body):
    _require_map(name)
    w, h, maxval, off = _pgm_header(body)
    info = _map_info(name)
    if (w, h) != (info["width"], info["height"]) or maxval != 255 or len(body) - off != w * h:
        raise ApiError(400, f"Image must be a {info['width']}x{info['height']} 8-bit PGM.")
    img = _map_image_path(name)
    orig = _map_path(name, ".orig.pgm")
    if not os.path.exists(orig):
        shutil.copy2(img, orig)
    tmp = img + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, img)
    info = _map_info(name)
    if _is_running() and _proc["map"] == name:
        info["reloaded"] = _reload_static_map(name)
    else:
        _log(f"Map '{name}' image edited.")
        info["reloaded"] = False
    return info


def _reload_static_map(name):
    """The stack navigates on this map: have map_server_static load the edited
    image now (nav2 LoadMap), so the planner's /map_static updates live. The
    pose graph is untouched, so localization carries on."""
    yaml_path = _map_path(name, ".yaml")
    cmd = ("ros2 service call /map_server_static/load_map nav2_msgs/srv/LoadMap "
           f"\"{{map_url: '{yaml_path}'}}\"")
    env = dict(os.environ)
    domain = _robot().get("domain_id")
    if domain is not None:
        env["ROS_DOMAIN_ID"] = str(domain)
    try:
        out = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=20)
        ok = out.returncode == 0 and "result=0" in out.stdout.replace(" ", "")
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _log(f"Map '{name}' edited and reloaded live." if ok
         else f"Map '{name}' edited; the live reload failed, so it applies when navigation restarts.")
    return ok


def _revert(name):
    _require_map(name)
    orig = _map_path(name, ".orig.pgm")
    if not os.path.exists(orig):
        raise ApiError(409, f"'{name}' has no edits to revert.")
    os.replace(orig, _map_image_path(name))
    _log(f"Map '{name}' reverted to the saved scan.")
    return _map_info(name)


def _rename(name, new, copy=False):
    _require_map(name)
    _check_name(new)
    if os.path.exists(_map_path(new, ".yaml")):
        raise ApiError(409, f"A map called '{new}' already exists.")
    if not copy:
        _require_unused(name, "rename")
    move = shutil.copy2 if copy else os.replace
    image = _map_image_path(name)
    for ext in (".yaml", ".posegraph", ".data", ".orig.pgm"):
        if os.path.exists(_map_path(name, ext)):
            move(_map_path(name, ext), _map_path(new, ext))
    if os.path.exists(image):
        move(image, _map_path(new, ".pgm"))
    _set_yaml_image(_map_path(new, ".yaml"), new + ".pgm")
    if not copy:
        state = _load_state()
        if state.get("default_map") == name:
            state["default_map"] = new
            _save_state(state)
    _log(f"Map '{name}' {'duplicated as' if copy else 'renamed to'} '{new}'.")
    return _map_info(new)


def _delete(name):
    _require_map(name)
    _require_unused(name, "delete")
    trash_id = f"{name}__{int(time.time())}"
    dest = os.path.join(TRASH_DIR, trash_id)
    os.makedirs(dest, exist_ok=True)
    image = _map_image_path(name)
    for ext in MAP_EXTS:
        src = _map_path(name, ext)
        if os.path.exists(src):
            shutil.move(src, os.path.join(dest, name + ext))
    if os.path.exists(image):
        shutil.move(image, os.path.join(dest, os.path.basename(image)))
    state = _load_state()
    if state.get("default_map") == name:
        state.pop("default_map")
        _save_state(state)
    _log(f"Map '{name}' moved to trash ({dest}).")
    return trash_id


def _restore(trash_id):
    if not trash_id or "/" in trash_id or "\\" in trash_id or ".." in trash_id:
        raise ApiError(400, "Bad trash id.")
    src = os.path.join(TRASH_DIR, trash_id)
    if not os.path.isdir(src):
        raise ApiError(404, "Nothing to restore.")
    name = trash_id.rsplit("__", 1)[0]
    if os.path.exists(_map_path(name, ".yaml")):
        raise ApiError(409, f"A map called '{name}' exists again; rename it first.")
    for f in os.listdir(src):
        shutil.move(os.path.join(src, f), os.path.join(MAPS_DIR, f))
    os.rmdir(src)
    _log(f"Map '{name}' restored.")
    return _map_info(name)


def _set_default(name):
    _require_map(name)
    state = _load_state()
    state["default_map"] = name
    _save_state(state)
    _log(f"Default map set to '{name}'.")


# ── HTTP ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        parts = [unquote(p) for p in path.split("/")[1:]]
        try:
            self._dispatch(method, path, parts, q)
        except ApiError as e:
            self._json(e.code, {"ok": False, "error": str(e)})
        except Exception as e:  # never leave the GUI hanging on a crash
            _log(f"Error handling {method} {path}: {e}")
            self._json(500, {"ok": False, "error": str(e)})

    def _dispatch(self, method, path, parts, q):
        if method == "GET" and path == "/api/status":
            return self._json(200, _status())
        if method == "GET" and path == "/api/logs":
            after = int(q.get("after", "0") or 0)
            with _lock:
                lines = [l for l in _logs if l["seq"] > after]
            return self._json(200, {"lines": lines, "last": _seq[0]})
        if method == "POST" and path == "/api/launch":
            _launch(q.get("target", "real"), q.get("mode", "navigation"), q.get("map"),
                    q.get("rviz") in ("1", "true"))
            return self._json(200, {"ok": True, **_status()})
        if method == "POST" and path == "/api/stop":
            msg = _stop()
            return self._json(200, {"ok": True, "message": msg, **_status()})
        if method == "GET" and path == "/api/robot":
            return self._json(200, _robot_public())
        if method == "POST" and path == "/api/robot":
            return self._json(200, {"ok": True, **_update_robot(self._body_json())})
        if method == "POST" and path == "/api/rviz":
            if q.get("on") in ("1", "true"):
                _start_rviz()
            else:
                _stop_rviz()
            return self._json(200, {"ok": True, **_status()})

        if parts[:2] == ["api", "maps"]:
            with _maps_lock:
                return self._maps(method, parts[2:], q)
        raise ApiError(404, "Not found.")

    def _body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ApiError(413, "Body too large.")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            raise ApiError(400, "Expected a JSON body.")

    def _maps(self, method, rest, q):
        if method == "GET" and not rest:
            return self._json(200, {"maps": _list_maps(), "default": _default_map(),
                                    "in_use": _proc["map"] if _is_running() else None, "dir": MAPS_DIR})
        if method == "POST" and rest == ["save"]:
            return self._json(200, {"ok": True, "map": _save_map(q.get("name", ""), q.get("overwrite") == "1")})
        if method == "POST" and rest == ["restore"]:
            return self._json(200, {"ok": True, "map": _restore(q.get("id", ""))})
        if not rest:
            raise ApiError(404, "Not found.")
        name, action = rest[0], (rest[1] if len(rest) > 1 else "")
        if action == "pgm" and method == "GET":
            _require_map(name)
            with open(_map_image_path(name), "rb") as f:
                body = f.read()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "image/x-portable-graymap")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if action == "bundle" and method == "GET":
            _require_map(name)
            body = map_bundle.pack(MAPS_DIR, name)
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if action == "bundle" and method == "PUT":
            _check_name(name)
            _require_unused(name, "replace")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > map_bundle.MAX_BUNDLE:
                raise ApiError(413, "Bundle too large.")
            try:
                fp = map_bundle.unpack(MAPS_DIR, name, self.rfile.read(length), q.get("overwrite") == "1")
            except FileExistsError as e:
                raise ApiError(409, str(e))
            except ValueError as e:
                raise ApiError(400, str(e))
            _log(f"Map '{name}' received from the fleet ({fp}).")
            return self._json(200, {"ok": True, "map": _map_info(name)})
        if action == "pgm" and method == "PUT":
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                raise ApiError(413, "Image too large.")
            return self._json(200, {"ok": True, "map": _put_pgm(name, self.rfile.read(length))})
        if method == "POST" and action == "rename":
            return self._json(200, {"ok": True, "map": _rename(name, q.get("to", ""))})
        if method == "POST" and action == "duplicate":
            return self._json(200, {"ok": True, "map": _rename(name, q.get("to", ""), copy=True)})
        if method == "POST" and action == "default":
            _set_default(name)
            return self._json(200, {"ok": True})
        if method == "POST" and action == "revert":
            return self._json(200, {"ok": True, "map": _revert(name)})
        if (method == "POST" and action == "delete") or (method == "DELETE" and not action):
            return self._json(200, {"ok": True, "trash_id": _delete(name)})
        raise ApiError(404, "Not found.")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def log_message(self, *args):
        pass  # quiet


def _migrate_legacy():
    for new, old in LEGACY_FILES.items():
        if not os.path.exists(new) and os.path.exists(old):
            try:
                os.rename(old, new)
                _log(f"Migrated {old} -> {new}")
            except OSError as e:
                _log(f"Could not migrate {old}: {e}")


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--set-id":
        data = _read_robot_file()
        data["id"] = _check_robot_id(sys.argv[2])
        _write_robot_file(data)
        print(f"Robot ID set to {data['id']} ({ROBOT_FILE}).")
        return
    _migrate_legacy()
    os.makedirs(MAPS_DIR, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=_beacon, daemon=True).start()
    r = _robot()
    _log(f"AMR launcher on http://0.0.0.0:{PORT} (maps: {MAPS_DIR})")
    if r["configured"]:
        _log(f"Robot {r['id']} announcing on UDP {DISCOVERY_PORT}.")
    else:
        _log(f"First-time setup: no Robot ID yet. Set one from the fleet screen, or run "
             f"'launcher_server.py --set-id AMR-03'. Announcing as new robot on UDP {DISCOVERY_PORT}.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if _is_running() or _rviz_running():
            _stop()


if __name__ == "__main__":
    main()
