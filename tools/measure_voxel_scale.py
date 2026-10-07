#!/usr/bin/env python3
"""Decide the voxel size from the shape of the animal, robustly.

tools/measure_voxel_and_vocabulary.py used min/max extents and got a 12 mm
tall fly, because 4 neurons sit hundreds of thousands of voxels outside the
CNS. This uses percentile extents (immune to those outliers) and splits the
brain from the VNC, then asks which of the candidate voxel sizes reproduces
the published Drosophila dimensions:

    brain          ~ 400 um wide (x) x 300 um long (y) x 250 um tall (z)
    brain + VNC    ~ 1000 um long (y)
    4 nm isotropic (FlyWire/FAFB v14 family), or 4/4/40 nm for the
    anisotropic "voxel" convention some releases use for z.
"""
from __future__ import annotations

import gzip
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/raw/flywire/banc"


def pct(vals, q):
    vals = sorted(vals)
    if not vals:
        return float("nan")
    i = min(len(vals) - 1, max(0, int(q * (len(vals) - 1))))
    return vals[i]


def main() -> int:
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    brain = {"x": [], "y": [], "z": []}
    vnc = {"x": [], "y": [], "z": []}
    allv = {"x": [], "y": [], "z": []}
    for a in attrs.values():
        pos = a.get("position") or []
        if not pos:
            continue
        x, y, z = (float(p) for p in str(pos[0]).replace(",", " ").split())
        for k, v in (("x", x), ("y", y), ("z", z)):
            allv[k].append(v)
        tgt = brain if y < 130000 else vnc
        tgt["x"].append(x); tgt["y"].append(y); tgt["z"].append(z)

    print(f"{'set':<8}{'axis':<5}{'1%':>10}{'99%':>10}{'span':>10}")
    spans = {}
    for name, d in (("brain", brain), ("vnc", vnc), ("all", allv)):
        for k in "xyz":
            lo = pct(d[k], 0.01); hi = pct(d[k], 0.99)
            spans[(name, k)] = hi - lo
            print(f"{name:<8}{k:<5}{lo:>10.0f}{hi:>10.0f}{hi-lo:>10.0f}")

    for nm, xy, z in (("4 / 4 / 40 nm", 4.0, 40.0),
                      ("4 nm isotropic", 4.0, 4.0),
                      ("8 / 8 / 40 nm", 8.0, 40.0)):
        print(f"\n{nm}:")
        for name in ("brain", "vnc", "all"):
            x = spans[(name, "x")] * xy / 1000
            y = spans[(name, "y")] * xy / 1000
            z = spans[(name, "z")] * z / 1000
            print(f"   {name:<6} {x:>8.0f} x {y:>8.0f} x {z:>8.0f} um")
    return 0


if __name__ == "__main__":
    sys.exit(main())