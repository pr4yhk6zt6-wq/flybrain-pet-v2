#!/usr/bin/env python3
"""Verify the ordering + vocabulary assumptions the streaming ingest relies on.

Three claims in flybrain/banc.py are load-bearing and were never measured:

A. "the release is already ordered by presynaptic root id"  -> the single-pass
   duplicate merge and the outgoing-CSR forward scan only work if the connection
   file never repeats a (pre, post) pair out of order (checked separately by
   tools/check_release_order.py) AND if canonical id order == raw root id order.
   The latter needs neurons.csv.gz itself to be sorted by Root ID.
B. "the release's per-connection neurotransmitter column is empty for every
   BANC row" -> if it is populated we should use the measured per-edge value
   instead of inferring the sign from the presynaptic neuron.
C. the cell-type vocabulary size (the Swift reader widened the field to u16 on
   the claim that BANC has 11,566 distinct types).
"""
from __future__ import annotations

import gzip
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/raw/flywire/banc"


def main() -> int:
    print("A. neuron table order + vocabulary")
    import csv
    prev = -1
    inversions = 0
    rows = 0
    types = Counter()
    fragments = 0
    super_classes = Counter()
    with gzip.open(DATA / "neurons.csv.gz", "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            try:
                rid = int(row["Root ID"])
            except (TypeError, ValueError):
                continue
            if rid < prev:
                inversions += 1
            prev = rid
            t = (row.get("Primary Cell Type") or row.get("Class")
                 or row.get("Super Class") or "unclassified").strip()
            types[t] += 1
            if "fragment" in (row.get("Primary Cell Type") or "").lower():
                fragments += 1
            super_classes[(row.get("Super Class") or "").strip()] += 1
    print(f"   rows                          : {rows}")
    print(f"   Root ID inversions            : {inversions}")
    print(f"   distinct cell types (u16 need): {len(types)}")
    print(f"   rows with 'fragment' in type  : {fragments}")
    print(f"   super classes                 : {dict(super_classes)}")

    print("\nB. per-connection nt_type column")
    nt = Counter()
    rows = 0
    with gzip.open(DATA / "connections_princeton.csv.gz", "rt", newline="") as fh:
        for line in fh:
            a = line.find(","); b = line.find(",", a + 1)
            c = line.find(",", b + 1); d = line.find(",", c + 1)
            if d < 0:
                continue
            rows += 1
            if rows <= 300000:
                nt[line[d + 1:].strip()] += 1
    print(f"   rows                          : {rows}")
    print(f"   nt_type values (first 300k)   : {dict(nt.most_common(10))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())