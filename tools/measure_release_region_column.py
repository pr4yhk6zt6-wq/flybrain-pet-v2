#!/usr/bin/env python3
"""Should the ingest use the release's own `Top in/out region` column?

`banc.py::_dominant_region` reconstructs a neuron's region by scanning its
neuropil tag lists (first tag that maps, input list before output list). But the
release we already download ships an explicit per-neuron `Top in/out region`
column in neurons.csv.gz, which the ingest never consults.

The reconstructed rule agrees with that column only 68.7% of the time, and on
the flight power muscles it is worst of all: the release labels DLM1-4 `WTCT`
(wing tectulum) while the leading-tag rule files them under the *first* tag that
happens to map, which for those cells is `IntTct` -> VENTRAL_NERVE_CORD.

This measures the consequence of switching to the release column as the primary
source (tag rule kept as fallback):

  * how many neurons each source covers,
  * where motor cells land, in particular the three regions `readMotorDrive`
    actually reads,
  * what the flight power muscles (DLM/DVM) become.

This is a coverage measurement, not an argument for a biology claim - the
biology is settled separately against the literature.

Run:  python3 tools/measure_release_region_column.py
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
READOUT = ("LEG_NEUROMERE", "WING_NEUROPIL", "SUBESOPHAGEAL_ZONE",
           "HALTERE_NEUROPIL")


def as_list(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)] if v else []


def main() -> int:
    data = ROOT / "data/raw/flywire/banc"
    csv_path, pkl_path = data / "neurons.csv.gz", data / "neuron_attributes.pickle.gz"
    if not csv_path.exists() or not pkl_path.exists():
        print("raw release not present; nothing to measure")
        return 0

    table: dict[int, dict] = {}
    with gzip.open(csv_path, "rt") as fh:
        for row in csv.DictReader(fh):
            try:
                table[int(row["Root ID"])] = row
            except (TypeError, ValueError):
                continue
    attrs = pickle.load(gzip.open(pkl_path, "rb"))

    mappable = 0
    no_col = 0
    col_unmapped = 0
    both = 0
    agree = 0
    fallback_used = 0
    pool_col = Counter()
    pool_tag = Counter()
    readout_col = Counter()
    readout_tag = Counter()
    flight_col = Counter()
    flight_tag = Counter()
    unmapped_tags = Counter()

    for rid, a in table.items():
        attrs_a = attrs.get(rid, {})
        super_class = (a.get("Super Class") or "").strip().lower()
        motor = "motor" in super_class
        names = " ".join(as_list(attrs_a.get("cell_type"))
                         + as_list(attrs_a.get("resolved_type"))).upper()
        tone = " ".join([a.get("Primary Cell Type") or "",
                         a.get("Class") or "",
                         a.get("Sub Class") or ""]).upper()
        is_flight = any(k in (names + " " + tone) for k in ("DLM", "DVM"))

        tag_rule = _dominant_region(attrs_a, (a.get("Soma side") or "").lower(),
                                    Counter())[0]

        col = (a.get("Top in/out region") or "").strip()
        col_region = map_neuropil(col) if col else None
        if col_region is None:
            if col:
                col_unmapped += 1
                unmapped_tags[col.split(".")[0]] += 1
            else:
                no_col += 1
            final = tag_rule
            fallback_used += 1
        else:
            final = col_region
            mappable += 1
        if col_region is not None and tag_rule == col_region:
            agree += 1
        both += 1

        if motor:
            pool_col[NAMES.get(final)] += 1
            pool_tag[NAMES.get(tag_rule)] += 1
            if NAMES.get(final) in READOUT:
                readout_col[NAMES.get(final)] += 1
            if NAMES.get(tag_rule) in READOUT:
                readout_tag[NAMES.get(tag_rule)] += 1
        if is_flight:
            flight_col[NAMES.get(final)] += 1
            flight_tag[NAMES.get(tag_rule)] += 1

    print(f"neurons in table: {both:,}")
    print(f"  have a mappable `Top in/out region`: {mappable:,} "
          f"({100*mappable/max(both,1):.1f}%)")
    print(f"  column absent:                       {no_col:,}")
    print(f"  column present but unmappable:       {col_unmapped:,}")
    print(f"  tag rule would be used as fallback:  {fallback_used:,} "
          f"({100*fallback_used/max(both,1):.1f}%)")
    print(f"  tag rule agrees with column:         {agree:,} "
          f"({100*agree/max(mappable,1):.1f}% of mappable)")

    print("\nmotor cells in the regions readMotorDrive reads:")
    print(f"  {'region':<22}{'via release column':>20}{'via tag rule (now)':>20}")
    for r in READOUT:
        print(f"  {r:<22}{readout_col.get(r,0):>20}{readout_tag.get(r,0):>20}")

    print("\nflight power muscles (DLM/DVM):")
    print(f"  via release column: {dict(sorted(flight_col.items(), key=lambda kv: -kv[1]))}")
    print(f"  via tag rule (now): {dict(sorted(flight_tag.items(), key=lambda kv: -kv[1]))}")

    print("\nall motor cells, top regions:")
    print("  via release column:")
    for r, n in pool_col.most_common(8):
        print(f"      {str(r):<24}{n:>6}")
    print("  via tag rule (now):")
    for r, n in pool_tag.most_common(8):
        print(f"      {str(r):<24}{n:>6}")

    if unmapped_tags:
        print("\nunmappable `Top in/out region` values (need a mapping entry):")
        for t, n in unmapped_tags.most_common(10):
            print(f"      {t:<24}{n:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())