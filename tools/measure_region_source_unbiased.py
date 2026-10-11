#!/usr/bin/env python3
"""Settle which region source to use, with an unbiased sample and both truths.

Two candidate sources:
  A. `banc.py::_dominant_region` - first neuropil TAG that maps (current rule)
  B. the release's own `Top in/out region` column (the ingest never reads it)

On all 805 motor neurons, B matches measured synapses 97.3% vs A's 61.7%. But an
earlier 4,000-neuron sample of the WHOLE population scored A slightly ahead
(73.6% vs 72.1%) - and that sample took the first rows of neurons.csv.gz, which
is biased toward whichever cell types happen to sort first (optic lobe dominates).
Changing `_dominant_region` touches every neuron, not just motor cells, so the
general population has to be measured without that bias.

This samples by STRIDE across the whole table and scores both rules against two
independent ground truths built from `connections_princeton.csv.gz`:
  * `out`    - synapse mass over the neuron's OUTPUT (pre) side
  * `in+out` - synapse mass over both sides, i.e. all its synapses

Run:  python3 tools/measure_region_source_unbiased.py
"""
from __future__ import annotations

import csv
import gzip
import pickle
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain import RegionID as RID              # noqa: E402
from flybrain.banc import _dominant_region        # noqa: E402
from flybrain.neuropil import map_neuropil        # noqa: E402

NAMES = {int(v): k for k, v in vars(RID).items() if k.isupper()}
STRIDE = 7          # every 7th row -> ~22k neurons, spread over the whole file


def dominant(tally: Counter) -> int | None:
    by_region: Counter = Counter()
    for tag, syn in tally.items():
        r = map_neuropil(tag)
        if r is not None:
            by_region[r] += syn
    return by_region.most_common(1)[0][0] if by_region else None


def main() -> int:
    data = ROOT / "data/raw/flywire/banc"
    csv_path = data / "neurons.csv.gz"
    pkl_path = data / "neuron_attributes.pickle.gz"
    conn_path = data / "connections_princeton.csv.gz"
    for p in (csv_path, pkl_path, conn_path):
        if not p.exists():
            print(f"{p.name} missing; raw release not present")
            return 0

    want: set[int] = set()
    col_region: dict[int, int] = {}
    is_motor: set[int] = set()
    i = -1
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            i += 1
            if i % STRIDE:
                continue
            try:
                rid = int(row["Root ID"])
            except (TypeError, ValueError):
                continue
            want.add(rid)
            if "motor" in (row.get("Super Class") or "").lower():
                is_motor.add(rid)
            col = (row.get("Top in/out region") or "").strip()
            r = map_neuropil(col) if col else None
            if r is not None:
                col_region[rid] = r
    print(f"strided sample: {len(want):,} neurons "
          f"({len(col_region):,} with a mappable column, {len(is_motor)} motor)")

    out_t: dict[int, Counter] = defaultdict(Counter)
    in_t: dict[int, Counter] = defaultdict(Counter)
    proc = subprocess.Popen(["zcat", str(conn_path)], stdout=subprocess.PIPE,
                            text=True, bufsize=1 << 20)
    assert proc.stdout is not None
    first = True
    for line in proc.stdout:
        if first:
            first = False
            continue
        parts = line.rstrip("\n").split(",")
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            qid = int(parts[1])
            syn = int(float(parts[3]))
        except ValueError:
            continue
        np_ = parts[2]
        if pid in want:
            out_t[pid][np_] += syn
        if qid in want:
            in_t[qid][np_] += syn
    proc.stdout.close()
    proc.wait()

    attrs = pickle.load(gzip.open(pkl_path, "rb"))

    res = {(name, grp): [0, 0] for name in ("A", "B") for grp in ("all", "motor")}
    for rid in want:
        # ground truths
        truth_out = dominant(out_t.get(rid, Counter()))
        comb = Counter(out_t.get(rid, Counter()))
        comb.update(in_t.get(rid, Counter()))
        truth_all = dominant(comb)
        a = attrs.get(rid, {})
        ruleA = _dominant_region(a, str(a.get("side") or "").lower(), Counter())[0]
        ruleB = col_region.get(rid)
        if ruleB is None:
            continue
        grp = "motor" if rid in is_motor else "all"
        for truth, gname in ((truth_all, "in+out"), (truth_out, "out")):
            if truth is None:
                continue
            for label, rule in (("A", ruleA), ("B", ruleB)):
                if rule == truth:
                    res[(label, grp)][0] += 1
                res[(label, grp)][1] += 1

    print()
    print(f"  {'group':<8}{'rule':<6}{'n':>8}{'correct':>10}{'rate':>9}")
    for grp in ("all", "motor"):
        for label in ("A", "B"):
            hit, tot = res[(label, grp)]
            if tot:
                print(f"  {grp:<8}{label:<6}{tot:>8,}{hit:>10,}{100*hit/tot:>8.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())