"""
Map bundles: one saved map (yaml + image + slam_toolbox pose graph) as a zip,
plus a content fingerprint, shared by launcher_server.py (each robot) and
fleet_server.py (the laptop's master library) so maps can be synced between
robots.

Two robots only share coordinates when they localize on the same pose graph,
so the fingerprint covers the files localization reads: yaml, image,
.posegraph and .data. Edit history (.orig.pgm) travels with the bundle but is
not part of the fingerprint.
"""

import hashlib
import io
import os
import re
import zipfile

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
CORE_EXTS = (".yaml", ".pgm", ".posegraph", ".data")
ALL_EXTS = CORE_EXTS + (".orig.pgm",)
MAX_BUNDLE = 256 * 1024 * 1024

_cache = {}


def _paths(maps_dir, name, exts):
    return [(e, os.path.join(maps_dir, name + e)) for e in exts]


def fingerprint(maps_dir, name):
    """12-hex content hash of the map, cached on file size + mtime."""
    files = [(e, p) for e, p in _paths(maps_dir, name, CORE_EXTS) if os.path.exists(p)]
    key = (maps_dir, name, tuple((e, os.path.getsize(p), os.path.getmtime(p)) for e, p in files))
    if key in _cache:
        return _cache[key]
    h = hashlib.sha1()
    for e, p in files:
        h.update(e.encode())
        with open(p, "rb") as f:
            if e == ".yaml":
                # The image line differs with the map's name and location only.
                h.update(re.sub(rb"(?m)^image:.*$", b"", f.read()))
                continue
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    digest = h.hexdigest()[:12]
    _cache[key] = digest
    return digest


def read_meta(maps_dir, name):
    """resolution / origin / thresholds from the map's yaml (flat keys only)."""
    meta = {}
    with open(os.path.join(maps_dir, name + ".yaml")) as f:
        for line in f:
            if ":" in line and not line.lstrip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return {
        "resolution": float(meta.get("resolution", "0.05")),
        "origin": [float(x) for x in meta.get("origin", "[0,0,0]").strip("[]").split(",")[:2]],
        "negate": int(float(meta.get("negate", "0"))),
        "occupied_thresh": float(meta.get("occupied_thresh", "0.65")),
        "free_thresh": float(meta.get("free_thresh", "0.196")),
    }


def pack(maps_dir, name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e, p in _paths(maps_dir, name, ALL_EXTS):
            if os.path.exists(p):
                z.write(p, name + e)
    return buf.getvalue()


def unpack(maps_dir, name, data, overwrite=False):
    """Write a bundle as map `name`. Members are renamed to `name`, so a map
    can arrive under a new name; anything else in the zip is ignored.
    Returns the fingerprint. Raises ValueError for a bad bundle."""
    if not NAME_RE.match(name or ""):
        raise ValueError("Use 1–40 letters, digits, '-' or '_' (start with a letter or digit).")
    if len(data) > MAX_BUNDLE:
        raise ValueError("Bundle too large.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a map bundle (expected a zip).")
    members = {}
    for info in z.infolist():
        base = os.path.basename(info.filename)
        for e in sorted(ALL_EXTS, key=len, reverse=True):
            if base.endswith(e) and NAME_RE.match(base[: -len(e)]):
                members.setdefault(e, info)
                break
    missing = [e for e in (".yaml", ".pgm") if e not in members]
    if missing:
        raise ValueError(f"Bundle has no {' or '.join(missing)} file.")
    if os.path.exists(os.path.join(maps_dir, name + ".yaml")) and not overwrite:
        raise FileExistsError(f"A map named '{name}' already exists.")
    os.makedirs(maps_dir, exist_ok=True)
    for e in ALL_EXTS:
        target = os.path.join(maps_dir, name + e)
        if e in members:
            body = z.read(members[e])
            if e == ".yaml":
                # The image line must point at this name's .pgm.
                text = re.sub(r"(?m)^image:.*$", f"image: {name}.pgm", body.decode("utf-8", "replace"))
                body = text.encode()
            tmp = target + ".part"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, target)
        elif os.path.exists(target):
            os.remove(target)  # stale file from the map this replaces
    return fingerprint(maps_dir, name)
