#!/usr/bin/env python3
"""Which region source is right for MOTOR cells? Measured against synapses.

TASK-007: `wingMuscleDrive` reads WING_NEUROPIL, which the BANC asset leaves with
0 motor cells (the demo asset has 2, so the app looks fine). Candidate cause:
`banc.py::_dominant_region` reconstructs a neuron's region from the first
neuropil TAG that maps, scanning `input_neuropils` before `output_neuropils`,
while the release also ships an explicit `Top in/out region` column that the
ingest never reads.

Ground truth is independent of both: `connections_princeton.csv.gz` carries the
neuropil and synapse count of every connection, so summing a motor neuron's
output synapses by region measures where it actually sends signals.

Memory discipline: the connections file is streamed from `zcat` and parsed with
plain string splits (no per-row dicts), and only the 805 motor ids are ever held.
Run:  python3 tools/measure_motor_region_ground_truth.py
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


def dominant_region(tally: Counter) -> int | None:
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

    motor: set[int] = set()
    col_region: dict[int, int] = {}
    cell_type: dict[int, str] = {}
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            if "motor" not in (row.get("Super Class") or "").lower():
                continue
            try:
                rid = int(row["Root ID"])
            except (TypeError, ValueError):
                continue
            motor.add(rid)
            cell_type[rid] = (row.get("Primary Cell Type") or "?").strip()
            col = (row.get("Top in/out region") or "").strip()
            r = map_neuropil(col) if col else None
            if r is not None:
                col_region[rid] = r
    print(f"motor neurons in release: {len(motor):,} "
          f"({len(col_region):,} with a mappable `Top in/out region`)")

    # ---- connections: pre_root_id, post_root_id, neuropil, syn_count -------
    out_tally: dict[int, Counter] = defaultdict(Counter)
    in_tally: dict[int, Counter] = defaultdict(Counter)
    print("streaming connections via zcat ...")
    proc = subprocess.Popen(["zcat", str(conn_path)], stdout=subprocess.PIPE,
                            text=True, bufsize=1 << 20)
    assert proc.stdout is not None
    header_seen = False
    rows = 0
    for line in proc.stdout:
        if not header_seen:
            header_seen = True
            continue
        parts = line.rstrip("\n").split(",")
        if len(parts) < 4:
            continue
        rows += 1
        try:
            pid = int(parts[0])
            qid = int(parts[1])
        except ValueError:
            continue
        # only parse the count for the neurons we keep
        if pid in motor:
            try:
                out_tally[pid][parts[2]] += int(float(parts[3]))
            except ValueError:
                pass
        if qid in motor:
            try:
                in_tally[qid][parts[2]] += int(float(parts[3]))
            except ValueError:
                pass
        if rows % 1000000 == 0:
            print(f"    {rows:,} rows")
    proc.stdout.close()
    proc.wait()
    print(f"    total {rows:,} rows")

    attrs = pickle.load(gzip.open(pkl_path, "rb"))

    FLIGHT = ("DLM", "DVM")
    a_hit = b_hit = n = 0
    a_ex: Counter = Counter()
    b_ex: Counter = Counter()
    wing_truth = wing_a = wing_b = 0
    flights = []
    for rid in sorted(motor):
        truth = dominant_region(out_tally.get(rid, Counter()))
        if truth is None:
            truth = dominant_region(in_tally.get(rid, Counter()))
        ct = cell_type.get(rid, "?")
        a = attrs.get(rid, {})
        ruleA = _dominant_region(a, str(a.get("side") or "").lower(), Counter())[0]
        ruleB = col_region.get(rid)
        if truth is not None and NAMES.get(truth) == "WING_NEUROPIL":
            wing_truth += 1
            if NAMES.get(ruleA) == "WING_NEUROPIL":
                wing_a += 1
            if ruleB is not None and NAMES.get(ruleB) == "WING_NEUROPIL":
                wing_b += 1
        if any(k in ct.upper() for k in FLIGHT):
            flights.append((ct, NAMES.get(ruleA), NAMES.get(ruleB),
                            NAMES.get(truth)))
        if truth is None:
            continue
        n += 1
        if ruleA == truth:
            a_hit += 1
        else:
            a_ex[(NAMES.get(ruleA), NAMES.get(truth))] += 1
        if ruleB is not None:
            if ruleB == truth:
                b_hit += 1
            else:
                b_ex[(NAMES.get(ruleB), NAMES.get(truth))] += 1

    print()
    print(f"motor neurons scored by measured synapse-weighted region: {n:,}")
    print(f"  A. tag rule (current)   correct: {a_hit:,} ({100*a_hit/max(n,1):.1f}%)")
    print(f"  B. release column       correct: {b_hit:,} ({100*b_hit/max(n,1):.1f}%)")
    print(f"\nmotor cells MEASURED in WING_NEUROPIL: {wing_truth}; "
          f"rule A files {wing_a}, rule B files {wing_b}"
          f" -> the readout can see {wing_a} (now) vs {wing_b} (release column)")

    print("\nA's worst errors (rule -> measured):")
    for (r, t), c in a_ex.most_common(8):
        print(f"  {str(r):<22} -> {str(t):<22}{c:>6,}")
    print("\nB's worst errors (release column -> measured):")
    for (r, t), c in b_ex.most_common(8):
        print(f"  {str(r):<22} -> {str(t):<22}{c:>6,}")

    print("\nflight power muscles (cell type, A, B, MEASURED):")
    for ct, ra, rb, tr in sorted(flights):
        print(f"  {ct:<14} A={str(ra):<16} B={str(rb):<16} measured={tr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())