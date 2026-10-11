#!/usr/bin/env python3
"""Which region source is right? Score both against measured synapses.

Two candidate sources disagree on ~31% of neurons:
  A. `banc.py::_dominant_region` - first neuropil TAG that maps (current rule)
  B. the release's own `Top in/out region` column (the ingest never reads it)

Agreement between A and B proves nothing. `connections_princeton.csv.gz` carries,
per connection, the neuropil and synapse count, so for each neuron we can measure
the synapse-weighted dominant neuropil directly. This scores A and B against that
independent measurement.

Memory discipline (the box this runs on caps a process well below the size of
these files held at once): only the sampled ids are ever resident, the CSV table
is freed before the pickle is opened, and the connections file is streamed twice
- once per side - so no per-connection object is retained.

Run:  python3 tools/measure_region_ground_truth.py [--sample N]
"""
from __future__ import annotations

import argparse
import csv
import gc
import gzip
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain import RegionID as RID              # noqa: E402
from flybrain.banc import _dominant_region        # noqa: E402
from flybrain.neuropil import map_neuropil        # noqa: E402

NAMES = {int(v): k for k, v in vars(RID).items() if k.isupper()}


def dominant_region(tally: Counter) -> int | None:
    """Region holding the most synapses, summing every neuropil tag."""
    by_region: Counter = Counter()
    for tag, syn in tally.items():
        r = map_neuropil(tag)
        if r is not None:
            by_region[r] += syn
    return by_region.most_common(1)[0][0] if by_region else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=4000,
                    help="score only the first N neurons (0 = all)")
    ap.add_argument("--motor-only", action="store_true",
                    help="score only motor neurons")
    args = ap.parse_args()

    data = ROOT / "data/raw/flywire/banc"
    csv_path = data / "neurons.csv.gz"
    pkl_path = data / "neuron_attributes.pickle.gz"
    conn_path = data / "connections_princeton.csv.gz"
    for p in (csv_path, pkl_path, conn_path):
        if not p.exists():
            print(f"{p.name} missing; raw release not present")
            return 0

    # ---- pass 1: pick the sample, keep only its rows -----------------------
    col_region: dict[int, int] = {}
    want: set[int] = set()
    motor: set[int] = set()
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            if args.motor_only and "motor" not in (row.get("Super Class") or "").lower():
                continue
            try:
                rid = int(row["Root ID"])
            except (TypeError, ValueError):
                continue
            col = (row.get("Top in/out region") or "").strip()
            r = map_neuropil(col) if col else None
            if r is not None:
                col_region[rid] = r
            want.add(rid)
            if args.sample and len(want) >= args.sample:
                break
    gc.collect()
    print(f"sampled {len(want):,} neurons ({len(col_region):,} with a mappable column)")

    # ---- pass 2/3: measured synapse-weighted region, per side --------------
    out_tally: dict[int, Counter] = defaultdict(Counter)
    in_tally: dict[int, Counter] = defaultdict(Counter)
    for side, path_key in (("output", "pre_root_id"), ("input", "post_root_id")):
        print(f"  streaming connections ({side}) ...")
        with gzip.open(conn_path, "rt") as fh:
            rd = csv.DictReader(fh)
            idx = 0
            for row in rd:
                try:
                    nid = int(row[path_key])
                except (TypeError, ValueError):
                    continue
                if nid not in want:
                    continue
                try:
                    syn = int(float(row.get("syn_count") or 0))
                except ValueError:
                    syn = 0
                (out_tally if side == "output" else in_tally)[nid][
                    row.get("neuropil") or ""] += syn
                idx += 1
                if idx % 500000 == 0:
                    print(f"    matched {idx:,} rows")
    gc.collect()

    # ---- pass 4: rule A, from the pickle (opened only now) ----------------
    attrs = pickle.load(gzip.open(pkl_path, "rb"))
    gc.collect()

    a_hit = b_hit = n = 0
    a_ex: Counter = Counter()
    b_ex: Counter = Counter()
    for rid in want:
        truth = dominant_region(out_tally.get(rid, Counter()))
        if truth is None:
            truth = dominant_region(in_tally.get(rid, Counter()))
        if truth is None:
            continue
        n += 1
        a = attrs.get(rid, {})
        side = str(a.get("side") or "").strip().lower()
        ruleA = _dominant_region(a, side, Counter())[0]
        if ruleA == truth:
            a_hit += 1
        else:
            a_ex[(NAMES.get(ruleA), NAMES.get(truth))] += 1
        ruleB = col_region.get(rid)
        if ruleB is not None:
            if ruleB == truth:
                b_hit += 1
            else:
                b_ex[(NAMES.get(ruleB), NAMES.get(truth))] += 1

    covered = sum(1 for r in want if col_region.get(r) is not None
                  and (out_tally.get(r) or in_tally.get(r)))
    print()
    print(f"neurons scored against measured synapse-weighted region: {n:,}")
    print(f"  A. tag rule (current)   correct: {a_hit:,} ({100*a_hit/max(n,1):.1f}%)")
    print(f"  B. release column       correct: {b_hit:,} ({100*b_hit/max(covered,1):.1f}%)"
          f"  [covers {covered:,}]")
    print("\nA's worst errors (rule -> measured):")
    for (r, t), c in a_ex.most_common(8):
        print(f"  {str(r):<22} -> {str(t):<22}{c:>7,}")
    print("\nB's worst errors (release column -> measured):")
    for (r, t), c in b_ex.most_common(8):
        print(f"  {str(r):<22} -> {str(t):<22}{c:>7,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())