#!/usr/bin/env python3
"""Where are the motor neurons, and can the readout reach them?

`SimulationCore.readMotorDrive` sums firing rate only inside three neuropils
(`legNeuromere`, `wingNeuropil`, `subesophagealZone`) and only from cells
carrying the motor label. Two things can go wrong with that and they look
identical from inside the simulator:

  * the labelled cells are somewhere else, so the group reads zero, and
  * the group reads zero because the fly is not moving.

This measures the first against the RELEASE (via the ingest's own mapping) and
against the committed asset, per group, so "the wing never beats" can be
attributed to the wiring rather than guessed at.

Read-only: prints, changes nothing, exits 0 either way.
"""
from __future__ import annotations

import collections
# import json  # orphaned when the block walk moved to tools/fbpack.py
import os
# import struct  # orphaned when the block walk moved to tools/fbpack.py
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))

from flybrain.pid import RegionID  # noqa: E402

sys.path.insert(0, HERE)
from fbpack import neuron_at, parse  # noqa: E402

MOTOR = 1 << 0
SENSORY = 1 << 1
STRIDE = 44

# The three neuropils `readMotorDrive` consults, and the group each feeds.
READOUT = {
    int(RegionID.LEG_NEUROMERE): "leg drive (6 slots)",
    int(RegionID.WING_NEUROPIL): "wingMuscleDrive",
    int(RegionID.SUBESOPHAGEAL_ZONE): "proboscisDrive",
}

RELEASE = Path(__file__).resolve().parents[1] / "data/raw/flywire/banc"
ASSET = Path(__file__).resolve().parents[1] / "data/generated/banc_cns.fbpack"


def load_asset(path: Path):
    """Header + (region, side, flags) per neuron via the shared reader."""
    hdr, blocks = parse(path)
    nb = blocks["neuron"]
    cells = [(neuron_at(nb, i)["region"], neuron_at(nb, i)["side"],
              neuron_at(nb, i)["flags"])
             for i in range(int(hdr["neuronCount"]))]
    return hdr, cells


def from_asset() -> int:
    if not ASSET.exists():
        print(f"asset not present: {ASSET}")
        return 0
    hdr, cells = load_asset(ASSET)
    print(f"COMMITTED ASSET — {ASSET.name} ({hdr['neuronCount']:,} neurons)\n")

    total_motor = sum(1 for c in cells if c[2] & MOTOR)
    print(f"  motor-labelled neurons in the asset: {total_motor:,}")
    per_region = collections.Counter(c[0] for c in cells if c[2] & MOTOR)
    names = {int(r): r.name for r in RegionID}
    print("\n  motor cells by region (descending):")
    for r, k in per_region.most_common():
        tag = READOUT.get(r)
        mark = f"  <- read as {tag}" if tag else "  <- NOT CONSULTED by the readout"
        print(f"      {names.get(r, str(r)):<26} {k:>6,}{mark}")

    print("\n  what each readout group sums:")
    for r, group in READOUT.items():
        n = sum(1 for c in cells if c[0] == r)
        m = sum(1 for c in cells if c[0] == r and c[2] & MOTOR)
        s = sum(1 for c in cells if c[0] == r and c[2] & SENSORY)
        verdict = ("READS ZERO — no motor cell in this neuropil"
                   if m == 0 else "has a motor pool")
        print(f"      {group:<22} {names.get(r, str(r)):<20} "
              f"{n:>6,} cells ({m} motor, {s} sensory)  {verdict}")

    sides = collections.Counter(c[1] for c in cells)
    print(f"\n  side byte across the whole asset: {dict(sorted(sides.items()))}")
    if 0 not in sides:
        print("      no cell carries side 0, so a channel that asks for side 0")
        print("      resolves to nothing unless the fallback looks past side 0")
    return 0


def from_release() -> int:
    neuron_csv = RELEASE / "neurons.csv.gz"
    if not neuron_csv.exists():
        print("\nrelease not present under data/raw/flywire/banc; skipped")
        return 0
    from flybrain import banc  # noqa: E402

    table = banc._load_neuron_table(banc.Path(str(neuron_csv)))
    attrs = banc._load_neuron_attributes(banc.Path(str(RELEASE / "neuron_attributes.pickle.gz")))

    by_region: collections.Counter = collections.Counter()
    types_by_region: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    side_by_region: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    unmapped: collections.Counter = collections.Counter()
    total_motor = 0

    for rid, row in table.items():
        if (row.get("Super Class") or "").strip().lower() != "motor":
            continue
        total_motor += 1
        a = attrs.get(rid, {})
        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        region, _ = banc._dominant_region(a, side_raw, unmapped)
        name = RegionID(region).name if region in {int(r) for r in RegionID} else str(region)
        by_region[name] += 1
        side_by_region[name][1 if side_raw == "left" else 2 if side_raw == "right" else 0] += 1
        primary = (row.get("Primary Cell Type") or row.get("Class") or "").strip()
        types_by_region[name][primary] += 1

    print(f"\nRELEASE — Super Class 'motor' neurons: {total_motor:,}\n")
    print(f"  {'region':<26} {'motor':>6}  sides")
    for name, k in by_region.most_common():
        print(f"  {name:<26} {k:>6,}  {dict(sorted(side_by_region[name].items()))}")
    print()
    for r, group in READOUT.items():
        name = RegionID(r).name
        k = by_region.get(name, 0)
        print(f"      {group:<22} <- {name:<20} {k:>5,} motor")
        if k == 0:
            print(f"          READS ZERO on the real release: no motor cell is"
                  f" assigned to {name}")
    print("\n  motor cell types the readout CANNOT see (worst 12), by region/type:")
    for name, k in by_region.most_common():
        if name in {RegionID(r).name for r in READOUT}:
            continue
        top = ", ".join(f"{t or '(unlabelled)'} x{n}" for t, n in types_by_region[name].most_common(3))
        print(f"      {name:<24} {k:>5,}  {top}")
    print("\n  wing-group types wherever they sit (name contains WMN or wing):")
    for name in by_region:
        for t, n in types_by_region[name].items():
            lt = t.lower()
            if "wmn" in lt or "wing" in lt or "haltere" in lt:
                print(f"      {name:<24} {t} x{n}")
    return 0


if __name__ == "__main__":
    from_asset()
    from_release()