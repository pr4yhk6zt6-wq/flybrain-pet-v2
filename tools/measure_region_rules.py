#!/usr/bin/env python3
"""Which region-assignment rule should the BANC ingest use?

The ingest files each neuron into ONE simulator region via
`banc.py::_dominant_region`, which takes the first tag that maps, scanning
`input_neuropils` before `output_neuropils`. The release ships its own explicit
`Top in/out region` column (neurons.csv.gz) that the ingest does not consult.

TASK-007 surfaced the consequence: `wingMuscleDrive` reads WING_NEUROPIL, which
the asset leaves with 0 motor cells, while the flight power muscles (DLM1-4) end
up in HALTERE_NEUROPIL -- even though the release itself labels DLM1-4 `WTCT`
(wing tectulum).

This is a measurement, not an argument. It scores candidate rules against the
release's own annotation and reports what each would put in the three regions
the motor readout actually reads. `Top in/out region` is used here as the
REFERENCE, not as truth: it is one more release annotation, and the point is to
stop silently disagreeing with the dataset we already download.

Run:  python3 tools/measure_region_rules.py
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

from flybrain import RegionID as RID          # noqa: E402
from flybrain.banc import _dominant_region    # noqa: E402
from flybrain.neuropil import map_neuropil    # noqa: E402

NAMES = {int(v): k for k, v in vars(RID).items() if k.isupper()}
READOUT_REGIONS = ("LEG_NEUROMERE", "WING_NEUROPIL", "SUBESOPHAGEAL_ZONE")


def as_list(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)] if v else []


def first_mapped(tags, side="") -> int | None:
    for tag in tags:
        r = map_neuropil(tag, side)
        if r is not None:
            return r
    return None


def rule_leading(attrs, side="") -> int | None:
    """Current rule: first mapping tag, input list before output list."""
    r, _u = _dominant_region(attrs, side, Counter())
    return r


def rule_output_only(attrs, side="") -> int | None:
    """A motor neuron's region is where its axon goes (output tags first)."""
    return first_mapped(as_list(attrs.get("output_neuropils")), side) or \
        first_mapped(as_list(attrs.get("input_neuropils")), side)


def rule_tallied_output(attrs, side="") -> int | None:
    """Majority vote over OUTPUT tags only (ignores tag order entirely)."""
    tally = Counter()
    for tag in as_list(attrs.get("output_neuropils")):
        r = map_neuropil(tag, side)
        if r is not None:
            tally[r] += 1
    return tally.most_common(1)[0][0] if tally else None


def main() -> int:
    data = ROOT / "data/raw/flywire/banc"
    csv_path, pkl_path = data / "neurons.csv.gz", data / "neuron_attributes.pickle.gz"
    if not csv_path.exists() or not pkl_path.exists():
        print("raw release not present; nothing to measure")
        return 0

    released: dict[str, str] = {}
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            released[row["Root ID"].strip()] = (row.get("Top in/out region") or "").strip()

    attrs = pickle.load(gzip.open(pkl_path, "rb"))
    rules = {
        "leading (current)": rule_leading,
        "output-only": rule_output_only,
        "tallied output": rule_tallied_output,
    }
    hits = Counter()
    total = 0
    pool = Counter()          # rule -> region -> motor count
    flight = Counter()        # rule -> region -> DLM/DVM count

    for rid, a in attrs.items():
        tag = released.get(str(a.get("root_id") or rid), "")
        ref = map_neuropil(tag) if tag else None
        if ref is None:
            continue
        total += 1
        is_motor = "motor" in str(a.get("super_class") or "").lower()
        names = " ".join(as_list(a.get("cell_type"))
                         + as_list(a.get("resolved_type"))).upper()
        is_flight = any(k in names for k in ("DLM", "DVM"))
        for name, fn in rules.items():
            ours = fn(a)
            if ours == ref:
                hits[name] += 1
            if is_motor and ours is not None:
                pool[(name, NAMES.get(ours))] += 1
            if is_flight and ours is not None:
                flight[(name, NAMES.get(ours))] += 1

    print(f"neurons with a mappable release annotation: {total:,}\n")
    print("agreement with the release's own `Top in/out region`:")
    for name in rules:
        print(f"  {name:<20} {hits[name]:>7,}  ({100*hits[name]/max(total,1):5.1f}%)")

    print("\nmotor cells landing in the readout regions:")
    hdr = f"  {'rule':<20}" + "".join(f"{r:<20}" for r in READOUT_REGIONS)
    print(hdr)
    for name in rules:
        row = f"  {name:<20}"
        for r in READOUT_REGIONS:
            row += f"{pool.get((name, r), 0):<20}"
        print(row)

    print("\nflight power muscles (DLM/DVM) by rule:")
    for name in rules:
        got = {r: n for (nm, r), n in flight.items() if nm == name}
        print(f"  {name:<20} {dict(sorted(got.items(), key=lambda kv: -kv[1]))}")

    print("\nnote: WING_NEUROPIL = 0 motor cells is the TASK-007 defect this measures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())