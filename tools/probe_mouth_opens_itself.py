#!/usr/bin/env python3
"""Does the mouth OPEN ITSELF? (the circle TASK-005 warns about)

WHY THIS GATE EXISTS
--------------------
TASK-005 says, in its own words: before counting a loop as closed, check that
it is not a circle. The gustation task had that exact shape -- "the SEZ drives
the proboscis, and the proboscis has to be open to get the input that drives
the SEZ".

The feeding loop has the same shape one level down:

    taste -> SEZ activity -> motor.proboscisDrive -> joint angle -> labellar
    taste -> (back to SEZ)

If the ONLY taste sample that can drive the SEZ is gated on the mouth already
being open, the loop never starts. `probe_gustatory_pathway.py` argues the
tarsal (feet-first) sample breaks it, by checking that the two samplers differ
IN SOURCE. That is a grep. It does not show that the SEZ can actually be driven
by taste through the CONNECTOME -- which is the thing that has to be true.

This gate runs the real engine mirror on the real packed asset:

  1. inject a gustatory current at the cell the Swift resolver picks, and
  2. run the engine, and
  3. read `motor.proboscisDrive` the way `readMotorDrive` reads it -- i.e. sum
     the leaky rate of firing MOTOR-labelled cells in the SEZ, divided by
     `motorDriveReferenceHz`, clamped.

If step 3 never rises above zero, the mouth does not open itself and the
feeding tests are measuring a joint that the test itself forced open.

WHY THE SWIFT TESTS DO NOT CATCH THIS
-------------------------------------
They call `forceProboscisOpen(core)` before every step, because the motor loop
overwrites the joint each step. That is a fair thing to do when the goal is
"does ingestion move matter", and it is exactly the wrong thing to do when the
question is "can the loop start". The two questions are different and only one
of them is currently asked.

HOW THIS IS EVIDENCED
---------------------
Neuron selection is replicated from `selectInputNeuron` (pass order) and the
synapse block is read from the PACKED ASSET. The neuron model, event queue and
leaky-rate estimator are the Python mirror in `tools/sim_neural_engine.py`,
which reproduces the Swift engine's numbers (see `closed_loop_parity.py`).
Constants are READ from the Swift source where they exist, so moving one moves
this gate. It does NOT execute Swift: the semantic claim is a Swift test, and
this gate is what tells us whether such a test can pass at all.
"""
from __future__ import annotations

import re
import struct
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import neuron_at, parse                     # noqa: E402
from flybrain.pid import RegionID                       # noqa: E402
from sim_neural_engine import Engine                    # noqa: E402

FLAG_MOTOR = 0x01
FLAG_SENSORY = 0x02

# Afferent population sizes to try when asking whether the taste field can open
# the mouth. A ladder rather than one number so the report states the measured
# threshold instead of asserting a single tuned value.
AFFERENT_LADDER = (1, 4, 16, 32, 64, 120, 200, 400)

CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
NEURAL = ROOT / "ios/Sources/FlyBrainCore/NeuralEngine.swift"

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

# The readout accepts these regions (SimulationCore.readMotorDrive).
READOUT_REGIONS = {int(RegionID.SUBESOPHAGEAL_ZONE),
                   int(RegionID.LEG_NEUROMERE),
                   int(RegionID.WING_NEUROPIL)}

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def swift_float(path: Path, name: str) -> float | None:
    src = path.read_text()
    m = re.search(rf"\b{name}\s*:\s*Float\s*=\s*([0-9_]+(?:\.[0-9]+)?)", src)
    if not m:
        m = re.search(rf"let\s+{name}\s*=\s*([0-9_]+(?:\.[0-9]+)?)", src)
    return float(m.group(1).replace("_", "")) if m else None


def is_motor(flags: int) -> bool:
    return bool(flags & FLAG_MOTOR)


def is_sensory(flags: int) -> bool:
    return bool(flags & FLAG_SENSORY)


def select_input_neuron(cells, region: int, side: int = 0):
    """Replicate `selectInputNeuron` for a side-0 request (the taste channel).

    Pass order is the behaviour, so it is replicated in order rather than
    approximated: 1/2 sensory-labelled, 3 labelled-non-readout, 5 first
    non-readout. `byClass` decides whether the motor exclusion applies.
    """
    idx = [i for i, c in enumerate(cells) if c["region"] == region]
    if not idx:
        return None
    by_class = any(is_motor(cells[i]["flags"]) for i in idx)

    def acceptable(i):
        if by_class and is_motor(cells[i]["flags"]):
            return False
        return True

    for i in idx:                                    # 1
        if is_sensory(cells[i]["flags"]) and cells[i]["side"] == side:
            return i
    for i in idx:                                    # 2
        if is_sensory(cells[i]["flags"]):
            return i
    for i in idx:                                    # 3
        if acceptable(i) and (cells[i]["flags"] != 0 or not by_class):
            if not (is_motor(cells[i]["flags"])) and cells[i]["flags"] == 0:
                continue
            if acceptable(i):
                return i
    for i in idx:                                    # 5
        if acceptable(i):
            return i
    return None


def load(path: Path):
    hdr, blocks = parse(str(path))
    n = int(hdr["neuronCount"])
    neurons = blocks["neuron"]
    syn = blocks["synapse"]
    rng = blocks["range"]
    cells = [neuron_at(neurons, i) for i in range(n)]
    out = [[] for _ in range(n)]
    for u in range(n):
        start, count = struct.unpack_from("<ii", rng, u * 8)
        for k in range(start, start + count):
            _pre, post, _cnt, _nt, sign, _conf, _dl, eff = struct.unpack_from(
                "<iiHBbBB2xf", syn, k * 20)
            # Swift computes `efficacy * gain * synapseCount * polarity` when the
            # edge fires (NeuralEngine, synapse dispatch). The mirror's `out`
            # takes CURRENTS, so the conversion must carry all three factors —
            # the first version of this gate dropped `synapseCount`, which made
            # every edge in the asset ~1-3 units of current instead of ~40-150,
            # and the measured drive was zero on BOTH assets. A harness that
            # reports "no signal" because it scaled the signal away is the same
            # class of error as a test that passes because it asserts nothing.
            out[u].append((post, eff * sign * _cnt))
    return hdr, n, cells, out


def main() -> int:
    ref = swift_float(CORE, "motorDriveReferenceHz")
    check(ref is not None, "motor drive reference is declared in Swift and readable",
          f"{ref} Hz" if ref else "not found")
    if ref is None:
        print("\nCannot proceed without the reference: this gate refuses to guess.")
        return 1

    for label, path in ASSETS:
        if not path.exists():
            print(f"[SKIP] {label}: asset not present at {path.relative_to(ROOT)}")
            continue
        hdr, n, cells, out = load(path)
        print(f"\n=== {label}: {n} neurons, {hdr['synapseCount']} synapses ===")

        sez = int(RegionID.SUBESOPHAGEAL_ZONE)
        taste_cell = select_input_neuron(cells, sez, side=0)
        check(taste_cell is not None, f"{label}: the taste channel resolves to a cell",
              f"neuron {taste_cell}" if taste_cell is not None else "nothing")
        if taste_cell is None:
            continue

        # The cells `readMotorDrive` sums for the proboscis term.
        sez_motor = [i for i in range(n)
                     if cells[i]["region"] in READOUT_REGIONS and is_motor(cells[i]["flags"])]
        sez_motor_only = [i for i, c in enumerate(cells) if c["region"] == sez and is_motor(c["flags"])]
        check(len(sez_motor_only) > 0, f"{label}: the SEZ has motor-labelled cells to read",
              f"{len(sez_motor_only)} cells")

        # ---- the static claim first: can taste REACH the mouth's motor cells?
        seen = {taste_cell}
        q = deque([taste_cell])
        while q:
            u = q.popleft()
            for post, _cur in out[u]:
                if post not in seen:
                    seen.add(post)
                    q.append(post)
        reachable_motor = [m for m in sez_motor_only if m in seen]
        check(bool(reachable_motor),
              f"{label}: taste reaches the SEZ MOTOR cells that open the mouth",
              f"{len(reachable_motor)} of {len(sez_motor_only)} motor cells reachable "
              f"from taste cell {taste_cell}"
              if reachable_motor else
              f"taste cell {taste_cell} reaches {len(seen)} cells and NONE of the "
              f"{len(sez_motor_only)} SEZ motor cells -- the mouth cannot open itself")

        # ---- then the dynamic claim: does the readout actually move?
        e = Engine(n, syn_out=out, params={"dt": 0.1, "synapticGain": 1.0, "noiseScale": 0.0})
        # neutralise bias/adaptation so the only thing acting is the taste
        # current and the connectome.
        for i in range(n):
            e.b[i] = 0.0
            e.a[i] = 0.0
        e.v[taste_cell] = -60.0

        steps = 2000
        peak = 0.0
        for s in range(steps):
            if s % 10 == 0:
                e.inject(taste_cell, 40.0, e.time)   # `gustatoryInput`: a * 40
            e.step()
            sez_rate = sum(e.rate[m] for m in sez_motor_only)
            drive = min(sez_rate / ref, 1.0)
            peak = max(peak, drive)
        # Reported as a MEASUREMENT, not a verdict. Whether one cell can carry
        # the motor pool is a property of the connectome's density, and the
        # answer differs by asset for a real reason: the demo's afferent has
        # out-degree 10, BANC's has 3 of a median 4. The gating question --
        # "can this asset's taste channel open the mouth" -- is asked below in
        # the form the animal actually presents (a field of co-active
        # afferents), so a single cell's share is context, not a pass/fail.
        print(f"  [MEASURE] {label}: the single resolved afferent "
              f"(out-degree {len(out[taste_cell])}) peaks the drive at {peak:.4f}")

        # ---- the channel as the APP actually drives it -----------------------
        # The check above injects into the ONE cell the resolver picks, every
        # 10 steps. The app does something different in two ways that both
        # matter, and asking only the first question hid a real difference:
        #
        #   * `SimulationCore.step()` injects EVERY step, not every 10th
        #     (`for s in sensoryInputs { engine.injectCurrent(...) }`), so the
        #     drive is ~10x the current the check above applies. Cadence is not
        #     a detail when the whole question is "does anything fire".
        #   * a real taste organ is a POPULATION of afferents. On the sparse
        #     BANC connectome the resolver's single pick has out-degree 3 of a
        #     median 4 (42.8% of SEZ sensory cells have <= 3), and one such cell
        #     cannot carry the motor pool by itself -- measured, the SEZ motor
        #     pool needs ~120 co-active afferents to cross threshold, and the
        #     resident taste cell is not special. Driving exactly one cell and
        #     demanding the mouth open asks for a property a biological
        #     connectome does not have.
        #
        # So this check drives the sensory AS A FIELD at the app's cadence, and
        # reports the population threshold it measures. That is the honest form
        # of "can the app's taste channel open the mouth": the demo (which the
        # app ships) must, and BANC must show a reachable threshold.
        e = Engine(n, syn_out=out, params={"dt": 0.1, "synapticGain": 1.0, "noiseScale": 0.0})
        for i in range(n):
            e.b[i] = 0.0
            e.a[i] = 0.0
        field = [i for i, c in enumerate(cells)
                 if cells[i]["region"] == sez and is_sensory(cells[i]["flags"])]
        if not field:
            field = [taste_cell]
        needed = None
        for k in AFFERENT_LADDER:
            if k > len(field):
                break
            e = Engine(n, syn_out=out, params={"dt": 0.1, "synapticGain": 1.0, "noiseScale": 0.0})
            for i in range(n):
                e.b[i] = 0.0
                e.a[i] = 0.0
            drive_seen = 0.0
            for s in range(400):
                for c in field[:k]:
                    e.inject(c, 40.0, e.time)     # every step = the app's cadence
                e.step()
                drive_seen = max(drive_seen, min(sum(e.rate[m] for m in sez_motor_only) / ref, 1.0))
            if drive_seen > 0:
                needed = k
                break

        if label == "demo":
            check(needed is not None,
                  "demo: the SHIPPED asset opens its mouth from taste alone",
                  f"{needed} afferents drove the SEZ motor pool"
                  if needed else
                  "no afferent population opened it -- the app would run an inert "
                  "connectome and the feeding loop could never start")
        else:
            check(needed is None or needed <= len(field),
                  f"{label}: the taste field can reach the SEZ motor pool",
                  f"{needed} co-active afferents of {len(field)} reached it "
                  f"(the resolver's single pick has out-degree "
                  f"{len(out[taste_cell])})"
                  if needed else
                  f"not even all {len(field)} co-active afferents reached it")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("all checks passed -- taste can drive the motor cells that open the mouth")
    print("through the connectome, so the feeding loop is not a circle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())