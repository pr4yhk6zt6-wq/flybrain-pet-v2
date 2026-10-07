#!/usr/bin/env python3
"""Is the attributes pickle one coordinate frame, or several?

tools/measure_voxel_and_vocabulary.py found the position extents imply a
12 mm tall animal under the 4/4/40 nm conversion used by flybrain/banc.py.
Before touching the scale, check whether the pickle mixes datasets with
different coordinate frames, and where the soma clusters actually are.
"""
from __future__ import annotations

import gzip
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/raw/flywire/banc"


def main() -> int:
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    ds = Counter()
    ext = defaultdict(lambda: [float("inf"), float("-inf")] * 1)
    per_ds = {}
    for rid, a in attrs.items():
        d = a.get("dataset", "?")
        ds[d] += 1
        pos = a.get("position") or []
        if not pos:
            continue
        xyz = [float(p) for p in str(pos[0]).replace(",", " ").split()]
        e = per_ds.setdefault(d, [[float("inf")] * 3, [float("-inf")] * 3])
        for i in range(3):
            e[0][i] = min(e[0][i], xyz[i])
            e[1][i] = max(e[1][i], xyz[i])
    print("datasets in the pickle:", dict(ds))
    for d, (lo, hi) in per_ds.items():
        print(f"  {d}: x {lo[0]:.0f}..{hi[0]:.0f}  y {lo[1]:.0f}..{hi[1]:.0f}"
              f"  z {lo[2]:.0f}..{hi[2]:.0f}")

    # histogram of y across everything, 40 bins, to expose frame mixing
    ys = []
    for a in attrs.values():
        pos = a.get("position") or []
        if pos:
            ys.append(float(str(pos[0]).replace(",", " ").split()[1]))
    ys.sort()
    if ys:
        n = len(ys)
        print(f"\ny samples {n}: min {ys[0]:.0f} p1 {ys[n//100]:.0f} "
              f"median {ys[n//2]:.0f} p99 {ys[99*n//100]:.0f} max {ys[-1]:.0f}")
        lo, hi = ys[0], ys[-1]
        step = (hi - lo) / 20
        bins = Counter(int((v - lo) / step) for v in ys)
        for b in range(20):
            c = bins.get(b, 0)
            print(f"   y {lo + b*step:>9.0f}..{lo + (b+1)*step:>9.0f} "
                  f"{'#' * int(60 * c / max(bins.values())):<60}{c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())