#!/usr/bin/env python3
"""Verify the voxel size assumption from the release itself, and dump the
complete 111-tag vocabulary with its current mapping.

Voxel size: focbrain/banc.py converts positions with x,y * 4 nm and z * 40 nm.
That anisotropy is load-bearing (it decides the brain's shape in the simulator),
so measure it: a Drosophila brain is ~500 um long, ~350 um wide and ~250 um
tall. If the release really is 4x4x40 nm, the voxel extents must come out in
that ratio.

Vocabulary: every tag the release contains, so the mapping can be pinned
exhaustively instead of relying on the generic dot-split branch.
"""
from __future__ import annotations

import gzip
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain.neuropil import map_neuropil, strip_side  # noqa: E402

DATA = ROOT / "data/raw/flywire/banc"


def main() -> int:
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    side = Counter()
    for a in attrs.values():
        side[(a.get("side") or "").strip().lower()] += 1
        pos = a.get("position") or []
        if not pos:
            continue
        xyz = [float(p) for p in str(pos[0]).replace(",", " ").split()]
        for i in range(3):
            lo[i] = min(lo[i], xyz[i]); hi[i] = max(hi[i], xyz[i])

    print("voxel extents (release units):")
    names = "x (left-right) y (anterior-posterior) z (dorsal-ventral)".split("  ")
    for i, nm in enumerate(["x", "y", "z"]):
        span = hi[i] - lo[i]
        print(f"   {nm}: {lo[i]:>10.0f} .. {hi[i]:>10.0f}  span {span:>9.0f} voxels")
    xy_nm, z_nm = 4.0, 40.0
    print("with 4/4/40 nm voxels the animal would be:")
    print(f"   x = {(hi[0]-lo[0])*xy_nm/1000:>7.0f} um")
    print(f"   y = {(hi[1]-lo[1])*xy_nm/1000:>7.0f} um")
    print(f"   z = {(hi[2]-lo[2])*z_nm/1000:>7.0f} um")
    print("with a scalar 4 nm voxel it would be:")
    print(f"   x = {(hi[0]-lo[0])*xy_nm/1000:>7.0f} um")
    print(f"   y = {(hi[1]-lo[1])*xy_nm/1000:>7.0f} um")
    print(f"   z = {(hi[2]-lo[2])*xy_nm/1000:>7.0f} um")
    print("soma sides:", dict(side))

    tags = Counter()
    for a in attrs.values():
        for k in ("input_neuropils", "output_neuropils"):
            for t in a.get(k) or []:
                tags[t] += 1
    print(f"\ncomplete release vocabulary ({len(tags)} tags):")
    unmapped = []
    for tag, n in sorted(tags.items()):
        m = map_neuropil(tag, "")
        if m is None:
            unmapped.append((tag, n))
    print(f"   mapped  : {len(tags) - len(unmapped)}")
    print(f"   UNMAPPED: {len(unmapped)} -> {unmapped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())