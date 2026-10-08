#!/usr/bin/env python3
"""Measure what `SimulationCore.readMotorDrive` is actually summing.

The readout adds the firing rate of EVERY neuron whose `region` is
`legNeuromere`, `wingNeuropil` or `subesophagealZone`. On a synthetic fixture
that is harmless, because the fixture puts nothing else in those regions. On the
real BANC release those neuropils also contain the first-order SENSORY afferents
(tarsal load, joint angle, campaniform strain report INTO the neuromere) and the
local VNC intrinsic interneurons.

If sensory afferents are included the readout is not a motor command: it is
whatever came in through the senses plus local activity, so it tracks the
stimulus instead of the motor output and the "closed loop" partially measures
its own input.

The release labels each neuron's `Super Class`, and the ingest assigns `region`
with `flybrain.neuropil.map_neuropil`, so the size of the error is measurable
rather than arguable. This script runs the REAL ingest mapping (`_dominant_region`
+ `map_neuropil`) over the RELEASE files, so the composition it reports is the
composition the simulator would actually build.

It also checks whether the readout can even separate the motor neurons from the
rest: the asset stores region and cell type, and nothing else.
"""
from __future__ import annotations

import collections
import os
import struct
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))

from flybrain import banc  # noqa: E402
from flybrain.pid import RegionID  # noqa: E402

READOUT_REGIONS = {
    int(RegionID.LEG_NEUROMERE): "legNeuromere",
    int(RegionID.WING_NEUROPIL): "wingNeuropil",
    int(RegionID.HALTERE_NEUROPIL): "haltereNeuropil",
    int(RegionID.SUBESOPHAGEAL_ZONE): "subesophagealZone",
}

# Which of these the readout actually sums (SimulationCore.readLegDrive etc.)
# is checked in Swift; this measures the composition of all of them.
RELEASE = os.path.join(HERE, "..", "data", "raw", "flywire", "banc")


def main() -> int:
    neuron_csv = os.path.join(RELEASE, "neurons.csv.gz")
    attr_pickle = os.path.join(RELEASE, "neuron_attributes.pickle.gz")
    if not (os.path.exists(neuron_csv) and os.path.exists(attr_pickle)):
        print(f"release not present under {RELEASE}; run tools/fetch_banc.py first")
        return 0

    table = banc._load_neuron_table(banc.Path(neuron_csv))
    attrs = banc._load_neuron_attributes(banc.Path(attr_pickle))

    by_region: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    type_by_region: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    unmapped: collections.Counter = collections.Counter()
    motor_elsewhere = collections.Counter()
    total = 0

    for rid, row in table.items():
        total += 1
        sc = (row.get("Super Class") or "(unlabelled)").strip() or "(unlabelled)"
        region, _ = banc._dominant_region(attrs.get(rid, {}),
                                          (row.get("Soma side") or "").strip().lower(),
                                          unmapped)
        if sc == "motor" and region not in READOUT_REGIONS:
            motor_elsewhere[RegionID(region).name if region in
                            {int(r) for r in RegionID} else str(region)] += 1
        name = READOUT_REGIONS.get(region)
        if name is None:
            continue
        by_region[name][sc] += 1
        primary = (row.get("Primary Cell Type") or row.get("Class") or "").strip()
        type_by_region[name][f"{sc} / {primary}"] += 1

    print(f"BANC release rows read: {total:,}")
    print("region assigned with the ingest's own mapping "
          "(_dominant_region + map_neuropil)\n")
    print("WHAT THE MOTOR READOUT SUMS OVER\n")
    for name in ("legNeuromere", "wingNeuropil", "haltereNeuropil", "subesophagealZone"):
        c = by_region[name]
        n = sum(c.values())
        motor = c.get("motor", 0)
        sensory = c.get("sensory", 0) + c.get("sensory_ascending", 0) + c.get("sensory_descending", 0)
        print(f"  {name}: {n:,} neurons")
        for sc, k in c.most_common():
            print(f"      {sc:<32} {k:>7,}  {100.0*k/max(n,1):5.1f}%")
        print(f"      -> motor {motor:,} ({100.0*motor/max(n,1):.1f}%), "
              f"sensory {sensory:,} ({100.0*sensory/max(n,1):.1f}%), "
              f"non-motor {n-motor:,} ({100.0*(n-motor)/max(n,1):.1f}%)")
        print()

    print("Cell types the readout would actually be reading (top 8 per region):\n")
    for name in ("legNeuromere", "wingNeuropil"):
        print(f"  {name}:")
        for t, k in type_by_region[name].most_common(8):
            print(f"      {k:>6}  {t}")
        print()

    print("Motor-super-class neurons the readout CANNOT see "
          "(they sit in a non-readout region):")
    for r, k in motor_elsewhere.most_common(10):
        print(f"      {r:<28} {k:>6}")

    # The counts above are derived from the RELEASE with the ingest's mapping.
    # The asset is that mapping applied and glia dropped, so the two differ by
    # exactly the glia the ingest removes. Read the asset too, so the numbers
    # the simulator actually sees are the ones on screen — and so a drift
    # between mapping and asset shows up here instead of in a silent
    # mis-attribution later.
    asset = Path(__file__).resolve().parent.parent / "data/generated/banc_cns.fbpack"
    if asset.exists():
        print("\nAS SEEN BY THE SIMULATOR (cell classes in the committed asset)\n")
        import struct
        blob = asset.read_bytes()
        hlen = struct.unpack_from("<Q", blob, 0)[0]
        off = 8 + hlen
        nbytes = struct.unpack_from("<Q", blob, off)[0]; off += 8
        stride = 44
        count = nbytes // stride
        READOUT = {16: "legNeuromere", 17: "wingNeuropil", 13: "subesophagealZone"}
        per = {v: [0, 0, 0] for v in READOUT.values()}   # motor, sensory, other
        for i in range(count):
            r = blob[off + i * stride + 5]
            f = blob[off + i * stride + 9]
            if r in READOUT:
                slot = 0 if f & 1 else (1 if f & 2 else 2)
                per[READOUT[r]][slot] += 1
        for reg, (mo, se, ot) in per.items():
            tot = mo + se + ot
            print(f"  {reg}: {tot:,} neurons — motor {mo} ({100.0*mo/max(tot,1):.1f}%), "
                  f"sensory {se} ({100.0*se/max(tot,1):.1f}%), other {ot}")
        motor_total = sum(1 for i in range(count) if blob[off + i * stride + 9] & 1)
        in_reg = sum(per[r][0] for r in per)
        print(f"\n  motor neurons in the asset: {motor_total} "
              f"(in a readout region {in_reg}, invisible {motor_total - in_reg})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())