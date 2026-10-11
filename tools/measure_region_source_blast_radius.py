#!/usr/bin/env python3
"""Blast radius of switching the BANC region source to the release's own column.

`banc.py::_dominant_region` reconstcts region from neuropil tag lists. The
release ships `Top in/out region` per neuron, which the ingest never reads and
with which the reconstruction agrees only 68.7% of the time.

Before switching, measure what changes: the whole-region distribution, the motor
pools, and specifically the flight power muscles whose mis-filing is TASK-007.
"""
from __future__ import annotations

import csv
import gzip
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain import RegionID as RID              # noqa: E402
from flybrain.banc import _dominant_region        # noqa: E402
from flybrain.neuropil import map_neuropil        # noqa: E402

NAMES = {int(v): k for k, v in vars(RID).items() if k.isupper()}


def as_list(v):
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)] if v else []


def main() -> int:
    data = ROOT / "data/raw/flywire/banc"
    csv_path, pkl_path = data / "neurons.csv.gz", data / "neuron_attributes.pickle.gz"
    if not csv_path.exists() or not pkl_path.exists():
        print("raw release not present; nothing to measure")
        return 0

    table = {}
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            try:
                table[int(row["Root ID"])] = row
            except (TypeError, ValueError):
                continue
    attrs = pickle.load(gzip.open(pkl_path, "rb"))

    tag_all, col_all = Counter(), Counter()
    moved = Counter()
    for rid, row in table.items():
        a = attrs.get(rid, {})
        super_class = (row.get("Super Class") or "").strip()
        if super_class in ("glia", "trachea", "not_a_neuron"):
            continue
        side = (row.get("Soma side") or a.get("side") or "").strip().lower()
        tag_r = _dominant_region(a, side, Counter())[0]
        col = (row.get("Top in/out region") or "").strip()
        col_r = map_neuropil(col) if col else None
        final = col_r if col_r is not None else tag_r
        tag_all[NAMES.get(tag_r, str(tag_r))] += 1
        col_all[NAMES.get(final, str(final))] += 1
        if final != tag_r:
            moved[(NAMES.get(tag_r), NAMES.get(final))] += 1

    print(f"{'region':<24}{'tag rule (now)':>16}{'release column':>16}{'delta':>10}")
    for name in sorted(set(tag_all) | set(col_all),
                       key=lambda n: -(col_all[n])):
        t, c = tag_all[name], col_all[name]
        print(f"{name:<24}{t:>16,}{c:>16,}{c-t:>+10,}")

    print(f"\ntotal neurons reassigned: {sum(moved.values()):,}")
    print("largest individual moves (from -> to):")
    for (f, t), n in moved.most_common(12):
        print(f"  {str(f):<24} -> {str(t):<24}{n:>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())