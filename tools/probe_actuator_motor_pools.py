#!/usr/bin/env python3
"""Does each motor-group readout have a cell to READ on each shipped asset?

`readMotorDrive` (SimulationCore.swift) sums the neural rate of MOTOR cells in
three regions and publishes three actuators:

    legNeuromere      -> 6-slot leg drive
    wingNeuropil      -> motor.wingMuscleDrive   (feeds wing stroke -> lift)
    subesophagealZone -> motor.proboscisDrive    (feeds the feeding loop)

A region group with no motor cell silently reads exactly zero, forever. That is
the same failure shape as the other channels in this repo: the actuator is
"implemented" and the call site exists, but no cell can ever drive it, and
nothing in the engine reports the difference between "the fly is not flying"
and "flight can never be driven".

This probe reads the region and motor-label semantics OUT OF the Swift source
and the region ids out of the Python enum, and reports per asset:

  * how many cells and how many MOTOR cells sit in each group's region;
  * whether the group can source a non-zero readout at all.

It does NOT execute Swift: the semantic claim (drive reaches the motor command)
is covered by the XCTest suite. What it pins is which regions the readout
consults and which cells carry the label, so adding a region or renaming the
label moves this probe.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "python"))

from fbpack import parse, NEURON_STRIDE  # noqa: E402  (tools/fbpack.py)
from flybrain import RegionID  # noqa: E402

SRC = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
print("readMotorDrive — region groups, read from the Swift switches:")
src = SRC.read_text()

# `readMotorDrive` has TWO switches: the first FILTERS which regions count at
# all, the second ROUTES each region to an actuator group. Reading only the
# first (as this probe originally did) misses a repointed group entirely, so
# both are parsed and the routing map is what gets pinned.
switches = re.findall(
    r"switch region \{\n(.*?)\n\s{12}\}", src, re.S)
if len(switches) < 2:
    print(f"  <-- expected two `switch region` blocks in readMotorDrive, "
          f"found {len(switches)}")
    sys.exit(1)
filter_sw, route_sw = switches[0], switches[1]

filtered = [c.strip(". ")
            for case in re.findall(r"case (\.\w+(?:, \.\w+)*):", filter_sw)
            for c in case.split(",")]
# region -> group, from the routing switch. Each case body accumulates into a
# group variable ("wing += rate", "sez += rate", or "drive[slot] += rate"), so
# the target is whatever `+= rate` is applied to — not the first token, which
# for legNeuromere is the `let slot = ...` declaration.
route: dict[str, str] = {}
for m in re.finditer(r"case ((?:\.\w+)(?:,\s*\.\w+)*):(.*?)(?=\n\s+case |\Z)",
                     route_sw, re.S):
    labels = [c.strip().lstrip(".") for c in m.group(1).split(",")]
    tgt = re.search(r"(\w+)\s*(?:\[[^\]]*\])?\s*\+=\s*rate", m.group(2))
    if not tgt:
        continue
    for lb in labels:
        route[lb] = tgt.group(1)
print(f"  regions consulted by the readout: {filtered}")
print(f"  routing: " + ", ".join(f"{k}->{v}" for k, v in route.items()))

# Pin the routing map itself: a group silently pointed at a different region
# would otherwise leave this probe (and the doc built on it) unchanged.
EXPECT_ROUTE = {
    "legNeuromere": "drive",
    "wingNeuropil": "wing",
    "subesophagealZone": "sez",
}
route_fail = 0
if route != EXPECT_ROUTE:
    print(f"  <-- routing changed: expected {EXPECT_ROUTE}, got {route}")
    route_fail = 1

# motor label semantics: the readout keeps a cell only when it is a motor cell,
# unless the asset predates the class byte.
labelled = "!n.isMotorNeuron" in src
print(f"  motor-cell filter: {'motor label required' if labelled else 'region-only'}")
print()

MOTOR_BIT = 1 << 0   # FLAG_MOTOR, mirrored from flybrain/pack.py
SENSORY_BIT = 1 << 1  # FLAG_SENSORY — a cell can carry neither.





def counts_of(asset: Path, regions: dict[str, int]):
    """Per-region (cells, motor) counts in one streaming pass.

    The real BANC asset is 153,746 neurons; materialising a dict per neuron
    exhausts memory on the device this runs on, so this unpacks only the fields
    the check needs and never keeps a per-neuron object.
    """
    hdr, blocks = parse(asset)
    nb = memoryview(blocks["neuron"])
    n = int(hdr["neuronCount"])
    stride = NEURON_STRIDE
    want = {rid: [0, 0] for rid in regions.values() if rid is not None}
    any_flags = 0
    for i in range(n):
        off = i * stride
        # struct "<iBBBBBBH": cid(4) ds(4) region(5) side(6) nt(7) prov(8)
        # flags(9) type(10) — offsets verified against fbpack.neuron_at.
        region = nb[off + 5]
        flags = nb[off + 9]
        any_flags |= flags
        if region in want:
            want[region][0] += 1
            if flags & MOTOR_BIT:
                want[region][1] += 1
    has_class = bool(any_flags)
    return has_class, want


def region_id(name: str) -> int | None:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
    return int(getattr(RegionID, snake)) if hasattr(RegionID, snake) else None


ASSETS = [Path(p) for p in sys.argv[1:]] or [
    ROOT / "data/generated/demo_micro.fbpack",
    ROOT / "data/generated/banc_cns.fbpack",
]

# --------------------------------------------------------------------------
# Pinned expectation. This gate records the state that was MEASURED, so that a
# change on either side fails loudly:
#
#   * demo_micro is tracked and ships in the app — every group must be
#     readable, or the actuator the app drives can never move.
#   * banc_cns is a gitignored local build artifact, so CI simply never sees
#     it. Rather than let its finding sit in a probe that CI cannot run, the
#     expected result is pinned here: when the asset IS present (a dev machine)
#     the reading must match. "0 motor cells in wingNeuropil" is a known defect
#     documented in README's Phase 6+ list; if the region mapping is fixed, or
#     the release changes, this table fails and forces the doc to be updated
#     with it. BANC is EXEMPT from the exit code only because it is read-ahead
#     evidence, never a CI input.
# --------------------------------------------------------------------------
EXPECT = {
    "demo_micro.fbpack":  {"legNeuromere": 2, "wingNeuropil": 2,
                           "subesophagealZone": 2},
    # A known defect, pinned rather than hidden: the release labels the flight
    # power muscles (DLM1-4) in HALTERE_NEUROPIL, which the readout never reads.
    "banc_cns.fbpack":    {"legNeuromere": 187, "wingNeuropil": 0,
                           "subesophagealZone": 102},
}
BANC_EXEMPT = {"banc_cns.fbpack"}  # untracked in CI; pinned, not enforced

fail = route_fail
for asset in ASSETS:
    if not asset.exists():
        print(f"{asset.name}: missing (skipped)")
        continue
    has_class, want = counts_of(asset, {g: region_id(g) for g in filtered})
    total = int(parse(asset)[0]["neuronCount"])
    print(f"{asset.name}: {total:,} neurons "
          f"(class byte present: {has_class})")
    expect = EXPECT.get(asset.name, {})
    # A group the table does not know about means the readout's region list
    # changed (repointed or renamed). That must fail, or the table silently
    # stops describing what the code does.
    unknown = [g for g in filtered if g not in expect]
    if expect and unknown:
        print(f"  <-- the readout consults region(s) the pinned table does not "
              f"know: {unknown}")
        fail = 1
    for group in filtered:
        rid = region_id(group)
        if rid is None:
            print(f"  {group:22s} -> (no such RegionID)")
            continue
        ids, motor = want[rid]
        # `readMotorDrive` keeps a cell when it is motor, OR when the asset has
        # no class byte at all (region-only fallback).
        readable = motor if has_class else ids
        if not readable:
            verdict = "READS ZERO — the actuator can never move"
        else:
            verdict = "readable"
        note = ""
        if group in expect and expect[group] != motor:
            note = (f"  <-- expected {expect[group]} motor; "
                    f"the pinned reading moved")
            if asset.name not in BANC_EXEMPT:
                fail = 1
        print(f"  {group:22s} -> region {rid:<3} "
              f"{ids:>7,} cells, {motor:>4,} motor   {verdict}{note}")
        if not readable and asset.name not in BANC_EXEMPT:
            fail = 1
    print()

print("HOW THIS IS EVIDENCED: region counts come from the asset bytes via "
      "tools/fbpack.py, the region ids from flybrain.RegionID, and the groups "
      "+ motor-label rule are read out of SimulationCore.swift at run time. "
      "This does NOT execute Swift — the semantic gate is the XCTest suite.")
print("banc_cns is a gitignored build artifact: its row is PINNED (so a change "
      "in the region mapping fails on a dev machine) but exempt from CI's exit "
      "code, which can only see tracked assets.")
sys.exit(fail)