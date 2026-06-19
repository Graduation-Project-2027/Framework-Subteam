#!/usr/bin/env python3
"""
AGV system launcher — tiny stdlib HTTP service the GUI calls to bring the
whole robot stack up or down (simulation or real robot).

Why a separate service: the GUI's ROS connection (rosbridge) only exists once
the stack is launched, so the launch control itself cannot go over ROS.  This
runs independently and is always reachable, so the GUI's "Launch Sim / Launch
Real / Stop" buttons work even when nothing else is running.

Run it ONCE from a sourced workspace shell:

    source install/setup.bash
    python3 src/agv_mission_control/launcher_server.py

Then serve the GUI (npm run dev in the gui/ folder) and use the header buttons.

Endpoints (CORS-open, no preflight needed):
    GET  /api/status              -> {"running": bool, "target": "sim"|"real"|null}
    POST /api/launch?target=sim   -> start the sim stack
    POST /api/launch?target=real  -> start the real stack
    POST /api/stop                -> stop whatever is running
"""

import json
import os
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("AGV_LAUNCHER_PORT", "8088"))

# ── Launch commands ──────────────────────────────────────────────────
# Edit these to match your setup.  Each runs the navigation stack AND the
# mission control (mission_manager + rosbridge); the GUI is NOT started here
# (start_gui:=false) because you're already serving it for these buttons.
COMMANDS = {
    "sim": (
        "ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars & "
        "ros2 launch agv_mission_control mission_control.launch.py "
        "start_gui:=false open_browser:=false use_sim_time:=true; "
        "wait"
    ),
    "real": (
        "ros2 launch agv_navigation navigation.launch.py mode:=2_lidars & "
        "ros2 launch agv_mission_control mission_control.launch.py "
        "start_gui:=false open_browser:=false use_sim_time:=false; "
        "wait"
    ),
}

_lock = threading.Lock()
_proc = {"popen": None, "target": None}


def _is_running():
    p = _proc["popen"]
    return p is not None and p.poll() is None


def _start(target):
    with _lock:
        if _is_running():
            return False, "A system is already running. Stop it first."
        cmd = COMMANDS.get(target)
        if not cmd:
            return False, f"Unknown target '{target}'."
        # New session/process-group so we can signal the whole tree on stop.
        popen = subprocess.Popen(
            ["bash", "-c", cmd],
            preexec_fn=os.setsid,
        )
        _proc["popen"] = popen
        _proc["target"] = target
        print(f"[launcher] started '{target}' (pgid={os.getpgid(popen.pid)})")
        return True, f"Launching {target}…"


def _stop():
    with _lock:
        if not _is_running():
            _proc["popen"] = None
            _proc["target"] = None
            return True, "Nothing was running."
        p = _proc["popen"]
        pgid = os.getpgid(p.pid)
        print(f"[launcher] stopping pgid={pgid}")
        try:
            os.killpg(pgid, signal.SIGINT)   # graceful: ros2 launch shuts nodes
        except ProcessLookupError:
            pass
    # Wait outside the lock; escalate if it won't die.
    try:
        p.wait(timeout=12)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGTERM)
            p.wait(timeout=5)
        except Exception:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
    with _lock:
        _proc["popen"] = None
        _proc["target"] = None
    return True, "Stopped."


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if urlparse(self.path).path == "/api/status":
            self._json(200, {"running": _is_running(), "target": _proc["target"] if _is_running() else None})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/launch":
            target = (parse_qs(parsed.query).get("target") or ["sim"])[0]
            ok, msg = _start(target)
            self._json(200 if ok else 409, {"ok": ok, "message": msg,
                                            "running": _is_running(),
                                            "target": _proc["target"]})
        elif parsed.path == "/api/stop":
            ok, msg = _stop()
            self._json(200, {"ok": ok, "message": msg, "running": False, "target": None})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *args):
        pass  # quiet


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[launcher] AGV system launcher on http://0.0.0.0:{PORT}")
    print("[launcher] endpoints: GET /api/status, POST /api/launch?target=sim|real, POST /api/stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if _is_running():
            _stop()


if __name__ == "__main__":
    main()
