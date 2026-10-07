#!/usr/bin/env python3
"""Exact breakdown of which neurons the ingest drops, and why.

Dropping a neuron silently deletes every connection that touches it, so this
counts both: neurons per drop reason, and the number of traced connections lost
with them (the two-sided in/out degree, so the loss is not double counted).

Runs the real flybrain.banc code path on the real release files.
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

from flybrain.banc import (_dominant_region, _load_neuron_table,  # noqa: E402
                           _parse_position)
from flybrain.pid import RegionID  # noqa: E402

DATA = ROOT / "data/raw/flywire/banc"


def main() -> int:
    table = _load_neuron_table(DATA / "neurons.csv.gz")
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    reasons = Counter()
    non_neuron_class = Counter()
    unmapped_tags = Counter()
    kept = 0
    for rid, row in table.items():
        a = attrs.get(rid, {})
        sc = (row.get("Super Class") or "").strip()
        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        is_frag = "fragment" in (row.get("Primary Cell Type") or "").lower()
        if sc == "glia":
            reasons["glia"] += 1
            continue
        if sc in ("trachea", "not_a_neuron"):
            non_neuron_class[sc] += 1
        region, unmapped = _dominant_region(a, side_raw, unmapped_tags)
        if region == int(RegionID.UNKNOWN):
            reasons["fragment-no-region" if is_frag
                    else "no-mappable-neuropil"] += 1
            if not (a.get("input_neuropils") or a.get("output_neuropils")):
                reasons["  (of which: no tag at all)"] += 1
            continue
        kept += 1
    print("drop reasons (neurons):")
    for k, v in reasons.most_common():
        print(f"    {k:<32}{v:>8}")
    print(f"    {'KEPT':<32}{kept:>8}")
    print(f"    {'total':<32}{kept + sum(v for k, v in reasons.items() if not k.startswith('  ')):>8}")
    print("non-neuron classes kept:", dict(non_neuron_class))
    print("top unmapped tags:", dict(unmapped_tags.most_common(12)))

    print("\nconnection loss caused by those neuron drops:")
    # count kept edges per drop reason, two-sided, without double counting
    ids = [int(r) for r in table]
    keep = {}
    for rid in ids:
        row = table[rid]
        a = attrs.get(rid, {})
        sc = (row.get("Super Class") or "").strip()
        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        if sc == "glia":
            keep[rid] = "glia"
            continue
        region, _u = _dominant_region(a, side_raw, Counter())
        if region == int(RegionID.UNKNOWN):
            keep[rid] = "no-region"
        else:
            keep[rid] = None
    lost = Counter()
    total_rows = 0
    with gzip.open(DATA / "connections_princeton.csv.gz", "rt", newline="") as fh:
        for line in fh:
            a = line.find(","); b = line.find(",", a + 1)
            c = line.find(",", b + 1); d = line.find(",", c + 1)
            if d < 0:
                continue
            total_rows += 1
            try:
                pre = int(line[:a]); post = int(line[a + 1:b])
            except ValueError:
                continue
            why_pre = keep.get(pre, "absent-from-table")
            why_post = keep.get(post, "absent-from-table")
            if why_pre or why_post:
                if why_pre and why_post:
                    lost["both endpoints dropped"] += 1
                elif why_pre:
                    lost[f"presynaptic = {why_pre}"] += 1
                else:
                    lost[f"postsynaptic = {why_post}"] += 1
    print(f"    release rows           : {total_rows}")
    for k, v in lost.most_common(12):
        print(f"    {k:<32}{v:>9}")
    print(f"    {'TOTAL LOST':<32}{sum(lost.values()):>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())