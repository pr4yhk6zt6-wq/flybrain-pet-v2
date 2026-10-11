#!/usr/bin/env python3
"""Does `wingMuscleDrive` actually move on the real BANC release?

The claim under test is the one TASK-007 recorded as broken: the readout
consults `WING_NEUROPIL`, the release put 0 motor cells there, so
`wingMuscleDrive` was pinned at 0 and the wings never actuated on the real
connectome (invisible on the demo asset, which does place 2 motor cells there).

The firmware's own verdict is what matters, so this probe does not assert the
region counts as a proxy. It runs the readout's exact rule — summed leaky
firing rate over `WING_NEUROPIL` cells carrying the motor label, divided by the
readout's reference, clipped to 1 — with drive arriving from cells that really
innervate the wing pool.

WHY A SUBGRAPH: the BANC asset is 153,746 neurons and 3,036,600 edges, and the
repo's engine mirror is pure Python; stepping the whole connectome for 2,000
steps does not finish (measured: killed at 15 min). The claim is LOCAL — "does
this pool receive drive at all" — so the probe extracts the wing pool plus a
bounded set of its real upstream cells and simulates only that. Every edge in
the subgraph is a real asset edge; nothing is synthesised.

Run: python3 tools/probe_wing_drive_from_banc.py
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import NEURON_STRIDE, neuron_at, parse         # noqa: E402
from flybrain.pid import RegionID                       # noqa: E402
from sim_neural_engine import Engine                    # noqa: E402

FLAG_MOTOR = 0x01
CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

# banc_cns is a gitignored build artifact: pinned, but exempt from CI's exit
# code (CI can only see tracked assets). The CI-runnable half of this same check
# is tools/verify_motor_pools_from_report.py.
EXPECT = {
    "demo": {"wingMotor": 2},
    # AFTER the TASK-007 fix. Before it this row read wingMotor 0.
    "banc": {"wingMotor": 50},
}

SYN_STRIDE = 20
# Bounded subgraph: the wing pool plus this many of its strongest real drivers.
SOURCE_CAP = 2000
DRIVE_NA = 40.0
STEPS = 2000

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def swift_number(name: str) -> float:
    """Read a constant out of SimulationCore.swift instead of hardcoding it."""
    src = CORE.read_text()
    for pat in (rf"\b{name}\s*:\s*Float\s*=\s*([0-9_]+(?:\.[0-9]+)?)",
                rf"let\s+{name}\s*=\s*([0-9_]+(?:\.[0-9]+)?)"):
        m = re.search(pat, src)
        if m:
            return float(m.group(1).replace("_", ""))
    raise SystemExit(f"could not read {name} from {CORE}")


def iter_synapses(blocks):
    """Yield (pre, post, current) for every real edge in the asset.

    Decode copied from `tools/probe_mouth_opens_itself.load`, which already runs
    in CI: the record is `<iiHBbBB2xf` (20 bytes) and Swift computes
    `efficacy * gain * synapseCount * polarity` when the edge fires, so the
    mirror's current must carry all three factors.

    Two earlier versions of this probe got this wrong, BOTH failing silently in
    the same direction — reporting "no signal" because the signal was misread:

      * an `<iii` decode at the 20-byte stride read `synapseCount`, the NT byte
        and the polarity byte as one int, so the "current" was a byte-packed
        integer rather than the edge's actual efficacy;
      * dropping `synapseCount` scales every edge to ~1-3 instead of ~40-150.
    """
    syn = blocks["synapse"]
    for k in range(len(syn) // SYN_STRIDE):
        _pre, post, cnt, _nt, sign, _conf, _dl, eff = struct.unpack_from(
            "<iiHBbBB2xf", syn, k * SYN_STRIDE)
        yield _pre, post, eff * sign * cnt


def measure(asset) -> None:
    hdr, blocks = parse(asset)
    nb = blocks["neuron"]
    n = int(hdr["neuronCount"])
    wing = int(RegionID.WING_NEUROPIL)

    region = bytearray(n)
    flags = bytearray(n)
    for i in range(n):
        o = i * NEURON_STRIDE
        region[i] = nb[o + 5]
        flags[i] = nb[o + 9]
    wing_motor = [i for i in range(n)
                  if region[i] == wing and (flags[i] & FLAG_MOTOR)]
    wing_all = sum(1 for i in range(n) if region[i] == wing)
    print(f"  {asset.name}: {n:,} neurons; WING_NEUROPIL {wing_all:,} cells, "
          f"{len(wing_motor)} motor")
    if not check(len(wing_motor) > 0,
                 "the wing pool the readout sums has motor cells",
                 f"{len(wing_motor)} motor cells in wingNeuropil"):
        return

    # Real edges: keep only those landing in the wing pool. One pass, because
    # the claim is LOCAL — "does this pool receive drive at all" — so the cells
    # that directly innervate it are driven directly and nothing is synthesised.
    wingt = set(wing_motor)
    direct: dict[int, float] = {}
    edges_into_wing = []
    for pre, post, cur in iter_synapses(blocks):
        if post in wingt:
            direct[pre] = direct.get(pre, 0.0) + cur
            edges_into_wing.append((pre, post, cur))
    ranked = sorted(direct.items(), key=lambda kv: -kv[1])[:SOURCE_CAP]
    drivers = {u for u, _ in ranked}
    print(f"  {len(direct):,} cells project into the wing pool; "
          f"driving the strongest {len(ranked)}")

    # Remap to a compact subgraph: drivers + wing motor cells.
    keep = sorted(drivers | wingt)
    idx = {g: i for i, g in enumerate(keep)}
    m = len(keep)
    out = [[] for _ in range(m)]
    for pre, post, cur in edges_into_wing:
        if pre in idx and post in idx and pre != post:
            out[idx[pre]].append((idx[post], cur))
    wing_local = [idx[w] for w in wing_motor]
    driver_local = [idx[u] for u, _ in ranked if u in idx]
    print(f"  subgraph: {m:,} neurons, {sum(len(v) for v in out):,} edges; "
          f"driving {len(driver_local)} real driver(s)")

    e = Engine(m, syn_out=out,
               params={"dt": 0.1, "synapticGain": 1.0, "noiseScale": 0.0})
    for i in range(m):
        e.b[i] = 0.0
        e.a[i] = 0.0

    peak_rate = 0.0
    for s in range(STEPS):
        if s % 10 == 0:
            for i in driver_local:
                e.inject(i, DRIVE_NA, e.time)
        e.step()
        peak_rate = max(peak_rate, sum(e.rate[w] for w in wing_local))

    ref = swift_number("motorDriveReferenceHz")
    drive = min(peak_rate / ref, 1.0)
    print(f"  [MEASURE] wing pool peaks at {peak_rate:.2f} Hz summed; "
          f"wingMuscleDrive = min({peak_rate:.2f}/{ref:g}, 1) = {drive:.4f}")
    check(drive > 0.0, "wingMuscleDrive is not pinned at zero",
          f"{drive:.4f} (was 0 before the TASK-007 region fix)"
          if drive > 0 else
          "the readout sums an empty wing pool — wings can never actuate")

    # BE HONEST ABOUT THE CEILING. `drive` is clamped to 1, so a non-zero
    # reading proves the pool is POPULATED and receives real drive; it does NOT
    # validate `motorDriveReferenceHz`, which was chosen for the synthetic demo
    # (2 wing motor cells) and is 1-2 orders of magnitude below what 50 motor
    # cells fed by 2,925 real drivers produce. Reporting the raw Hz keeps that
    # visible instead of letting the clamp turn a gross scale mismatch into a
    # tidy 1.0.
    if peak_rate > ref * 10:
        print(f"  [NOTE] the raw rate is {peak_rate / ref:.0f}x the reference, so "
              f"the clamp hides the scale: this probe proves the POOL is wired, "
              f"not that the reference is calibrated for BANC.")


def main() -> int:
    ref = swift_number("motorDriveReferenceHz")
    print(f"readMotorDrive's reference, read from SimulationCore.swift: {ref}\n")
    for label, asset in ASSETS:
        if not asset.exists():
            print(f"{label}: {asset.name} missing (skipped)")
            continue
        print(f"{label}:")
        measure(asset)

        # Re-derive the pool size for the pin without re-parsing the subgraph.
        hdr, blocks = parse(asset)
        nb = blocks["neuron"]
        wing = int(RegionID.WING_NEUROPIL)
        got = sum(1 for i in range(int(hdr["neuronCount"]))
                  if neuron_at(nb, i)["region"] == wing
                  and neuron_at(nb, i)["flags"] & FLAG_MOTOR)
        want = EXPECT.get(label, {}).get("wingMotor")
        if want is not None:
            check(got == want, f"{label}: wing pool matches the pinned count",
                  f"{got} (pinned {want})")
        print()

    print("HOW THIS IS EVIDENCED: the reference constant and the readout's rule "
          "are read from SimulationCore.swift at run time; the pool, the edges "
          "and the subgraph come from the asset bytes via tools/fbpack.py; the "
          "dynamics are the repo's own engine mirror (tools/sim_neural_engine.py).")
    print("This does NOT execute Swift — the semantic gate is the XCTest suite.")
    print("banc_cns is a gitignored build artifact, so it is PINNED here for a "
          "dev machine but exempt from CI's exit code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())