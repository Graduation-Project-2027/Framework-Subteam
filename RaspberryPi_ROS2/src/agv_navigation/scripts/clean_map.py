#!/usr/bin/env python3
"""Clean rack legs out of a SLAM map.

Reads <map>.pgm + <map>.yaml plus a locations.json, paints a free-space
square at every rack/drop_zone position, and writes <map>_clean.pgm.

Run this whenever you add or move rack positions; the resulting clean
map has only walls and fixed structures, while rack obstacles are added
dynamically at runtime via the /rack_obstacles PointCloud2 topic.

Usage:
    clean_map.py <map_yaml> <locations_json> [--side 1.0] [--out <pgm>]
"""

import argparse
import json
import os
import sys

import numpy as np
import yaml
from PIL import Image


def load_pgm(path):
    return np.array(Image.open(path))


def save_pgm(arr, path):
    Image.fromarray(arr.astype(np.uint8), mode="L").save(path)


def world_to_px(wx, wy, origin, resolution, height):
    """Convert world (x, y) to pixel (col, row) in the PGM image."""
    col = int(round((wx - origin[0]) / resolution))
    row = height - 1 - int(round((wy - origin[1]) / resolution))
    return col, row


def punch_square(arr, cx, cy, half_px, free_value=255):
    """Fill a square centred at (cx, cy) with free_value."""
    h, w = arr.shape
    x0, x1 = max(0, cx - half_px), min(w, cx + half_px + 1)
    y0, y1 = max(0, cy - half_px), min(h, cy + half_px + 1)
    arr[y0:y1, x0:x1] = free_value


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("map_yaml")
    ap.add_argument("locations_json")
    ap.add_argument("--side", type=float, default=1.10,
                    help="Square side length in metres (slightly larger "
                         "than rack to fully erase legs and inflation halo).")
    ap.add_argument("--out", default=None,
                    help="Output PGM path (default: <stem>_clean.pgm).")
    ap.add_argument("--out-yaml", default=None,
                    help="Output YAML path (default: <stem>_clean.yaml).")
    args = ap.parse_args()

    with open(args.map_yaml) as f:
        meta = yaml.safe_load(f)
    pgm_dir = os.path.dirname(os.path.abspath(args.map_yaml))
    pgm_path = os.path.join(pgm_dir, meta["image"])
    arr = load_pgm(pgm_path)
    h, _ = arr.shape

    res = float(meta["resolution"])
    origin = list(meta["origin"][:2])
    half_px = max(1, int(round((args.side / 2.0) / res)))

    with open(args.locations_json) as f:
        locs = json.load(f)

    targets = []
    for r in locs.get("racks", []):
        targets.append((r["x"], r["y"], r.get("name", r.get("id", "?"))))
    for z in locs.get("drop_zones", []):
        targets.append((z["x"], z["y"], z.get("name", z.get("id", "?"))))

    print(f"Cleaning {len(targets)} rack/zone positions "
          f"(side={args.side}m, half_px={half_px})")

    for wx, wy, name in targets:
        col, row = world_to_px(wx, wy, origin, res, h)
        punch_square(arr, col, row, half_px)
        print(f"  {name}: world=({wx:.2f}, {wy:.2f}) → px=({col}, {row})")

    out_pgm = args.out or pgm_path.replace(".pgm", "_clean.pgm")
    save_pgm(arr, out_pgm)

    out_yaml = args.out_yaml or args.map_yaml.replace(".yaml", "_clean.yaml")
    meta["image"] = os.path.basename(out_pgm)
    with open(out_yaml, "w") as f:
        yaml.safe_dump(meta, f, default_flow_style=None, sort_keys=False)

    print(f"Wrote {out_pgm}")
    print(f"Wrote {out_yaml}")


if __name__ == "__main__":
    sys.exit(main())
