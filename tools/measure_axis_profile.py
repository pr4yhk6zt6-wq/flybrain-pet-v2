#!/usr/bin/env python3
"""Settle the voxel scale from the shape of the animal, axis by axis.

The ingest converts positions with 4 nm in x/y and 40 nm in z. That is either
right or it silently distorts the whole animal, so this prints the full
percentile profile per axis (min/max are useless: 4 neurons sit 500k voxels
outside the CNS) and the implied physical size under each candidate scale.

Also splits brain from VNC at the y gap (the release has a 50k-voxel empty band
between y=60k and y=110k), because the two have known, published sizes:
    brain      ~ 400-500 um wide x 300-400 um long x 250 um tall
    brain+VNC  ~ 1000 um long
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
    return vals[min(len(vals) - 1, max(0, int(q * (len(vals) - 1))))]


def main() -> int:
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    axes = {"x": [], "y": [], "z": []}
    for a in attrs.values():
        pos = a.get("position") or []
        if not pos:
            continue
        x, y, z = (float(p) for p in str(pos[0]).replace(",", " ").split())
        axes["x"].append(x); axes["y"].append(y); axes["z"].append(z)

    qs = [0, 0.001, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999, 1]
    print(f"{'axis':<5}" + "".join(f"{f'q{q}':>10}" for q in qs))
    for k in "xyz":
        print(f"{k:<5}" + "".join(f"{pct(axes[k], q):>10.0f}" for q in qs))
    print()
    print(f"{'axis':<5}{'q1-q99':>12}{'q25-q75':>12}{'q.1-q99.9':>12}")
    spans = {}
    for k in "xyz":
        a = axes[k]
        s99 = pct(a, 0.99) - pct(a, 0.01)
        s50 = pct(a, 0.75) - pct(a, 0.25)
        s999 = pct(a, 0.999) - pct(a, 0.001)
        spans[k] = s99
        print(f"{k:<5}{s99:>12.0f}{s50:>12.0f}{s999:>12.0f}")

    print("\nimplied physical size of the q1-q99 box:")
    for nm, xy, z in (("4 / 4 / 40 nm", 4.0, 40.0),
                      ("4 nm isotropic", 4.0, 4.0),
                      ("8 / 8 / 40 nm", 8.0, 40.0),
                      ("2 / 2 / 40 nm", 2.0, 40.0)):
        print(f"  {nm:<16} {spans['x']*xy/1000:>7.0f} x {spans['y']*xy/1000:>7.0f}"
              f" x {spans['z']*z/1000:>7.0f} um")

    # brain vs VNC split at the measured gap
    for label, lo_y, hi_y in (("brain", -1e9, 90000), ("VNC", 90000, 1e9)):
        sub = {"x": [], "y": [], "z": []}
        for a in attrs.values():
            pos = a.get("position") or []
            if not pos:
                continue
            x, y, z = (float(p) for p in str(pos[0]).replace(",", " ").split())
            if lo_y <= y < hi_y:
                sub["x"].append(x); sub["y"].append(y); sub["z"].append(z)
        print(f"\n{label}: n={len(sub['x'])}")
        for k in "xyz":
            print(f"   {k}: q1 {pct(sub[k],0.01):>9.0f} q99 {pct(sub[k],0.99):>9.0f}"
                  f" span {pct(sub[k],0.99)-pct(sub[k],0.01):>9.0f} voxels"
                  f"  = {(pct(sub[k],0.99)-pct(sub[k],0.01))*(40.0 if k=='z' else 4.0)/1000:>6.0f} um")
    return 0


if __name__ == "__main__":
    sys.exit(main())