#!/usr/bin/env python3
"""
LEVERAGE fleet manager: runs on the laptop only. Robots never talk to each
other directly; this service is the one place that sees all of them.

    python3 src/amr_mission_control/fleet_server.py        # then open the GUI

What it does
  Discovery    Listens for the UDP beacon every launcher_server.py broadcasts
               (port 8089), so robots show up without typing addresses. A new
               robot is only listed until you add it to the fleet; nothing is
               sent to a robot that was not added.
  Awareness    Keeps a rosbridge link to every robot in the fleet and relays
               the others' footprints to each one on /fleet/peers
               (PointCloud2 in that robot's base_footprint frame), which the
               Nav2 fleet_layer marks in both costmaps.
  Traffic      5 Hz: every driving robot claims the next few metres of its
               Nav2 path. When two claims overlap, the lower-priority robot
               is held (mission_manager cancels and later re-sends the same
               goal). Holds and the lease ride on a heartbeat: a robot that
               stops hearing from the laptop holds position.
  Maps         Master map library (~/.ros/amr_fleet/maps); pull a map from a
               robot, push it to others, compare fingerprints. Traffic is only
               compared between robots localized on the same map.
  Layout       One lane network and rack/zone list for the whole fleet, pushed
               to every robot and kept in sync, including rack occupancy.
  Settings     Robot ID (first-time setup), name, ROS domain, speed, priority,
               stored on the robot itself (launcher_server.py /api/robot).

HTTP API (CORS-open, JSON) on port 8090 ($AMR_FLEET_PORT):
    GET  /api/fleet/state
    POST /api/fleet/robots/add?host=IP[&port=8088]
    POST /api/fleet/robots/KEY/adopt | release | forget
    POST /api/fleet/robots/KEY/settings         body: settings JSON
    POST /api/fleet/robots/KEY/command          body: mission command JSON
    POST /api/fleet/robots/KEY/launch?map=NAME[&target=real|sim]   start navigation
    POST /api/fleet/robots/KEY/stop             stop the robot's stack
    POST /api/fleet/command                     body: command for every robot
    POST /api/fleet/tasks                       body: {type, from_id, to_id, robot}
    GET  /api/fleet/layout     PUT /api/fleet/layout (body: layout JSON)
    POST /api/fleet/layout/import?robot=KEY
    GET  /api/fleet/maps
    GET  /api/fleet/maps/NAME/pgm
    POST /api/fleet/maps/pull?robot=KEY&name=NAME[&as=NEW][&overwrite=1]
    POST /api/fleet/maps/push?name=NAME&robots=KEY,KEY[&overwrite=1]
    POST /api/fleet/maps/NAME/delete
    PUT  /api/fleet/maps/NAME/pgm               edited image: library + every robot with that copy (live)
    POST /api/fleet/provision/connect           {host, user, password}: check a fresh Pi
    POST /api/fleet/provision/run               set it up as a robot (see provision.py)
    POST /api/fleet/robots/KEY/deploy           upload + build + restart its launcher
    GET  /api/fleet/jobs/ID?after=N             a setup / deploy job: steps and live log
Anything else is served from gui/dist when it has been built.
"""

import base64
import json
import math
import os
import random
import re
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import map_bundle
import provision

PORT = int(os.environ.get("AMR_FLEET_PORT", "8090"))
DISCOVERY_PORT = int(os.environ.get("AMR_DISCOVERY_PORT", "8089"))
FLEET_DIR = os.path.expanduser(os.environ.get("AMR_FLEET_DIR", "~/.ros/amr_fleet"))
MAPS_DIR = os.path.join(FLEET_DIR, "maps")
REGISTRY_FILE = os.path.join(FLEET_DIR, "fleet.json")
LAYOUT_FILE = os.path.join(FLEET_DIR, "layout.json")
GUI_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui", "dist")
MANAGER = socket.gethostname()

BEACON_LOST = 4.0          # s without a beacon before a robot reads as lost
HEARTBEAT_TTL = 2.0        # s a robot keeps driving without hearing from us
TICK = 0.2                 # traffic controller period
LOOKAHEAD_T = 5.0          # s of path a driving robot claims ...
LOOKAHEAD_MIN, LOOKAHEAD_MAX = 1.2, 4.0   # ... clamped to these metres
CLEARANCE = 0.15           # m kept between claimed footprints
RELEASE_AFTER = 1.0        # s a conflict must stay clear before a hold lifts
STUCK_AFTER = 4.0          # s the robot with right of way may stand still before the order flips
BREAK_FOR = 6.0            # s a hold-loop breaker keeps right of way
ROBOT_R = math.hypot(0.35, 0.32)       # bounding radius, empty
CARRY_R = math.hypot(0.525, 0.525)     # bounding radius, carrying a rack
RACK_SIDE = 0.95                       # rack footprint (square), as the GUI draws it
TRAVEL_STEPS = {"navigating", "navigating_to_rack", "navigating_to_dest", ""}
TRAVEL_STATES = {"RUNNING", "NAVIGATING", "RETURNING"}

_L = threading.RLock()
_robots = {}               # machine key -> Robot
_manual_hosts = set()
_events = deque(maxlen=200)
_traffic = {"conflicts": [], "updated": 0.0}
_flipped = {}              # robot pair -> {"since", "winner", "reported"} (deadlock breaker)
_retreats = {}             # robot pair -> back-off record (head-on on a lane)
_breakers = {}             # robot -> {"until", "from"}: hold-loop right of way
_breaker_failed = {}       # robot -> until: was given right of way and did not move
_no_retreat = {}           # robot pair -> until: a back-off didn't help, don't plan another
_layout = {"rev": 0, "map": None, "lanes": [], "racks": [], "drop_zones": [],
           "lane_corner_radius": 0.6, "sync": True, "source": None, "updated": None}
_library_cache = {"at": 0.0, "maps": []}
_jobs = {}                 # job id -> provision.Job
_ssh_users = {}            # robot machine key / host -> SSH user it was set up with


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def event(text, robot=None, tone="info"):
    with _L:
        _events.append({"t": time.time(), "robot": robot, "text": text, "tone": tone})
    print(f"[fleet] {text}", flush=True)


# ── Minimal rosbridge client (RFC 6455 text frames, JSON protocol) ─────
class WebSocket:
    def __init__(self, host, port, timeout=3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("rosbridge closed during handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("rosbridge refused the WebSocket upgrade")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.send_lock = threading.Lock()
        self.sock.settimeout(None)

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("rosbridge closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.send_lock:
            self.sock.sendall(head + mask + body)

    def send(self, obj):
        self._frame(0x1, json.dumps(obj).encode())

    def recv(self):
        """Next complete text message as a dict (handles ping/fragments)."""
        parts = []
        while True:
            b0, b1 = self._read(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise ConnectionError("rosbridge closed the link")
            if opcode == 0x9:
                self._frame(0xA, data)
                continue
            if opcode in (0x1, 0x2, 0x0):
                parts.append(data)
                if b0 & 0x80:
                    return json.loads(b"".join(parts))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ── Robots ─────────────────────────────────────────────────────────────
class Robot:
    def __init__(self, machine):
        self.machine = machine
        self.info = {}            # latest /api/robot payload (identity + settings)
        self.host = None
        self.seen = 0.0           # last beacon / poll
        self.adopted = False
        self.link = None          # RosLink while adopted
        self.mission = None       # latest /mission_control/state
        self.mission_at = 0.0
        self.plan = []
        self.log = deque(maxlen=60)
        self.maps = []            # robot's map list (with fingerprints)
        self.maps_at = 0.0
        self.hold = None          # {"by": key, "reason": str, "since": t}
        self.clear_since = None
        self.claim = []
        self.moving_since = None
        self.last_pos = None
        self.last_move_at = None
        self.applied_speed = None
        self.last_states = {}     # rack id -> state last reported by this robot
        self.layout_pushed = None  # layout rev pushed while linked
        self.layout_ok = False     # robot's lanes/racks/zones match the fleet layout

    @property
    def label(self):
        return self.info.get("id") or f"New {self.machine[:4]}"

    def url(self, path):
        return f"http://{self.host}:{self.info.get('launcher', 8088)}{path}"

    def online(self):
        return time.time() - self.seen < BEACON_LOST

    def linked(self):
        return bool(self.link and self.link.connected and time.time() - self.mission_at < 2.0)

    def public(self):
        m = self.mission or {}
        hold = None
        if self.hold:
            by = _robots.get(self.hold["by"])
            hold = {"by": self.hold["by"], "by_label": by.label if by else "?",
                    "reason": self.hold["reason"], "since": self.hold["since"]}
            rt = self.hold.get("retreat")
            if rt:
                hold["retreat"] = {"id": rt["id"], "path": [{"x": x, "y": y} for x, y in rt["path"][::2]],
                                   "into": rt["into"]}
        return {
            "key": self.machine,
            "id": self.info.get("id"),
            "label": self.label,
            "configured": bool(self.info.get("id")),
            "name": self.info.get("name", ""),
            "host": self.host,
            "hostname": self.info.get("host"),
            "launcher": self.info.get("launcher", 8088),
            "rosbridge": self.info.get("rosbridge", 9090),
            "settings": {k: self.info.get(k) for k in ("id", "name", "domain_id", "max_speed", "priority", "notes")},
            "stack": self.info.get("stack"),
            "status": ("lost" if self.adopted and not self.online()
                       else "online" if self.online() else "offline"),
            "adopted": self.adopted,
            "linked": self.linked(),
            "link_error": self.link.error if self.link else None,
            "mission": {
                "state": m.get("state"), "pose": m.get("robot_pose"), "step": m.get("current_step"),
                "task": m.get("current_task"), "progress": m.get("progress"),
                "carrying": m.get("carrying"), "speed": m.get("speed"),
                "queue": len(m.get("queue") or []), "fleet": m.get("fleet"),
            } if m else None,
            "plan": self.plan[::4],
            "claim": self.claim,
            "hold": hold,
            "layout_sync": ("off" if not (_layout["sync"] and _layout["rev"]) else
                            "synced" if self.layout_ok and self.linked() else "sending"),
            "log": list(self.log)[-12:],
        }


class RosLink(threading.Thread):
    """rosbridge link to one robot: mission state in, commands + peers out."""

    def __init__(self, robot):
        super().__init__(daemon=True)
        self.robot = robot
        self.ws = None
        self.connected = False
        self.error = None
        self.stopped = False

    def run(self):
        backoff = 1.0
        while not self.stopped:
            r = self.robot
            try:
                self.ws = WebSocket(r.host, r.info.get("rosbridge", 9090))
                for topic, typ, throttle in (
                        ("/mission_control/state", "std_msgs/msg/String", 200),
                        ("/mission_control/feedback", "std_msgs/msg/String", 0),
                        ("/plan", "nav_msgs/msg/Path", 300)):
                    self.ws.send({"op": "subscribe", "topic": topic, "type": typ,
                                  "throttle_rate": throttle, "queue_length": 1})
                self.ws.send({"op": "advertise", "topic": "/mission_control/command", "type": "std_msgs/msg/String"})
                self.ws.send({"op": "advertise", "topic": "/fleet/peers", "type": "sensor_msgs/msg/PointCloud2"})
                self.connected, self.error, backoff = True, None, 1.0
                event(f"{r.label}: linked over rosbridge.", r.machine)
                while not self.stopped:
                    self._on(self.ws.recv())
            except (OSError, ConnectionError, ValueError) as e:
                if self.connected:
                    event(f"{r.label}: rosbridge link lost ({e}).", r.machine, "warn")
                self.error = "rosbridge not reachable; is the robot's stack running?" \
                    if isinstance(e, OSError) else str(e)
            finally:
                self.connected = False
                if self.ws:
                    self.ws.close()
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    def stop(self):
        self.stopped = True
        if self.ws:
            self.ws.close()

    def _on(self, msg):
        if msg.get("op") != "publish":
            return
        topic, body, r = msg.get("topic"), msg.get("msg", {}), self.robot
        if topic == "/mission_control/state":
            try:
                state = json.loads(body.get("data", "{}"))
            except ValueError:
                return
            with _L:
                r.mission, r.mission_at = state, time.time()
        elif topic == "/plan":
            pts = [{"x": round(p["pose"]["position"]["x"], 3), "y": round(p["pose"]["position"]["y"], 3)}
                   for p in body.get("poses", [])]
            with _L:
                r.plan = pts
                r.plan_at = time.time()
        elif topic == "/mission_control/feedback":
            try:
                fb = json.loads(body.get("data", "{}"))
            except ValueError:
                return
            if fb.get("message"):
                with _L:
                    r.log.append(fb)

    def publish(self, topic, msg):
        if not self.connected or not self.ws:
            return False
        try:
            self.ws.send({"op": "publish", "topic": topic, "msg": msg})
            return True
        except OSError:
            return False

    def command(self, cmd):
        return self.publish("/mission_control/command", {"data": json.dumps(cmd)})


# ── Persistence ────────────────────────────────────────────────────────
def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _save_registry():
    with _L:
        data = {"robots": [{"machine": r.machine, "host": r.host, "info": r.info}
                           for r in _robots.values() if r.adopted],
                "manual_hosts": sorted(_manual_hosts),
                "ssh_users": _ssh_users}
    _save_json(REGISTRY_FILE, data)


def _load_registry():
    data = _load_json(REGISTRY_FILE, {})
    for item in data.get("robots", []):
        r = Robot(item["machine"])
        r.host, r.info, r.adopted = item.get("host"), item.get("info", {}), True
        _robots[r.machine] = r
    _manual_hosts.update(data.get("manual_hosts", []))
    _ssh_users.update(data.get("ssh_users", {}))
    _layout.update(_load_json(LAYOUT_FILE, {}))


# ── HTTP to robot launchers ────────────────────────────────────────────
def _http(url, method="GET", body=None, ctype="application/json", timeout=4.0, raw=False):
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:
            msg = None
        raise ApiError(e.code, msg or f"Robot answered {e.code}.")
    except (urllib.error.URLError, OSError):
        raise ApiError(502, "The robot's launcher isn't reachable.")
    return data if raw else json.loads(data or b"{}")


def _get_robot(key):
    r = _robots.get(key)
    if not r:
        raise ApiError(404, "No such robot.")
    return r


def _seen(info, host):
    """A beacon or a poll answered: create or refresh the robot."""
    key = info.get("machine")
    if not key:
        return None
    with _L:
        r = _robots.get(key)
        if r is None:
            r = _robots[key] = Robot(key)
            event(f"Found {info.get('id') or 'a new robot'} at {host}"
                  f"{'' if info.get('id') else ' (needs first-time setup)'}.", key)
        moved = r.host and r.host != host
        r.host, r.seen = host, time.time()
        old_id = r.info.get("id")
        r.info = {k: v for k, v in info.items() if k != "leverage"}
        if r.adopted and (moved or old_id != r.info.get("id")):
            _save_registry()
        if moved and r.link:
            r.link.stop()
            r.link = None   # the supervisor relinks at the new address
    return r


def _discovery():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("", DISCOVERY_PORT))
    while True:
        try:
            data, addr = sock.recvfrom(65536)
            info = json.loads(data)
            if info.get("leverage") == "robot":
                _seen(info, addr[0])
        except (ValueError, OSError):
            continue


def _supervisor():
    """Polls manually added hosts, keeps links up, refreshes robot map lists."""
    while True:
        for host in list(_manual_hosts):
            h, _, port = host.partition(":")
            try:
                _seen(_http(f"http://{h}:{port or 8088}/api/robot", timeout=1.5), h)
            except ApiError:
                pass
        with _L:
            robots = list(_robots.values())
        for r in robots:
            if r.adopted and r.host and r.link is None:
                r.link = RosLink(r)
                r.link.start()
            if r.online() and time.time() - r.maps_at > 15:
                try:
                    r.maps = _http(r.url("/api/maps"), timeout=3).get("maps", [])
                except ApiError:
                    pass
                r.maps_at = time.time()
        time.sleep(2.0)


# ── Traffic control ────────────────────────────────────────────────────
def _radius(r):
    return CARRY_R if (r.mission or {}).get("carrying") else ROBOT_R


def _pose(r):
    p = (r.mission or {}).get("robot_pose")
    return (p["x"], p["y"], p.get("yaw", 0.0)) if p else None


def _driving(r):
    m = r.mission or {}
    return m.get("state") in TRAVEL_STATES and (m.get("current_step") or "") in TRAVEL_STEPS


def _claim(r):
    """Points (0.15 m apart) of the path ahead this robot will drive soon."""
    pose = _pose(r)
    if not pose:
        return []
    pts = [(pose[0], pose[1])]
    plan = r.plan if (_driving(r) and time.time() - getattr(r, "plan_at", 0) < 2.0) else []
    if len(plan) < 2:
        return pts
    speed = float((r.mission or {}).get("speed") or 0.4)
    reach = max(LOOKAHEAD_MIN, min(LOOKAHEAD_MAX, speed * LOOKAHEAD_T))
    k = min(range(len(plan)), key=lambda i: (plan[i]["x"] - pose[0]) ** 2 + (plan[i]["y"] - pose[1]) ** 2)
    acc, last = 0.0, pts[0]
    for p in plan[k:]:
        q = (p["x"], p["y"])
        d = math.hypot(q[0] - last[0], q[1] - last[1])
        if d < 0.15:
            continue
        acc += d
        pts.append(q)
        last = q
        if acc >= reach:
            break
    return pts


def _gap(a_pts, b_pts, bay=None, ra=0.0, rb=0.0):
    """Closest distance between two point sets, as robot centres: compared
    with ra + rb + CLEARANCE. With `bay` (_Bays) and the two bounding radii,
    a robot in a rack bay counts as the square it is there, not its circle."""
    if bay is None:
        return min(math.hypot(p[0] - q[0], p[1] - q[1]) for p in a_pts for q in b_pts)
    ba, bb = [bay(p) for p in a_pts], [bay(q) for q in b_pts]
    return min(bay.gap(p, q, i, j, ra, rb) for p, i in zip(a_pts, ba) for q, j in zip(b_pts, bb))


class _Bays:
    """Rack bays (mirrors gui/src/lib/traffic.js bayIndex): the straight run
    from each site to its entry point, one rack wide. A robot only turns on
    the lane (it squares up at the entry point, then drives straight in or
    out), so inside a bay it is a square aligned with the bay, rack included,
    not the circle its turning sweeps. Robots in neighbouring bays are as far
    apart as the racks are; bounding circles would make neighbouring slots
    exclude each other."""

    def __init__(self, lanes, sites):
        self.bays = []
        for s in sites:
            f = _approach_foot(lanes, s)
            L = math.hypot(f[0] - s["x"], f[1] - s["y"]) if f else 0.0
            if L > 0.2:
                self.bays.append((s, (f[0] - s["x"]) / L, (f[1] - s["y"]) / L, L - 0.1))

    def __call__(self, p):
        for i, (s, ux, uy, end) in enumerate(self.bays):
            dx, dy = p[0] - s["x"], p[1] - s["y"]
            t, n = dx * ux + dy * uy, abs(dy * ux - dx * uy)
            if -RACK_SIDE / 2 <= t <= end and n <= RACK_SIDE / 2:
                return i
        return -1

    def apart(self, i, j):
        a, b = self.bays[i][0], self.bays[j][0]
        return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) >= RACK_SIDE + 0.1

    def _to_square(self, i, p, q, h):
        _, ux, uy, _ = self.bays[i]
        dx, dy = q[0] - p[0], q[1] - p[1]
        along, across = abs(dx * ux + dy * uy), abs(dy * ux - dx * uy)
        return math.hypot(max(0.0, along - h), max(0.0, across - h))

    def gap(self, p, q, i, j, rp, rq):
        """Centre distance equivalent for robots at p (bay i) and q (bay j),
        to compare with rp + rq + CLEARANCE; inf for neighbouring bays. A
        bounding radius r is a square of half side 0.75 r (covers the real
        footprint with and without a rack)."""
        if i >= 0 and j >= 0 and i != j and self.apart(i, j):
            return math.inf
        if i >= 0 > j:
            return self._to_square(i, p, q, 0.75 * rp) + rp
        if j >= 0 > i:
            return self._to_square(j, q, p, 0.75 * rq) + rq
        return math.hypot(p[0] - q[0], p[1] - q[1])


def _map_of(r):
    st = r.info.get("stack") or {}
    if not st.get("running") or st.get("mode") != "navigation":
        return None
    fp = next((m.get("fingerprint") for m in r.maps if m.get("name") == st.get("map")), None)
    return (st.get("map"), fp)


def _lane_graph(lanes, merge=0.5):
    """Nodes + adjacency as mission_manager builds it (points < merge apart are one)."""
    nodes, adj = [], []

    def find(x, y):
        for i, (nx, ny) in enumerate(nodes):
            if (nx - x) ** 2 + (ny - y) ** 2 < merge * merge:
                return i
        nodes.append((x, y))
        adj.append([])
        return len(nodes) - 1

    for lane in lanes:
        prev = None
        for p in lane.get("points", []):
            i = find(float(p["x"]), float(p["y"]))
            if prev is not None and prev != i and prev not in adj[i]:
                adj[i].append(prev)
                adj[prev].append(i)
            prev = i
    return nodes, adj


def _proj(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L < 1e-9 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    fx, fy = ax + t * dx, ay + t * dy
    return fx, fy, math.hypot(px - fx, py - fy)


def _approach_foot(lanes, site):
    """Where a site's face meets the lane: the ray from its centre along the
    approach direction (opposite its yaw), else the closest perpendicular
    foot on that side. Mirrors mission_manager _find_lane_approach_foot."""
    ax, ay = -math.cos(site.get("yaw", 0.0)), -math.sin(site.get("yaw", 0.0))
    hit = None
    for lane in lanes:
        pts = lane.get("points", [])
        for i in range(len(pts) - 1):
            px, py = pts[i]["x"], pts[i]["y"]
            ex, ey = pts[i + 1]["x"] - px, pts[i + 1]["y"] - py
            den = ax * ey - ay * ex
            if abs(den) < 1e-9:
                continue
            t = ((px - site["x"]) * ey - (py - site["y"]) * ex) / den
            u = ((px - site["x"]) * ay - (py - site["y"]) * ax) / den
            if t > 0.05 and -1e-6 <= u <= 1 + 1e-6 and (hit is None or t < hit[2]):
                hit = (site["x"] + ax * t, site["y"] + ay * t, t)
    if hit:
        return hit
    best = None
    for lane in lanes:
        pts = lane.get("points", [])
        for i in range(len(pts) - 1):
            fx, fy, d = _proj(site["x"], site["y"], pts[i]["x"], pts[i]["y"], pts[i + 1]["x"], pts[i + 1]["y"])
            score = d if (fx - site["x"]) * ax + (fy - site["y"]) * ay >= 0 else d + 1000
            if best is None or score < best[2]:
                best = (fx, fy, score)
    return best if best and best[2] < 1000 else None


def _ahead(r):
    """Plan points still ahead of the robot."""
    p = _pose(r)
    if not r.plan or not p:
        return []
    k = min(range(len(r.plan)), key=lambda i: (r.plan[i]["x"] - p[0]) ** 2 + (r.plan[i]["y"] - p[1]) ** 2)
    return [(q["x"], q["y"]) for q in r.plan[k:]]


def _plan_retreat(lanes, lo, hi, reach, bots, sites):
    """How lo gets out of hi's way (mirrors gui/src/lib/traffic.js planRetreat):
    back off along the lane to the nearest point clear of hi and its route;
    failing that, a refuge: a site's lane foot, then straight in (under an
    occupied rack when not carrying, a free location when carrying; never a
    site in another robot's task or with a robot in it)."""
    nodes, adj = _lane_graph(lanes)
    lp, hp = _pose(lo), _pose(hi)
    best = None
    for i, nbrs in enumerate(adj):
        for j in nbrs:
            if j < i:
                continue
            fx, fy, d = _proj(lp[0], lp[1], *nodes[i], *nodes[j])
            if best is None or d < best[3]:
                best = (i, j, (fx, fy), d)
    route = [(hp[0], hp[1])] + _ahead(hi)
    dist0 = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
    if best is None or best[3] > 0.3:
        # In a spur: back up it toward its site, to the first point clear of hi.
        site = None
        for s in sites:
            foot = _approach_foot(lanes, s)
            if foot and _proj(lp[0], lp[1], s["x"], s["y"], foot[0], foot[1])[2] < 0.2:
                site = s
                break
        if site is None or ((lo.mission or {}).get("carrying") and site.get("state") == "occupied"):
            return None
        d = dist0(lp, (site["x"], site["y"]))
        pts, t = [(lp[0], lp[1])], 0.1
        while t <= d:
            p = (lp[0] + (site["x"] - lp[0]) * t / d, lp[1] + (site["y"] - lp[1]) * t / d)
            pts.append(p)
            if dist0(p, hp) > reach + 0.3 and _gap(route, [p]) > reach + 0.1:
                return {"path": pts, "into": None, "spur": True}
            t += 0.1
        if d < 0.3:  # already at the end of its spur: nowhere to back off to
            return None
        return {"path": pts + [(site["x"], site["y"])], "into": None, "spur": True}
    others = [_pose(b) for b in bots if b is not lo and b is not hi]
    dist = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
    blocked = lambda p: dist(p, hp) < reach or any(dist(p, o) < reach for o in others)
    clear = lambda p, m: dist(p, hp) > reach + m and _gap(route, [p]) > reach + 0.1

    reserved = set()
    for b in bots:
        t = (b.mission or {}).get("current_task") or {}
        reserved.update(x for x in ((t.get("from") or {}).get("id"), (t.get("to") or {}).get("id"), t.get("rack_id")) if x)
    carrying = bool((lo.mission or {}).get("carrying"))
    refuges = []
    for s in sites:
        if s.get("id") in reserved or (carrying and s.get("state") == "occupied"):
            continue
        if any(dist(_pose(b), (s["x"], s["y"])) < reach for b in bots if b is not lo):
            continue
        if not clear((s["x"], s["y"]), 0.3):
            continue
        foot = _approach_foot(lanes, s)
        if foot:
            refuges.append((s, (foot[0], foot[1])))

    def walk(toward, came_from, stop):
        pts = [best[2]]
        cur, target, prev, total = best[2], toward, came_from, 0.0
        while total < 12.0:
            n = nodes[target]
            d = dist(cur, n)
            t = 0.1
            while t <= d:
                p = (cur[0] + (n[0] - cur[0]) * t / d, cur[1] + (n[1] - cur[1]) * t / d)
                if blocked(p):
                    return None
                pts.append(p)
                total += 0.1
                hit = stop(p)
                if hit is not None:
                    return pts, hit
                t += 0.1
            cur = n
            nxt = [j for j in adj[target] if j != prev]
            if not nxt:
                return None
            prev, target = target, max(nxt, key=lambda j: dist(nodes[j], hp))
        return None

    def shortest(opts):
        opts = [o for o in opts if o]
        return min(opts, key=lambda o: len(o[0])) if opts else None

    on_lane = lambda p: False if clear(p, 0.3) else None
    into = lambda p: next((s for s, f in refuges if dist(p, f) < 0.08), None)
    got = shortest([walk(best[1], best[0], on_lane), walk(best[0], best[1], on_lane)])
    if got:
        return {"path": got[0], "into": None}
    got = shortest([walk(best[1], best[0], into), walk(best[0], best[1], into)])
    return {"path": got[0], "into": got[1]} if got else None


def _rank(r):
    """Higher goes first: operator priority, carrying, first to start driving."""
    return (int(r.info.get("priority") or 0), bool((r.mission or {}).get("carrying")),
            -(r.moving_since or time.time()), r.label)


def _traffic_tick():
    now = time.time()
    with _L:
        bots = [r for r in _robots.values() if r.adopted and r.linked() and _pose(r)]
        for r in bots:
            r.claim = _claim(r)
            if _driving(r):
                r.moving_since = r.moving_since or now
            else:
                r.moving_since = None
            p = _pose(r)
            if r.last_pos is None or math.hypot(p[0] - r.last_pos[0], p[1] - r.last_pos[1]) > 0.1:
                r.last_pos, r.last_move_at = (p[0], p[1]), now

        go_now = set()  # winners of a flip: their hold lifts without the release delay

        def stuck(r, since=0.0):
            return now - max(r.last_move_at or now, since) > STUCK_AFTER

        conflicts = []
        wants = {}   # yielder key -> (winner, reason, retreat)
        seen = set()
        handled = set()
        lanes = _layout["lanes"] if _layout["rev"] else []
        sites = _layout["racks"] + _layout["drop_zones"]
        bay = _Bays(lanes, sites) if lanes else None
        # Robots backing off stay held until the robot they made way for no
        # longer drives past the spot they left (or past them).
        for pair, rt in list(_retreats.items()):
            lo, hi = _robots.get(rt["loser"]), _robots.get(rt["winner"])
            if lo not in bots or hi not in bots:
                del _retreats[pair]
                continue
            reach = _radius(lo) + _radius(hi) + CLEARANCE
            hp, lp = _pose(hi), _pose(lo)
            ahead = [(hp[0], hp[1])] + _ahead(hi)
            # Backed off and the other still can't get past: the back-off
            # didn't help. Drop it (no new one for this pair for a while) so
            # the deadlock breaker decides instead.
            if not hi.hold and now - rt["at"] > 2 * STUCK_AFTER and stuck(lo, rt["at"]) and stuck(hi, rt["at"]):
                del _retreats[pair]
                _no_retreat[pair] = now + 20
                if lo.hold:
                    lo.hold["retreat"] = None
                event(f"Backing off didn't let {hi.label} past {lo.label}; trying the other way round.",
                      lo.machine, "warn")
            elif _driving(hi) and _gap(ahead, [rt["from"], (lp[0], lp[1])], bay, _radius(hi), _radius(lo)) < reach:
                handled.add(pair)
                seen.add(pair)
                wants[lo.machine] = (hi, f"backing off for {hi.label}", rt)
                conflicts.append({"a": hi.machine, "b": lo.machine, "kind": "yield",
                                  "reason": "backing off to let it pass", "gap": 0.0})
            else:
                del _retreats[pair]
                go_now.add(lo.machine)

        for i, a in enumerate(bots):
            for b in bots[i + 1:]:
                if _map_of(a) != _map_of(b):
                    continue
                reach = _radius(a) + _radius(b) + CLEARANCE
                gap = _gap(a.claim, b.claim, bay, _radius(a), _radius(b))
                if gap >= reach:
                    continue
                da, db = _driving(a), _driving(b)
                if not (da or db):
                    continue
                if tuple(sorted((a.machine, b.machine))) in handled:
                    continue
                if da and db:
                    lo, hi = (a, b) if _rank(a) < _rank(b) else (b, a)
                    reason = "paths cross" if _gap(lo.claim[:1], hi.claim[:1], bay, _radius(lo), _radius(hi)) >= reach else "too close"
                    kind = "yield"
                    pair = tuple(sorted((a.machine, b.machine)))
                    seen.add(pair)

                    def blocks(w, l, reach=reach):  # l's body stands in w's path
                        lp = _pose(l)
                        return len(w.claim) > 1 and _gap(w.claim[1:], [(lp[0], lp[1])], bay, _radius(w), _radius(l)) < reach

                    # In the way goes first: exactly one robot stands in the
                    # other's path (follower behind leader, robot waiting on a
                    # lane the other drives along). Holding the one in the way
                    # would leave the other stuck behind it, and Nav2 would spin
                    # in recovery trying to get past.
                    a_in_way, b_in_way = blocks(b, a), blocks(a, b)
                    if a_in_way != b_in_way:
                        lo, hi = (b, a) if a_in_way else (a, b)
                        reason = f"{hi.label} is in its way"
                    elif a_in_way and b_in_way and lanes and _no_retreat.get(pair, 0) <= now:
                        # Head-on: the lower-ranked robot backs off along the lane.
                        # Whichever of the two has the shorter way out backs off
                        # (ties: the lower-ranked one).
                        plan = _plan_retreat(lanes, lo, hi, reach, bots, sites)
                        other = _plan_retreat(lanes, hi, lo, reach, bots, sites)
                        if other and (not plan or len(other["path"]) < len(plan["path"])):
                            lo, hi, plan = hi, lo, other
                        if plan:
                            lp = _pose(lo)
                            rt = {"loser": lo.machine, "winner": hi.machine, "path": plan["path"],
                                  "into": plan["into"], "id": f"{pair[0]}|{pair[1]}@{now:.1f}",
                                  "from": (lp[0], lp[1]), "at": now}
                            _retreats[pair] = rt
                            where = (f"into {plan['into'].get('name', 'a site')}"
                                     f" ({'under the rack' if plan['into'].get('state') == 'occupied' else 'free slot'})"
                                     if plan["into"] else f"{len(plan['path']) * 0.1:.1f} m back up its spur"
                                     if plan.get("spur") else f"{len(plan['path']) * 0.1:.1f} m along the lane")
                            event(f"{lo.label} backs off {where} to let {hi.label} pass.", lo.machine, "warn")
                            wants[lo.machine] = (hi, f"backing off for {hi.label}", rt)
                            conflicts.append({"a": hi.machine, "b": lo.machine, "kind": "yield",
                                              "reason": "backing off to let it pass", "gap": round(gap, 2)})
                            continue

                    # Deadlock breaker: right of way that goes nowhere flips.
                    # Right of way it doesn't use passes on: it stands still,
                    # blocked by lo, or unheld and blocked by anything else its
                    # costmap sees, while lo's own way is clear of it.
                    flip = _flipped.get(pair)
                    if not flip and stuck(hi, hi.moving_since or 0.0) and (
                            blocks(hi, lo) or (not hi.hold and not blocks(lo, hi))):
                        flip = _flipped[pair] = {"since": now, "winner": lo.machine, "reported": False}
                        event(f"{hi.label} is blocked by {lo.label}; {lo.label} goes first to clear the way.",
                              lo.machine, "warn")
                    if flip:
                        if flip["winner"] == lo.machine:
                            lo, hi = hi, lo
                        go_now.add(hi.machine)
                        reason = "clearing the way"
                        if now - flip["since"] > 2 * STUCK_AFTER and stuck(hi, flip["since"]) and (
                                blocks(hi, lo) or stuck(lo, flip["since"])):
                            kind, reason = "deadlock", "block each other"
                            if not flip["reported"]:
                                flip["reported"] = True
                                event(f"{hi.label} and {lo.label} block each other. Drive one aside from its console.",
                                      hi.machine, "fault")
                            # Never settle on a stand-off: right of way keeps
                            # swapping until one gets moving (or the operator steps in).
                            if now - flip["since"] > 4 * STUCK_AFTER:
                                _flipped[pair] = {"since": now, "winner": lo.machine, "reported": True}
                    # A hold-loop breaker keeps right of way for a few seconds.
                    breaking = lambda r: _breakers.get(r.machine, {}).get("until", 0) > now
                    if breaking(lo) and not breaking(hi) and not stuck(lo, _breakers[lo.machine]["at"]):
                        lo, hi = hi, lo
                        reason = f"{hi.label} breaks a hold loop"
                    wants[lo.machine] = (hi, reason, None)
                    conflicts.append({"a": hi.machine, "b": lo.machine, "kind": kind, "reason": reason,
                                      "gap": round(gap, 2)})
                else:
                    # One robot is parked in the other's way: Nav2 sees it
                    # (fleet_layer + lidar) and plans around it.
                    mover, parked = (a, b) if da else (b, a)
                    # A parked robot never moves on its own: if the mover can't
                    # get past it, tell the operator.
                    stuck_behind = not mover.hold and stuck(mover, mover.moving_since or 0.0)
                    conflicts.append({"a": mover.machine, "b": parked.machine,
                                      "kind": "blocked" if stuck_behind else "pass",
                                      "reason": "stuck behind a parked robot" if stuck_behind else "passing a parked robot",
                                      "gap": round(gap, 2)})
        for pair in [k for k in _flipped if k not in seen]:
            del _flipped[pair]
        for pair in [k for k, t in _no_retreat.items() if t <= now]:
            del _no_retreat[pair]
        # Hold loops (A waits for B, B for C, C for A) never resolve pair by
        # pair: one robot in the loop gets right of way for BREAK_FOR, the
        # highest-ranked whose path is not physically blocked.
        waits_on = {k: w[0].machine for k, w in wants.items()}
        visited = set()
        for start in list(waits_on):
            chain, at, k = [], {}, start
            while k in waits_on and k not in at and k not in visited:
                at[k] = len(chain)
                chain.append(k)
                k = waits_on[k]
            visited.update(chain)
            if k not in at:
                continue
            loop = chain[at[k]:]
            cand = [c for c in loop if wants[c][2] is None]

            def unblocked(c):
                # Its way ahead must be clear of every robot's body, not just
                # the one it waited for, or it gets right of way and can't use it.
                r = _robots[c]
                return len(r.claim) < 2 or all(
                    _gap(r.claim[1:], [_pose(o)[:2]], bay, _radius(r), _radius(o)) >= _radius(r) + _radius(o) + CLEARANCE
                    for o in bots if o is not r)
            cand = [c for c in cand if _breaker_failed.get(c, 0) <= now]
            pool = [c for c in cand if unblocked(c)]
            if not pool:
                continue
            go = max(pool, key=lambda c: _rank(_robots[c]))
            _breakers[go] = {"until": now + BREAK_FOR, "at": now}
            del wants[go]
            go_now.add(go)
            event(f"Hold loop {' → '.join(_robots[c].label for c in loop)}: {_robots[go].label} goes first.",
                  go, "warn")
        for k, b in list(_breakers.items()):
            r = _robots.get(k)
            # Right of way it could not use: try someone else for a while.
            if r and now - b["at"] > STUCK_AFTER and (r.last_move_at or 0) < b["at"] + 0.5:
                _breaker_failed[k] = now + 20.0
                del _breakers[k]
            elif b["until"] <= now:
                del _breakers[k]
        for r in bots:
            want = wants.get(r.machine)
            if want:
                winner, reason, rt = want
                if not r.hold or r.hold["by"] != winner.machine:
                    if rt is None:
                        event(f"{r.label} holds for {winner.label}: {reason}.", r.machine, "warn")
                    r.hold = {"by": winner.machine, "reason": reason, "since": now}
                r.hold["reason"] = reason
                r.hold["retreat"] = rt
                r.clear_since = None
            elif r.hold:
                r.clear_since = r.clear_since or now
                if now - r.clear_since >= RELEASE_AFTER or r.machine in go_now:
                    event(f"{r.label} is clear and continues.", r.machine)
                    r.hold, r.clear_since = None, None
        for r in _robots.values():
            if r.hold and r not in bots:
                r.hold = None
        _traffic.update(conflicts=conflicts, updated=now)
    for r in bots:
        rt = (r.hold or {}).get("retreat")
        beat = {"action": "fleet_heartbeat", "manager": MANAGER, "ttl": HEARTBEAT_TTL,
                "hold": bool(r.hold),
                "by": _robots[r.hold["by"]].label if r.hold else "",
                "reason": r.hold["reason"] if r.hold else ""}
        if rt:
            beat.update(retreat=[[round(x, 3), round(y, 3)] for x, y in rt["path"]], retreat_id=rt["id"],
                        retreat_into=({k: rt["into"][k] for k in ("x", "y", "yaw")} if rt["into"] else None))
        r.link.command(beat)
        _publish_peers(r, [b for b in bots if b is not r and _map_of(b) == _map_of(r)])


def _footprint_points(pose, half_l, half_w, step=0.1):
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    out = []
    nl, nw = max(1, int(2 * half_l / step)), max(1, int(2 * half_w / step))
    for i in range(nl + 1):
        for j in range(nw + 1):
            lx, ly = -half_l + 2 * half_l * i / nl, -half_w + 2 * half_w * j / nw
            out.append((x + c * lx - s * ly, y + s * lx + c * ly))
    return out


FAN = [(13.0 * math.cos(2 * math.pi * k / 720), 13.0 * math.sin(2 * math.pi * k / 720)) for k in range(720)]


def _publish_peers(r, peers):
    """Other robots' footprints (padded 0.1 m) in r's base_footprint frame,
    plus a ring of far points that only clears stale marks."""
    me = _pose(r)
    if not me:
        return
    c, s = math.cos(-me[2]), math.sin(-me[2])
    pts = []
    for p in peers:
        pose = _pose(p)
        carrying = (p.mission or {}).get("carrying")
        hl, hw = (0.625, 0.625) if carrying else (0.45, 0.42)
        for (wx, wy) in _footprint_points(pose, hl, hw):
            dx, dy = wx - me[0], wy - me[1]
            pts.append((c * dx - s * dy, s * dx + c * dy, 0.1))
    pts.extend((fx, fy, 0.1) for fx, fy in FAN)
    data = b"".join(struct.pack("<fff", *p) for p in pts)
    r.link.publish("/fleet/peers", {
        "header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": "base_footprint"},
        "height": 1, "width": len(pts),
        "fields": [{"name": n, "offset": o, "datatype": 7, "count": 1} for n, o in (("x", 0), ("y", 4), ("z", 8))],
        "is_bigendian": False, "point_step": 12, "row_step": 12 * len(pts),
        "data": base64.b64encode(data).decode(), "is_dense": True,
    })


def _traffic_loop():
    while True:
        t0 = time.time()
        try:
            _traffic_tick()
            _layout_tick()
        except Exception as e:  # keep traffic control alive whatever happens
            event(f"Traffic tick failed: {e}", tone="warn")
        time.sleep(max(0.0, TICK - (time.time() - t0)))


# ── Shared layout (lanes, racks, zones) ────────────────────────────────
def _layout_sig(lanes, racks, zones):
    strip = lambda items: [{k: v for k, v in it.items() if k != "state"} for it in items]
    return json.dumps([lanes, strip(racks), strip(zones)], sort_keys=True)


def _layout_tick():
    with _L:
        if not _layout["sync"] or not _layout["rev"]:
            return
        bots = [r for r in _robots.values() if r.adopted and r.linked()]
        want = _layout_sig(_layout["lanes"], _layout["racks"], _layout["drop_zones"])
        states = {it["id"]: it.get("state") for it in _layout["racks"] + _layout["drop_zones"]}
        for r in bots:
            loc = (r.mission or {}).get("locations") or {}
            have = _layout_sig(loc.get("lanes", []), loc.get("racks", []), loc.get("drop_zones", []))
            r.layout_ok = have == want
            if have != want:
                if r.layout_pushed != _layout["rev"]:
                    r.link.command({"action": "update_locations", "racks": _layout["racks"],
                                    "drop_zones": _layout["drop_zones"]})
                    r.link.command({"action": "update_lanes", "lanes": _layout["lanes"]})
                    r.layout_pushed = _layout["rev"]
                    event(f"{r.label}: layout rev {_layout['rev']} sent.", r.machine)
                continue
            # Occupancy: a robot that moved a rack reports the new state first.
            for it in loc.get("racks", []) + loc.get("drop_zones", []):
                sid, st = it.get("id"), it.get("state")
                prev = r.last_states.get(sid)
                r.last_states[sid] = st
                if prev is not None and st != prev and states.get(sid) != st:
                    states[sid] = st
                    for item in _layout["racks"] + _layout["drop_zones"]:
                        if item["id"] == sid:
                            item["state"] = st
                    for o in bots:
                        if o is not r:
                            o.link.command({"action": "set_location_state", "id": sid, "state": st})
                    event(f"{it.get('name', sid)} is now {st} (reported by {r.label}).", r.machine)
                    _save_json(LAYOUT_FILE, _layout)
            # Correct robots that disagree with the fleet's occupancy.
            for it in loc.get("racks", []) + loc.get("drop_zones", []):
                fs = states.get(it.get("id"))
                if fs and it.get("state") != fs and r.last_states.get(it["id"]) == it.get("state"):
                    r.link.command({"action": "set_location_state", "id": it["id"], "state": fs})


def _clean_layout(body):
    def pts(lane):
        return [{"x": float(p["x"]), "y": float(p["y"])} for p in lane.get("points", [])]
    lanes = [{"id": str(l.get("id") or f"lane_{i}"), "name": str(l.get("name", "")), "points": pts(l),
              "oneway": bool(l.get("oneway", False))}
             for i, l in enumerate(body.get("lanes", []))]
    lanes = [l for l in lanes if len(l["points"]) >= 2]

    def site(it, default):
        out = {"id": str(it["id"]), "name": str(it.get("name", "")), "x": float(it["x"]),
               "y": float(it["y"]), "yaw": float(it.get("yaw", 0.0)),
               "state": it.get("state") if it.get("state") in ("free", "occupied") else default}
        return out
    return {"lanes": lanes,
            "racks": [site(r, "occupied") for r in body.get("racks", [])],
            "drop_zones": [site(z, "free") for z in body.get("drop_zones", [])]}


def _set_layout(body, source=None):
    try:
        clean = _clean_layout(body)
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "Layout needs lanes with points, and racks/zones with id, x, y.")
    with _L:
        _layout.update(clean)
        if "map" in body:
            _layout["map"] = body["map"]
        if "sync" in body:
            _layout["sync"] = bool(body["sync"])
        if "lane_corner_radius" in body:
            _layout["lane_corner_radius"] = max(0.0, min(2.0, float(body["lane_corner_radius"])))
        _layout["rev"] += 1
        _layout["source"] = source or "fleet"
        _layout["updated"] = time.time()
        for r in _robots.values():
            r.layout_pushed = None
            r.last_states = {}
        _save_json(LAYOUT_FILE, _layout)
    event(f"Layout rev {_layout['rev']}: {len(clean['lanes'])} lanes, {len(clean['racks'])} racks, "
          f"{len(clean['drop_zones'])} zones.")
    return dict(_layout)


# ── Maps ───────────────────────────────────────────────────────────────
def _library():
    if time.time() - _library_cache["at"] < 2.0:
        return _library_cache["maps"]
    out = []
    if os.path.isdir(MAPS_DIR):
        for f in sorted(os.listdir(MAPS_DIR), key=str.lower):
            name = f[:-5]
            if f.endswith(".yaml") and map_bundle.NAME_RE.match(name):
                p = lambda e: os.path.join(MAPS_DIR, name + e)
                stamps = [os.path.getmtime(p(e)) for e in map_bundle.ALL_EXTS if os.path.exists(p(e))]
                try:
                    meta = map_bundle.read_meta(MAPS_DIR, name)
                except (OSError, ValueError):
                    meta = {}
                out.append({"name": name, "fingerprint": map_bundle.fingerprint(MAPS_DIR, name), **meta,
                            "has_posegraph": os.path.exists(p(".posegraph")) and os.path.exists(p(".data")),
                            "modified": max(stamps) if stamps else None,
                            "bytes": sum(os.path.getsize(p(e)) for e in map_bundle.ALL_EXTS if os.path.exists(p(e)))})
    _library_cache.update(at=time.time(), maps=out)
    return out


def _pull(key, name, as_name=None, overwrite=False):
    r = _get_robot(key)
    data = _http(r.url(f"/api/maps/{quote(name)}/bundle"), raw=True, timeout=30)
    try:
        fp = map_bundle.unpack(MAPS_DIR, as_name or name, data, overwrite)
    except FileExistsError as e:
        raise ApiError(409, str(e))
    except ValueError as e:
        raise ApiError(400, str(e))
    _library_cache["at"] = 0
    event(f"Map {as_name or name} ({fp}) copied from {r.label} to the fleet library.", key)
    return fp


def _push(name, keys, overwrite=False):
    if not os.path.exists(os.path.join(MAPS_DIR, name + ".yaml")):
        raise ApiError(404, f"Map '{name}' is not in the fleet library.")
    data = map_bundle.pack(MAPS_DIR, name)
    results = {}
    for key in keys:
        r = _robots.get(key)
        if not r or not r.online():
            results[key] = {"ok": False, "error": "Robot is offline."}
            continue
        try:
            _http(r.url(f"/api/maps/{quote(name)}/bundle{'?overwrite=1' if overwrite else ''}"),
                  method="PUT", body=data, ctype="application/zip", timeout=60)
            results[key] = {"ok": True}
            r.maps_at = 0
            event(f"Map {name} sent to {r.label}.", key)
        except ApiError as e:
            results[key] = {"ok": False, "error": str(e)}
    return results


def _edit_map(name, body):
    """Replace the library image of a map with an edited one and send it to
    every robot holding the same copy. A robot navigating on it reloads it
    live (launcher_server.py). Robots with a different copy are left alone."""
    if not map_bundle.NAME_RE.match(name) or not os.path.exists(os.path.join(MAPS_DIR, name + ".yaml")):
        raise ApiError(404, f"Map '{name}' is not in the fleet library.")
    if not body.startswith(b"P5"):
        raise ApiError(400, "Expected a binary PGM image.")
    old_fp = map_bundle.fingerprint(MAPS_DIR, name)
    img = os.path.join(MAPS_DIR, name + ".pgm")
    orig = os.path.join(MAPS_DIR, name + ".orig.pgm")
    if not os.path.exists(orig) and os.path.exists(img):
        with open(img, "rb") as src, open(orig, "wb") as dst:
            dst.write(src.read())
    with open(img + ".part", "wb") as f:
        f.write(body)
    os.replace(img + ".part", img)
    _library_cache["at"] = 0
    new_fp = map_bundle.fingerprint(MAPS_DIR, name)
    results = {}
    with _L:
        robots = [r for r in _robots.values() if r.adopted]
    for r in robots:
        have = next((m for m in r.maps if m.get("name") == name), None)
        if not have:
            continue
        if have.get("fingerprint") != old_fp:
            results[r.machine] = {"ok": False, "error": "Has a different copy; send it the full map first."}
            continue
        if not r.online():
            results[r.machine] = {"ok": False, "error": "Robot is offline."}
            continue
        try:
            info = _http(r.url(f"/api/maps/{quote(name)}/pgm"), method="PUT", body=body,
                         ctype="application/octet-stream", timeout=30)
            results[r.machine] = {"ok": True, "live": bool(info.get("map", info).get("reloaded"))}
            r.maps_at = 0
        except ApiError as e:
            results[r.machine] = {"ok": False, "error": str(e)}
    sent = [k for k, v in results.items() if v["ok"]]
    event(f"Map {name} edited ({new_fp}); sent to {len(sent)} robot(s)"
          f"{', applied live on ' + str(sum(1 for k in sent if results[k]['live'])) if sent else ''}.")
    return {"fingerprint": new_fp, "results": results}


# ── Robot setup over SSH (provision.py) ─────────────────────────────────
def _provision_done(job, wait):
    """After setup/deploy: find the robot over HTTP, apply its settings, add it."""
    host = job.result["host"]
    info, t0 = None, time.time()
    while time.time() - t0 < (120 if wait else 30):
        try:
            info = _http(f"http://{host}:8088/api/robot", timeout=2)
            break
        except ApiError:
            time.sleep(2)
    if info is None:
        raise provision.ProvisionError(f"The launcher on {host}:8088 does not answer. "
                                       "Check 'journalctl -u amr-launcher' on the robot.")
    settings = {k: job.result[k] for k in ("name", "domain_id") if job.result.get(k) not in (None, "")}
    if settings:
        info = _http(f"http://{host}:8088/api/robot", method="POST", body=json.dumps(settings).encode())
    r = _seen(info, host)
    with _L:
        if job.result.get("user"):
            _ssh_users[r.machine] = _ssh_users[host] = job.result["user"]
        _manual_hosts.add(f"{host}:8088")
        r.adopted = True
    _save_registry()
    job.result["key"] = r.machine
    job.log(f"{r.label} answers at {host} and is in the fleet.", "step")
    event(f"{r.label} set up over SSH and added to the fleet.", r.machine)


def _start_job(title, steps, target, *args):
    job = provision.Job(title, steps)
    _jobs[job.id] = job
    threading.Thread(target=target, args=(job, *args, _provision_done), daemon=True).start()
    return job


def _provision_body(body):
    host = str(body.get("host", "")).strip()
    user = str(body.get("user", "")).strip() or "ubuntu"
    if not host:
        raise ApiError(400, "Type the Pi's IP address or hostname.")
    if not provision.available():
        raise ApiError(501, "The laptop needs paramiko for robot setup: pip install paramiko")
    return host, user, str(body.get("password", ""))


# ── Tasks ──────────────────────────────────────────────────────────────
def _dispatch(body):
    sites = {s["id"]: s for s in _layout["racks"] + _layout["drop_zones"]}
    typ = body.get("type", "transport")
    src, dst = sites.get(body.get("from_id")), sites.get(body.get("to_id"))
    if not dst or (typ == "transport" and not src):
        raise ApiError(400, "Pick sites from the fleet layout.")
    if typ == "transport":
        if src.get("state") != "occupied":
            raise ApiError(409, f"{src['name']} has no rack to pick up.")
        if dst.get("state") == "occupied":
            raise ApiError(409, f"{dst['name']} is already occupied.")
    target = src if typ == "transport" else dst
    with _L:
        pool = [r for r in _robots.values() if r.adopted and r.linked()
                and (r.info.get("stack") or {}).get("mode") == "navigation"]
        if body.get("robot") not in (None, "", "auto"):
            pool = [r for r in pool if r.machine == body["robot"]]
            if not pool:
                raise ApiError(409, "That robot can't take tasks now (offline or not navigating).")
            chosen, reason = pool[0], "chosen by the operator"
        else:
            cands = []
            for r in pool:
                m = r.mission or {}
                if m.get("carrying") or m.get("state") == "ERROR":
                    continue
                pose = _pose(r)
                dist = math.hypot(pose[0] - target["x"], pose[1] - target["y"])
                busy = m.get("state") not in ("IDLE", "COMPLETED")
                cost = dist + 6.0 * len(m.get("queue") or []) + (8.0 if busy else 0.0)
                cands.append((cost, dist, busy, r))
            if not cands:
                raise ApiError(409, "No robot is free: all are offline, carrying or stopped.")
            cands.sort(key=lambda c: c[0])
            cost, dist, busy, chosen = cands[0]
            reason = f"{'queued behind its current task' if busy else 'idle'}, {dist:.1f} m from {target['name']}"
            if len(cands) > 1:
                reason += f"; next best {cands[1][3].label} ({cands[1][0] - cost:+.1f} m)"
    cmd = {"action": "add_task", "type": typ, "to_name": dst["name"], "to": dst}
    if typ == "transport":
        cmd.update(rack_id=src["id"], from_name=src["name"], **{"from": src})
    chosen.link.command(cmd)
    if (chosen.mission or {}).get("state") in ("IDLE", "COMPLETED"):
        chosen.link.command({"action": "start"})
    event(f"Task {src['name'] + ' → ' if src and typ == 'transport' else ''}{dst['name']} → {chosen.label} ({reason}).",
          chosen.machine)
    return {"robot": chosen.machine, "label": chosen.label, "reason": reason}


# ── Fleet state ────────────────────────────────────────────────────────
def _warnings():
    out = []
    with _L:
        fleet = [r for r in _robots.values() if r.adopted]
        for c in _traffic["conflicts"]:
            if c["kind"] == "blocked":
                la, lb = _robots[c["a"]].label, _robots[c["b"]].label
                out.append({"tone": "caution", "robots": [c["a"], c["b"]],
                            "text": f"{la} is stuck behind parked {lb}. Send {lb} to its dock or move it."})
            if c["kind"] == "deadlock":
                la, lb = _robots[c["a"]].label, _robots[c["b"]].label
                out.append({"tone": "fault", "robots": [c["a"], c["b"]],
                            "text": f"{la} and {lb} block each other. Drive one aside from its console, or stop one task."})
        ids, machines_by_id, domains = {}, {}, {}
        for r in _robots.values():
            if r.info.get("id"):
                machines_by_id.setdefault(r.info["id"].lower(), []).append(r)
        for rid, rs in machines_by_id.items():
            if len(rs) > 1:
                out.append({"tone": "fault", "robots": [r.machine for r in rs],
                            "text": f"{len(rs)} robots use the ID {rs[0].info['id']}. Give each its own ID."})
        for r in fleet:
            d = r.info.get("domain_id")
            domains.setdefault(d, []).append(r)
        for d, rs in domains.items():
            if len(rs) > 1:
                out.append({"tone": "caution", "robots": [r.machine for r in rs],
                            "text": f"{', '.join(r.label for r in rs)} share ROS domain "
                                    f"{'(default)' if d is None else d}; their topics mix on shared Wi-Fi."})
        maps = {}
        for r in fleet:
            mp = _map_of(r)
            if mp:
                maps.setdefault(mp, []).append(r)
        if len(maps) > 1:
            out.append({"tone": "caution", "robots": [r.machine for r in fleet],
                        "text": "Robots run different maps (or different copies of one). Only robots on "
                                "the same map are kept apart; sync the map from the Maps tab."})
        for r in fleet:
            if r.adopted and not r.online():
                out.append({"tone": "caution", "robots": [r.machine], "text": f"{r.label} is not answering."})
            elif r.adopted and r.online() and not r.linked() and (r.info.get("stack") or {}).get("running"):
                out.append({"tone": "caution", "robots": [r.machine],
                            "text": f"{r.label}: stack runs but no rosbridge link yet."})
    return out


def _state():
    with _L:
        robots = sorted((r.public() for r in _robots.values()),
                        key=lambda p: (not p["adopted"], p["label"].lower()))
        return {
            "manager": MANAGER, "t": time.time(),
            "robots": robots,
            "traffic": dict(_traffic),
            "warnings": _warnings(),
            "events": list(_events)[-40:],
            "layout": {k: _layout[k] for k in ("rev", "map", "sync", "source", "updated", "lane_corner_radius")},
        }


# ── HTTP ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")

    def _send(self, code, body, ctype):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _raw(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > map_bundle.MAX_BUNDLE:
            raise ApiError(413, "Image too large.")
        return self.rfile.read(n)

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > 4 * 1024 * 1024:
            raise ApiError(413, "Body too large.")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "Expected a JSON body.")

    def _route(self, method):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [unquote(p) for p in path.split("/")[1:]]
        try:
            if parts[:2] == ["api", "fleet"]:
                return self._api(method, parts[2:], q)
            if method == "GET":
                return self._static(path)
            raise ApiError(404, "Not found.")
        except ApiError as e:
            self._json(e.code, {"ok": False, "error": str(e)})
        except Exception as e:
            event(f"Error handling {method} {path}: {e}", tone="warn")
            self._json(500, {"ok": False, "error": str(e)})

    def _static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.realpath(os.path.join(GUI_DIST, rel))
        if not full.startswith(os.path.realpath(GUI_DIST)) or not os.path.isfile(full):
            full = os.path.join(GUI_DIST, "index.html")
        if not os.path.isfile(full):
            raise ApiError(404, "GUI not built: run 'npm run build' in gui/, or use 'npm run dev'.")
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".png": "image/png",
                 ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
        with open(full, "rb") as f:
            self._send(200, f.read(), types.get(os.path.splitext(full)[1], "application/octet-stream"))

    def _api(self, method, rest, q):
        if method == "GET" and rest == ["state"]:
            return self._json(200, _state())

        if rest[:1] == ["robots"]:
            if method == "POST" and rest[1:] == ["add"]:
                host = q.get("host", "").strip()
                if not host:
                    raise ApiError(400, "Type the robot's IP address or hostname.")
                port = q.get("port") or "8088"
                info = _http(f"http://{host}:{port}/api/robot", timeout=3)
                r = _seen(info, socket.gethostbyname(host))
                _manual_hosts.add(f"{host}:{port}")
                _save_registry()
                return self._json(200, {"ok": True, "robot": r.public()})
            r = _get_robot(rest[1]) if len(rest) > 1 else None
            action = rest[2] if len(rest) > 2 else ""
            if r and method == "POST" and action == "adopt":
                if not r.info.get("id"):
                    raise ApiError(409, "Give the robot an ID first (first-time setup).")
                with _L:
                    clash = [o for o in _robots.values() if o is not r and o.adopted
                             and (o.info.get("id") or "").lower() == r.info["id"].lower()]
                if clash:
                    raise ApiError(409, f"{r.info['id']} is already in the fleet. Change this robot's ID first.")
                r.adopted = True
                _save_registry()
                event(f"{r.label} added to the fleet.", r.machine)
                speed = r.info.get("max_speed")
                return self._json(200, {"ok": True, "speed": speed})
            if r and method == "POST" and action in ("release", "forget"):
                if r.link:
                    r.link.command({"action": "fleet_detach"})
                    r.link.stop()
                    r.link = None
                r.adopted, r.hold, r.mission = False, None, None
                if action == "forget":
                    with _L:
                        _robots.pop(r.machine, None)
                        _manual_hosts.difference_update({h for h in _manual_hosts if h.split(":")[0] == r.host})
                _save_registry()
                event(f"{r.label} removed from the fleet.", r.machine)
                return self._json(200, {"ok": True})
            if r and method == "POST" and action == "settings":
                body = self._body()
                if body.get("id"):
                    with _L:
                        clash = [o for o in _robots.values() if o is not r
                                 and (o.info.get("id") or "").lower() == str(body["id"]).strip().lower()]
                    if clash:
                        raise ApiError(409, f"{clash[0].label} already uses that ID.")
                info = _http(r.url("/api/robot"), method="POST", body=json.dumps(body).encode())
                _seen(info, r.host)
                if "max_speed" in body and r.link:
                    r.link.command({"action": "set_speed", "speed": info.get("max_speed")})
                if r.adopted:
                    _save_registry()
                return self._json(200, {"ok": True, "robot": r.public()})
            if r and method == "POST" and action == "deploy":
                if (r.info.get("stack") or {}).get("running"):
                    raise ApiError(409, f"{r.label}'s stack is running. Stop it first: deploying restarts the launcher.")
                if not provision.available():
                    raise ApiError(501, "The laptop needs paramiko for deploys: pip install paramiko")
                user = _ssh_users.get(r.machine) or _ssh_users.get(r.host) or q.get("user") or "ubuntu"
                job = _start_job(f"Deploy to {r.label}", provision.DEPLOY_STEPS, provision.run_deploy,
                                 r.host, user, {"clean": q.get("clean") == "1"})
                return self._json(200, {"ok": True, "job": job.id})
            if r and method == "POST" and action in ("launch", "stop"):
                if not r.adopted:
                    raise ApiError(409, "Add the robot to the fleet first.")
                if action == "launch":
                    name = q.get("map") or _layout.get("map") or ""
                    target = q.get("target", "real")
                    res = _http(r.url(f"/api/launch?target={quote(target)}&mode=navigation&map={quote(name)}&rviz=0"),
                                method="POST", body=b"", timeout=10)
                    event(f"{r.label}: navigation starting on {name}.", r.machine)
                else:
                    res = _http(r.url("/api/stop"), method="POST", body=b"", timeout=20)
                    event(f"{r.label}: stack stopped.", r.machine)
                r.info["stack"] = {"running": res.get("running"), "mode": res.get("mode"),
                                   "map": res.get("map"), "target": res.get("target")}
                return self._json(200, {"ok": True})
            if r and method == "POST" and action == "command":
                if not r.adopted:
                    raise ApiError(409, "Add the robot to the fleet first.")
                cmd = self._body()
                if not r.link or not r.link.command(cmd):
                    raise ApiError(409, f"{r.label} has no rosbridge link; start its stack first.")
                return self._json(200, {"ok": True})
            raise ApiError(404, "Not found.")

        if method == "POST" and rest == ["command"]:
            cmd = self._body()
            sent = []
            with _L:
                for r in _robots.values():
                    if r.adopted and r.link and r.link.command(cmd):
                        sent.append(r.machine)
            event(f"Fleet command '{cmd.get('action')}' sent to {len(sent)} robot(s).",
                  tone="fault" if cmd.get("action") == "estop" else "info")
            return self._json(200, {"ok": True, "sent": sent})

        if method == "POST" and rest == ["provision", "connect"]:
            host, user, password = _provision_body(self._body())
            try:
                r = provision.Remote(host, user, password)
                try:
                    facts = r.facts()
                finally:
                    r.close()
            except provision.ProvisionError as e:
                raise ApiError(502, str(e))
            return self._json(200, {"ok": True, "facts": facts})
        if method == "POST" and rest == ["provision", "run"]:
            body = self._body()
            host, user, password = _provision_body(body)
            rid = str(body.get("robot_id", "")).strip()
            hostname = str(body.get("hostname", "")).strip().lower()
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9-]{0,15}$", rid):
                raise ApiError(400, "Robot ID: 1–16 letters, digits or '-' (e.g. AMR-03).")
            if not provision.HOST_RE.match(hostname):
                raise ApiError(400, "Hostname: lower-case letters, digits and '-' only.")
            with _L:
                clash = [o for o in _robots.values() if (o.info.get("id") or "").lower() == rid.lower()]
            if clash:
                raise ApiError(409, f"{clash[0].label} already uses that ID.")
            opts = {"robot_id": rid, "hostname": hostname, "new_password": body.get("new_password") or None,
                    "install_deps": body.get("install_deps", True)}
            job = _start_job(f"Set up {rid} at {host}", provision.SETUP_STEPS, provision.run_setup,
                             host, user, password, opts)
            job.result.update(name=body.get("name") or "", domain_id=body.get("domain_id"))
            return self._json(200, {"ok": True, "job": job.id})
        if method == "GET" and rest[:1] == ["jobs"] and len(rest) == 2:
            job = _jobs.get(rest[1])
            if not job:
                raise ApiError(404, "No such job.")
            return self._json(200, job.public(int(q.get("after", "0") or 0)))

        if method == "POST" and rest == ["tasks"]:
            return self._json(200, {"ok": True, **_dispatch(self._body())})

        if rest[:1] == ["layout"]:
            if method == "GET" and len(rest) == 1:
                with _L:
                    return self._json(200, dict(_layout))
            if method == "PUT" and len(rest) == 1:
                return self._json(200, {"ok": True, "layout": _set_layout(self._body())})
            if method == "POST" and rest[1:] == ["import"]:
                r = _get_robot(q.get("robot", ""))
                loc = (r.mission or {}).get("locations")
                if not loc:
                    raise ApiError(409, f"{r.label} has not reported a layout yet.")
                body = {**loc, "map": (_map_of(r) or [None])[0]}
                return self._json(200, {"ok": True, "layout": _set_layout(body, source=r.label)})

        if rest[:1] == ["maps"]:
            if method == "GET" and len(rest) == 1:
                with _L:
                    per = {r.machine: r.maps for r in _robots.values() if r.adopted or r.online()}
                return self._json(200, {"library": _library(), "robots": per, "dir": MAPS_DIR})
            if method == "POST" and rest[1:] == ["pull"]:
                fp = _pull(q.get("robot", ""), q.get("name", ""), q.get("as"), q.get("overwrite") == "1")
                return self._json(200, {"ok": True, "fingerprint": fp})
            if method == "POST" and rest[1:] == ["push"]:
                keys = [k for k in q.get("robots", "").split(",") if k]
                return self._json(200, {"ok": True, "results": _push(q.get("name", ""), keys,
                                                                     q.get("overwrite") == "1")})
            if len(rest) == 3 and rest[2] == "pgm" and method == "GET":
                name = rest[1]
                path = os.path.join(MAPS_DIR, name + ".pgm")
                if not map_bundle.NAME_RE.match(name) or not os.path.exists(path):
                    raise ApiError(404, "Not in the library.")
                with open(path, "rb") as f:
                    return self._send(200, f.read(), "image/x-portable-graymap")
            if len(rest) == 3 and rest[2] == "pgm" and method == "PUT":
                return self._json(200, {"ok": True, **_edit_map(rest[1], self._raw())})
            if len(rest) == 3 and rest[2] == "delete" and method == "POST":
                name = rest[1]
                if not map_bundle.NAME_RE.match(name):
                    raise ApiError(400, "Bad map name.")
                for e in map_bundle.ALL_EXTS:
                    p = os.path.join(MAPS_DIR, name + e)
                    if os.path.exists(p):
                        os.remove(p)
                _library_cache["at"] = 0
                event(f"Map {name} removed from the fleet library.")
                return self._json(200, {"ok": True})
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

    def log_message(self, *args):
        pass


def main():
    os.makedirs(MAPS_DIR, exist_ok=True)
    _load_registry()
    for target in (_discovery, _supervisor, _traffic_loop):
        threading.Thread(target=target, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    event(f"Fleet manager on http://0.0.0.0:{PORT}; listening for robots on UDP {DISCOVERY_PORT}; "
          f"library {MAPS_DIR}.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        # Deliberately no fleet_detach: robots that were driving under traffic
        # control hold position until the manager is back, rather than carry
        # on uncoordinated. Detach a robot from its own console to free it.
        event("Fleet manager stopped; managed robots hold their drives until it returns.")


if __name__ == "__main__":
    main()
