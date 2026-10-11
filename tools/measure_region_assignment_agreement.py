#!/usr/bin/env python3
"""Does our region assignment agree with the release's own "Top in/out region"?

`banc.py::_dominant_region` picks a neuron's region by counting the tags in its
`input_neuropils` + `output_neuropils` lists and taking the winner. The release
also ships an explicit `Top in/out region` column in neurons.csv. These two are
not the same computation, and this measures how far apart they land.

Motivation: `wingMuscleDrive` reads WING_NEUROPIL, which the BANC asset gives 0
motor cells, while the flight power muscles (DLM1-4) sit in HALTERE_NEUROPIL.
The release's own column says DLM1-4 are WTCT (= wing tectulum). So either the
router reads the wrong region, or the ingest assigns the wrong one -- this
decides which.
"""
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


def names(v):
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)] if v else []


def main():
    csv_path = ROOT / "data/raw/flywire/banc/neurons.csv.gz"
    pkl_path = ROOT / "data/raw/flywire/banc/neuron_attributes.pickle.gz"
    if not csv_path.exists() or not pkl_path.exists():
        print("raw release not present; nothing to measure")
        return 0

    attrs = pickle.load(gzip.open(pkl_path, "rb"))

    # The release's own answer, per root id.
    released = {}
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            rid = row["Root ID"].strip()
            released[rid] = row.get("Top in/out region", "").strip()

    unmapped = Counter()
    agree = disagree = 0
    flight_released = Counter()
    flight_ours = Counter()
    examples = []

    for rid, a in attrs.items():
        super_class = str(a.get("super_class") or "").lower()
        if "motor" not in super_class:
            continue
        ours, _ = _dominant_region(a, "", unmapped)
        rel_tag = released.get(str(a.get("root_id") or rid), "")
        rel = map_neuropil(rel_tag) if rel_tag else None
        if rel is None:
            continue
        if ours == rel:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 12:
                examples.append(
                    (str(names(a.get("cell_type")))[:30],
                     rel_tag[:18], NAMES.get(ours), NAMES.get(rel)))

        allc = " ".join(names(a.get("cell_type")) + names(a.get("resolved_type"))).upper()
        if "DLM" in allc or "DVM" in allc:
            flight_released[NAMES.get(rel)] += 1
            flight_ours[NAMES.get(ours)] += 1

    total = agree + disagree
    print(f"motor neurons compared: {total:,}")
    if total:
        print(f"  agreement:    {agree:,} ({100*agree/total:.1f}%)")
        print(f"  disagreement: {disagree:,} ({100*disagree/total:.1f}%)")
    print("\nflight power muscles (DLM/DVM):")
    print(f"  release says: {dict(flight_released)}")
    print(f"  we assign:    {dict(flight_ours)}")
    print("\nexamples of disagreement (cell type, release tag, ours, release):")
    for ct, tag, o, r in examples:
        print(f"  {ct:<30} {tag:<18} ours={o} release={r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())