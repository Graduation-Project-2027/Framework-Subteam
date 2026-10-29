"""
Robot provisioning over SSH, run by fleet_server.py on the laptop.

A fresh Raspberry Pi with Ubuntu 24.04 becomes a fleet robot from the GUI:
connect with its address, user and password, then one job

  1. names it: hostname = Robot ID (lower case), with mDNS (avahi) so
     <hostname>.local resolves on the network
  2. installs ROS 2 Jazzy if missing (official ros2-apt-source method)
  3. uploads this workspace (RaspberryPi_ROS2, no build output, LF line
     endings, scripts executable)
  4. runs install_dependencies.sh, then colcon build
  5. sets the Robot ID and installs launcher_server.py as a systemd service
     (amr-launcher) that starts at boot
  6. installs the laptop's SSH key, so later deploys need no password
  7. changes the login password if asked (last, so sudo keeps working)
  8. waits for the robot's beacon

"Deploy" repeats 3-5 for a robot already set up (key login, no password).

Passwords are only held in memory for the job; nothing is written to disk.
Requires paramiko on the laptop:  pip install paramiko
"""

import io
import os
import posixpath
import re
import shlex
import tarfile
import threading
import time
import uuid
from collections import deque

try:
    import paramiko
except ImportError:  # the rest of the fleet server works without it
    paramiko = None

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.normpath(os.path.join(HERE, "..", ".."))       # RaspberryPi_ROS2
KEY_FILE = os.path.expanduser(os.environ.get("AMR_FLEET_KEY", "~/.ros/amr_fleet/id_rsa"))
SERVICE = "amr-launcher"
REMOTE_WS = "RaspberryPi_ROS2"
SKIP_DIRS = {"build", "install", "log", "node_modules", "dist", "__pycache__", ".git", ".vite", "images"}
# Simulation-only assets a robot never loads (the folder stays, so CMake installs fine).
SKIP_PATHS = {"src/amr_description/models/Distribution_Warehouse"}
TEXT_EXT = {".py", ".sh", ".yaml", ".yml", ".xml", ".launch", ".txt", ".cfg", ".md", ".json", ".urdf",
            ".xacro", ".rviz", ".js", ".jsx", ".css", ".html", ".service", ".ini", ".toml", ""}
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

ROS_INSTALL = r"""set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y software-properties-common curl
add-apt-repository -y universe
ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F'"' '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb"
dpkg -i /tmp/ros2-apt-source.deb
apt-get update
apt-get install -y ros-jazzy-ros-base ros-dev-tools
"""

SERVICE_UNIT = """[Unit]
Description=LEVERAGE AMR launcher (stack control, map library, fleet beacon)
After=network-online.target
Wants=network-online.target

[Service]
User={user}
WorkingDirectory={ws}
ExecStart=/bin/bash -lc 'source /opt/ros/jazzy/setup.bash && source install/setup.bash && exec python3 src/amr_mission_control/launcher_server.py'
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


class ProvisionError(Exception):
    pass


def available():
    return paramiko is not None


def laptop_key():
    """The laptop's deploy key (created once), as (paramiko key, public line)."""
    os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
    if os.path.exists(KEY_FILE):
        key = paramiko.RSAKey.from_private_key_file(KEY_FILE)
    else:
        key = paramiko.RSAKey.generate(3072)
        key.write_private_key_file(KEY_FILE)
    return key, f"{key.get_name()} {key.get_base64()} leverage-fleet"


def pack_workspace():
    """tar.gz of the workspace for the robot: sources only, LF endings,
    executable scripts. Returns (bytes, file count)."""
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(WORKSPACE):
            rel_root = os.path.relpath(root, WORKSPACE).replace(os.sep, "/")
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                             and f"{rel_root}/{d}".lstrip("./") not in SKIP_PATHS)
            for name in sorted(files):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, WORKSPACE).replace(os.sep, "/")
                with open(path, "rb") as f:
                    data = f.read()
                ext = os.path.splitext(name)[1].lower()
                if ext in TEXT_EXT and b"\0" not in data[:4096]:
                    data = data.replace(b"\r\n", b"\n")   # a Windows checkout breaks bash on the Pi
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = int(os.path.getmtime(path))
                executable = ext == ".sh" or "/scripts/" in f"/{rel}" or data.startswith(b"#!")
                info.mode = 0o755 if executable else 0o644
                tar.addfile(info, io.BytesIO(data))
                count += 1
    return buf.getvalue(), count


class Job:
    """A provisioning or deploy run: steps, a live log, and a result."""

    def __init__(self, title, steps):
        self.id = uuid.uuid4().hex[:10]
        self.title = title
        self.steps = [{"id": s, "label": label, "state": "todo"} for s, label in steps]
        self.lines = deque(maxlen=4000)
        self.seq = 0
        self.state = "running"
        self.error = None
        self.result = {}
        self.started = time.time()
        self.lock = threading.Lock()

    def log(self, text, source="job"):
        with self.lock:
            for line in str(text).splitlines() or [""]:
                self.seq += 1
                self.lines.append({"seq": self.seq, "t": time.time(), "source": source, "text": line})

    def step(self, sid, state):
        for s in self.steps:
            if s["id"] == sid:
                s["state"] = state
        if state == "now":
            self.log(f"▸ {next(s['label'] for s in self.steps if s['id'] == sid)}", "step")

    def public(self, after=0):
        with self.lock:
            return {"id": self.id, "title": self.title, "state": self.state, "error": self.error,
                    "steps": [dict(s) for s in self.steps], "result": self.result,
                    "started": self.started, "last": self.seq,
                    "lines": [l for l in self.lines if l["seq"] > after]}


class Remote:
    """One SSH session to a robot."""

    def __init__(self, host, user, password=None, key=None, timeout=10):
        if paramiko is None:
            raise ProvisionError("The laptop needs paramiko for robot setup: pip install paramiko")
        self.host, self.user, self.password = host, user, password
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # a fresh Pi has an unknown key
        try:
            self.ssh.connect(host, username=user, password=password, pkey=key, timeout=timeout,
                             banner_timeout=timeout, auth_timeout=timeout,
                             look_for_keys=False, allow_agent=False)
        except paramiko.AuthenticationException:
            raise ProvisionError("Wrong user name or password." if password else
                                 "The laptop's key is not accepted; set the robot up again with its password.")
        except Exception as e:  # noqa: BLE001 - socket errors, timeouts, refused
            raise ProvisionError(f"Couldn't reach {host} over SSH: {e}")

    def close(self):
        try:
            self.ssh.close()
        except Exception:  # noqa: BLE001
            pass

    def run(self, cmd, job=None, sudo=False, stdin=None, timeout=None, quiet=False):
        """Run a shell command, streaming output to the job log. Returns
        (exit code, output)."""
        if sudo:
            cmd = f"sudo -S -p '' bash -c {shlex.quote(cmd)}"
            stdin = (self.password or "") + "\n" + (stdin or "")
        chan = self.ssh.get_transport().open_session()
        chan.set_combine_stderr(True)
        chan.exec_command(cmd)
        if stdin:
            chan.sendall(stdin.encode())
        chan.shutdown_write()
        out, pending, t0 = [], b"", time.time()
        while True:
            if chan.recv_ready():
                pending += chan.recv(65536)
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    text = raw.decode("utf-8", "replace").rstrip("\r")
                    out.append(text)
                    if job and not quiet:
                        job.log(text, "pi")
            elif chan.exit_status_ready():
                break
            elif timeout and time.time() - t0 > timeout:
                chan.close()
                raise ProvisionError(f"Timed out after {timeout} s: {cmd[:60]}")
            else:
                time.sleep(0.05)
        if pending:
            text = pending.decode("utf-8", "replace")
            out.append(text)
            if job and not quiet:
                job.log(text, "pi")
        return chan.recv_exit_status(), "\n".join(out)

    def must(self, cmd, job, what, **kw):
        code, out = self.run(cmd, job, **kw)
        if code != 0:
            raise ProvisionError(f"{what} failed (exit {code}). See the log above.")
        return out

    def facts(self):
        script = ("echo host=$(hostname); . /etc/os-release; echo os=$PRETTY_NAME; echo codename=$VERSION_CODENAME; "
                  "echo arch=$(uname -m); echo home=$HOME; "
                  "test -f /opt/ros/jazzy/setup.bash && echo ros=yes || echo ros=no; "
                  f"test -d ~/{REMOTE_WS}/src && echo ws=yes || echo ws=no; "
                  f"echo service=$(systemctl is-active {SERVICE} 2>/dev/null || true); "
                  "echo mem=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)")
        _, out = self.run(script, quiet=True)
        facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        code, _ = self.run("sudo -S -p '' true", stdin=(self.password or "") + "\n", quiet=True) if self.password else (1, "")
        facts["sudo"] = "yes" if code == 0 else "no"
        return facts

    def upload(self, data, remote_path):
        sftp = self.ssh.open_sftp()
        try:
            sftp.putfo(io.BytesIO(data), remote_path)
        finally:
            sftp.close()


# ── Jobs ───────────────────────────────────────────────────────────────
SETUP_STEPS = [
    ("name", "Hostname and mDNS"),
    ("ros", "ROS 2 Jazzy"),
    ("upload", "Upload the workspace"),
    ("deps", "Install dependencies"),
    ("build", "Build (colcon)"),
    ("identity", "Robot ID and launcher service"),
    ("key", "Laptop key for later deploys"),
    ("password", "New login password"),
    ("check", "Robot answers on the network"),
]
DEPLOY_STEPS = [
    ("upload", "Upload the workspace"),
    ("build", "Build (colcon)"),
    ("restart", "Restart the launcher"),
]


def _upload_and_build(r, job, home, clean=False):
    ws = posixpath.join(home, REMOTE_WS)
    job.step("upload", "now")
    data, count = pack_workspace()
    job.log(f"{count} files, {len(data) / 1e6:.1f} MB from {WORKSPACE}")
    r.upload(data, "/tmp/amr_ws.tgz")
    r.must(f"mkdir -p {ws} && tar xzf /tmp/amr_ws.tgz -C {ws} && rm /tmp/amr_ws.tgz && echo unpacked into {ws}",
           job, "Unpacking the workspace")
    job.step("upload", "done")
    return ws


def _build(r, job, ws, clean=False):
    job.step("build", "now")
    pre = f"rm -rf {ws}/build {ws}/install {ws}/log && " if clean else ""
    # Two workers keep a 4–8 GB Pi from running out of memory on the C++ packages.
    r.must(f"{pre}cd {ws} && source /opt/ros/jazzy/setup.bash && "
           "colcon build --symlink-install --parallel-workers 2 --event-handlers console_direct-",
           job, "colcon build", timeout=3 * 3600)
    job.step("build", "done")


def run_setup(job, host, user, password, opts, on_done):
    """Fresh Pi -> fleet robot. opts: robot_id, hostname, new_password,
    install_ros (auto), install_deps, sim(False)."""
    r = None
    sudoers = "/etc/sudoers.d/amr-provision"
    try:
        r = Remote(host, user, password)
        facts = r.facts()
        if facts.get("sudo") != "yes":
            raise ProvisionError(f"{user} can't use sudo with this password.")
        home = facts.get("home") or f"/home/{user}"
        job.log(f"Connected to {facts.get('host')} ({facts.get('os')}, {facts.get('arch')}).")
        # Unattended installs call sudo many times; allow that for this job only.
        r.must(f"echo '{user} ALL=(ALL) NOPASSWD:ALL' > {sudoers} && chmod 440 {sudoers} && visudo -cf {sudoers}",
               job, "Temporary sudo for the installer", sudo=True, quiet=True)

        hostname = opts["hostname"]
        job.step("name", "now")
        r.must(f"hostnamectl set-hostname {hostname} && "
               f"(grep -q '^127.0.1.1' /etc/hosts && sed -i 's/^127.0.1.1.*/127.0.1.1 {hostname}/' /etc/hosts "
               f"|| echo '127.0.1.1 {hostname}' >> /etc/hosts) && "
               "DEBIAN_FRONTEND=noninteractive apt-get install -y avahi-daemon >/dev/null && systemctl enable --now avahi-daemon "
               f"&& echo 'now {hostname} / {hostname}.local'", job, "Setting the hostname", sudo=True)
        job.step("name", "done")

        job.step("ros", "now")
        if facts.get("ros") == "yes":
            job.log("ROS 2 Jazzy is already installed.")
        else:
            if facts.get("codename") != "noble":
                raise ProvisionError(f"ROS 2 Jazzy needs Ubuntu 24.04 (noble); this Pi runs {facts.get('os')}.")
            r.must(ROS_INSTALL, job, "Installing ROS 2 Jazzy", sudo=True, timeout=3 * 3600)
        job.step("ros", "done")

        ws = _upload_and_build(r, job, home)

        job.step("deps", "now")
        if opts.get("install_deps", True):
            r.must(f"cd {ws} && bash install_dependencies.sh{' --sim' if opts.get('sim') else ''}",
                   job, "install_dependencies.sh", timeout=3 * 3600)
            job.step("deps", "done")
        else:
            job.log("Skipped (already installed).")
            job.step("deps", "skipped")

        _build(r, job, ws, clean=True)

        job.step("identity", "now")
        rid = opts["robot_id"]
        r.must(f"cd {ws} && python3 src/amr_mission_control/launcher_server.py --set-id {shlex.quote(rid)}",
               job, "Setting the Robot ID")
        unit = SERVICE_UNIT.format(user=user, ws=ws)
        rules = (f"{user} ALL=(root) NOPASSWD: /usr/bin/systemctl restart {SERVICE}, "
                 f"/usr/bin/systemctl stop {SERVICE}, /usr/bin/systemctl start {SERVICE}")
        r.must(f"cat > /etc/systemd/system/{SERVICE}.service && "
               f"echo '{rules}' > /etc/sudoers.d/{SERVICE} && chmod 440 /etc/sudoers.d/{SERVICE} && "
               f"visudo -cf /etc/sudoers.d/{SERVICE} && systemctl daemon-reload && "
               f"systemctl enable {SERVICE} && systemctl restart {SERVICE} && systemctl is-active {SERVICE}",
               job, "Installing the launcher service", sudo=True, stdin=unit)
        job.step("identity", "done")

        job.step("key", "now")
        _, pub = laptop_key()
        r.must(f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qF {shlex.quote(pub)} ~/.ssh/authorized_keys || echo {shlex.quote(pub)} >> ~/.ssh/authorized_keys) "
               f"&& chmod 600 ~/.ssh/authorized_keys && echo key installed", job, "Installing the laptop key")
        job.step("key", "done")

        job.step("password", "now")
        if opts.get("new_password"):
            r.must("chpasswd", job, "Changing the password", sudo=True,
                   stdin=f"{user}:{opts['new_password']}\n", quiet=True)
            job.log("Password changed. Use the new one from now on.")
            job.step("password", "done")
        else:
            job.log("Kept the current password.")
            job.step("password", "skipped")

        job.step("check", "now")
        job.result = {"host": host, "hostname": hostname, "user": user, "robot_id": rid}
        on_done(job, wait=True)
        job.step("check", "done")
        job.state = "done"
        job.log(f"{rid} is set up. It appears in the fleet as {hostname} ({hostname}.local).", "step")
    except ProvisionError as e:
        _fail(job, str(e))
    except Exception as e:  # noqa: BLE001 - never leave a job hanging
        _fail(job, f"Unexpected error: {e}")
    finally:
        if r is not None:
            try:
                r.run(f"rm -f {sudoers}", sudo=True, quiet=True)
            except Exception:  # noqa: BLE001
                pass
            r.close()


def run_deploy(job, host, user, opts, on_done):
    """Robot already set up: upload, build, restart the launcher (key login)."""
    r = None
    try:
        key, _ = laptop_key()
        r = Remote(host, user, key=key)
        _, home = r.run("echo $HOME", quiet=True)
        ws = _upload_and_build(r, job, home.strip())
        _build(r, job, ws, clean=opts.get("clean", False))
        job.step("restart", "now")
        r.must(f"sudo -n /usr/bin/systemctl restart {SERVICE} && sleep 2 && systemctl is-active {SERVICE}",
               job, "Restarting the launcher")
        job.step("restart", "done")
        job.state = "done"
        job.result = {"host": host}
        on_done(job, wait=False)
        job.log("Deployed. The launcher restarted with the new code.", "step")
    except ProvisionError as e:
        _fail(job, str(e))
    except Exception as e:  # noqa: BLE001
        _fail(job, f"Unexpected error: {e}")
    finally:
        if r is not None:
            r.close()


def _fail(job, message):
    job.state = "failed"
    job.error = message
    for s in job.steps:
        if s["state"] == "now":
            s["state"] = "failed"
    job.log(f"✗ {message}", "step")
