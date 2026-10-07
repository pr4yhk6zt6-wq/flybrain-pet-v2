#!/usr/bin/env python3
"""Measure the three things the ingest currently ASSUMES about the release.

1. Are `input_neuropils` / `output_neuropils` ordered by descending synapse
   count, and is there any per-tag count to weight them by? (`_dominant_region`
   claims "the dominant (highest-synapse) tag" but weights every tag as 1.)
2. How many neurons have a first input tag that differs from the first output
   tag - i.e. how much does "input wins" decide?
3. How many neurons have no parseable soma position (the ingest silently
   substitutes (0,0,0) and only bumps a counter)?
4. Does the connections file's `neuropil` column ever carry a compound tag
   (the "ME.LO" form the mapping has a branch for)?
"""
from __future__ import annotations

import gzip
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/raw/flywire/banc"


def main() -> int:
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    keys = Counter()
    for a in list(attrs.values())[:2000]:
        keys.update(a.keys())
    print("attribute keys seen:", sorted(keys))

    both = 0
    differ = 0
    input_only = 0
    output_only = 0
    neither = 0
    multi_tag = 0
    no_pos = 0
    bad_pos = 0
    dot_tags = Counter()
    for a in attrs.values():
        ins = a.get("input_neuropils") or []
        outs = a.get("output_neuropils") or []
        for t in list(ins) + list(outs):
            if "." in t:
                dot_tags[t] += 1
        if len(ins) > 1 or len(outs) > 1:
            multi_tag += 1
        if ins and outs:
            both += 1
            if ins[0] != outs[0]:
                differ += 1
        elif ins:
            input_only += 1
        elif outs:
            output_only += 1
        else:
            neither += 1
        pos = a.get("position") or []
        if not pos:
            no_pos += 1
            continue
        entry = pos[0] if isinstance(pos, (list, tuple)) else pos
        parts = [p.strip() for p in str(entry).replace(",", " ").split()]
        if len(parts) != 3:
            bad_pos += 1
            continue
        try:
            [float(p) for p in parts]
        except ValueError:
            bad_pos += 1

    n = len(attrs)
    print(f"\nneurons                      : {n}")
    print(f"both input+output tags       : {both}")
    print(f"  first tag differs          : {differ}  ({100*differ/max(both,1):.1f}% of those)")
    print(f"input tags only              : {input_only}")
    print(f"output tags only             : {output_only}")
    print(f"neither                      : {neither}")
    print(f"more than one tag either way : {multi_tag}")
    print(f"no position at all           : {no_pos}")
    print(f"position not 'x, y, z'       : {bad_pos}")
    print(f"compound (dot) tags anywhere : {sum(dot_tags.values())} {dict(dot_tags)}")

    print("\nconnections file neuropil column:")
    seen = Counter()
    rows = 0
    with gzip.open(DATA / "connections_princeton.csv.gz", "rt", newline="") as fh:
        for line in fh:
            a = line.find(","); b = line.find(",", a + 1)
            c = line.find(",", b + 1); d = line.find(",", c + 1)
            if d < 0:
                continue
            rows += 1
            tag = line[b + 1:c].strip()
            if rows <= 200000:
                seen[tag] += 1
    print(f"rows read (capped 200k)      : {rows}")
    print(f"distinct neuropil tags in conn file: {len(seen)}")
    print("  with a dot:", {k: v for k, v in seen.items() if "." in k})
    print("  top 12:", seen.most_common(12))
    return 0


if __name__ == "__main__":
    sys.exit(main())