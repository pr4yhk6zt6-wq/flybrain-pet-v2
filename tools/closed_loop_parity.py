#!/usr/bin/env python3
"""Closed-loop parity gate.

Runs the EXACT topology of `TestSupport.closedLoopConnectome` (4 neurons per
region, 36 neurons) through the Python mirrors of the engine, the motor system
and the body, and checks the behaviour that Swift `WorldTests` asserts.

Why this exists: the Swift test runs on a macOS runner. When the fixture's
parameters were wrong (no intra-region relay; an efficacy below the firing
threshold, so no EPSP ever reached threshold) the previous test still passed,
because its only positional assertion was `position.x >= 0` against a fly
spawned at x = 0. That is a vacuous assertion — it cannot fail. This mirror
reproduces the fixture's real behaviour offline so the numbers in the Swift test
are CALIBRATED rather than guessed, and so a fixture that cannot conduct is
caught before it reaches CI.

Sync with Swift (tools/check_tests.py also cross-checks the numbers):
  * region ids            FlyBrainCore/Types.swift  (RegionID)
  * 4 neurons / region    TestSupport.closedLoopConnectome
  * intra-region chain    i -> i+1
  * sensory -> medulla -> centralComplex (last neuron of each stage)
  * centralComplex -> leg & wing pool (first neuron of each pool)
  * leg/wing pool recurrence 0 <-> 1
  * efficacy 2.0, synapseCount 100, synapticGain 1.0  ->  0.2 mV per spike
  * odour current = min(conc, 1) * 40 - 5, injected into antennal lobe
  * odour concentration = emission * exp(-d^2 / (2D)), D = 100

Exit code is the gate: non-zero means the closed loop is not conducting.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_neural_engine import Engine          # noqa: E402
import sim_body_dynamics as BD                # noqa: E402
import sim_motor_system as MS                 # noqa: E402

DT_MS = 0.1
MAIN_STEPS = 4000          # 400 ms of neural time

# RegionID raw values (types.swift) — order here IS the pid order in the fixture
REGION_ORDER = [
    ("retinaLeft", 1), ("retinaRight", 2), ("lamina", 3), ("antennalLobe", 8),
    ("medulla", 4), ("mushroomBody", 9), ("centralComplex", 11),
    ("legNeuromere", 16), ("wingNeuropil", 17),
]
RL, RR, LAM, AL, ME, MB, CX, LEG, WING = [rid for _, rid in REGION_ORDER]

PER_REGION = 4
EFFICACY = 2.0
SYNAPSE_COUNT = 100.0
SYNAPTIC_GAIN = 1.0

# drive scaling from SimulationCore
DRIVE_REFERENCE_HZ = 100.0
ODOUR_DILUTION = 100.0


def build_connectome(closed: bool = True):
    """Exact mirror of TestSupport.closedLoopConnectome.

    closed=False reproduces `regionalConnectome` instead: every region an
    isolated chain with NO cross-region edges, which is the control that proves
    the motion comes through the connectome rather than around it.
    """
    regions, index_of = [], {}
    for name, rid in REGION_ORDER:
        index_of[rid] = []
        for _ in range(PER_REGION):
            index_of[rid].append(len(regions))
            regions.append(rid)

    out = [[] for _ in regions]
    # edge current, faithful to NeuralEngine: efficacy * gain * synapseCount
    w = EFFICACY * SYNAPTIC_GAIN * SYNAPSE_COUNT

    for _, rid in REGION_ORDER:
        ids = index_of[rid]
        for i in range(len(ids) - 1):
            out[ids[i]].append((ids[i + 1], w))

    if closed:
        sources = (RL, RR, LAM, AL)
        for s in sources:
            out[index_of[s][-1]].append((index_of[ME][0], w))
        out[index_of[ME][-1]].append((index_of[CX][0], w))
        for m in (LEG, WING):
            out[index_of[CX][-1]].append((index_of[m][0], w))
        for m in (LEG, WING):
            out[index_of[m][0]].append((index_of[m][1], w))
            out[index_of[m][1]].append((index_of[m][0], w))
    return regions, index_of, out


def odour_current(concentration: float) -> float:
    """SensoryInterface odour transduction: min(conc,1)*40 - 5."""
    return min(concentration, 1.0) * 40.0 - 5.0


def concentration(emission: float, distance_mm: float) -> float:
    """World odorConcentration: emission * exp(-d^2 / 2D)."""
    return emission * math.exp(-(distance_mm ** 2) / (2.0 * ODOUR_DILUTION))


def run_closed_loop(steps, emission, closed=True):
    """One full arc: scene -> sensory -> connectome -> drive -> CPG -> body."""
    regions, index_of, out = build_connectome(closed=closed)
    engine = Engine(len(regions), syn_out=out)
    engine.motorRateTauMs = 20.0
    motor = MS.MotorSystem()
    body = BD.Body()

    antenna = index_of[AL][0]
    for s in range(steps):
        t = s * DT_MS
        if emission > 0:
            current = odour_current(concentration(emission, 0.1))
            # Swift `odorInput` targets AL side 1 AND side 2; this fixture has
            # side 0 on the antennal lobe, so inputNeuron() falls back to the
            # SAME neuron for both and the current is applied twice.
            engine.inject(antenna, current, t)
            engine.inject(antenna, current, t)
        engine.step()

        drive = [0.0] * 6
        wing = 0.0
        # O(active): walk the leaky-rate set, matching readMotorDrive
        for i in engine.rateRing:
            rate = engine.rate[i]
            if rate <= 0:
                continue
            r = regions[i]
            if r == LEG:
                drive[i % 6] += rate
            elif r == WING:
                wing += rate
        d = [min(x / DRIVE_REFERENCE_HZ, 1.0) for x in drive]
        motor.wing_muscle_drive = min(wing / DRIVE_REFERENCE_HZ, 1.0)
        o = motor.update(d, DT_MS)
        body.step(fwd_target=getattr(o, "forward_speed_target", 0.0),
                  contact=min(1.0, max(0.0, getattr(o, "leg_contact_fraction", 1.0))))

    moved = math.hypot(body.pos[0], body.pos[2])
    return {"moved_mm": moved, "spikes": engine.cumTotal,
            "drive": d, "regions": regions, "engine": engine, "index": index_of}


def main() -> int:
    print("Closed-loop parity with TestSupport.closedLoopConnectome")
    print(f"  topology : {len(REGION_ORDER) * PER_REGION} neurons, "
          f"efficacy {EFFICACY} x {SYNAPSE_COUNT:.0f} synapses x gain {SYNAPTIC_GAIN}")
    # dv per spike = (eff * gain * count) * 10 * (dt / tauM); dt=0.1, tauM=10 ms,
    # so the factor is eff * 10 mV for count=100 and gain=1.
    per_spike_mv = EFFICACY * SYNAPTIC_GAIN * SYNAPSE_COUNT * 10 * (DT_MS / 10.0)
    print(f"  per spike: {per_spike_mv:.1f} mV (threshold gap ~10 mV; "
          f"below efficacy ~1.0 no chain conducts)")
    print()

    fails = []

    short = run_closed_loop(1500, emission=4.0)
    long = run_closed_loop(MAIN_STEPS, emission=4.0)
    no_odour = run_closed_loop(MAIN_STEPS, emission=0.0)
    cut_path = run_closed_loop(MAIN_STEPS, emission=4.0, closed=False)

    print(f"  odour, 150 ms of neural time : {short['moved_mm']:7.3f} mm "
          f"({short['spikes']:5d} spikes)")
    print(f"  odour, 400 ms of neural time : {long['moved_mm']:7.3f} mm "
          f"({long['spikes']:5d} spikes)")
    print(f"  no odour (control)           : {no_odour['moved_mm']:7.3f} mm "
          f"({no_odour['spikes']:5d} spikes)")
    print(f"  cut connectome (control)     : {cut_path['moved_mm']:7.3f} mm "
          f"({cut_path['spikes']:5d} spikes)")
    print()

    # 1) The arc conducts and displaces the animal.
    if long["moved_mm"] <= 0.05:
        fails.append(f"odour does not move the fly ({long['moved_mm']:.3f} mm): "
                     "the sensory->motor path is not conducting")
    # 2) It keeps going rather than stalling.
    if not long["moved_mm"] > short["moved_mm"]:
        fails.append("displacement does not grow with time")
    # 3) Removing the sensory input must stop it. A loop whose output does not
    #    depend on its input is not a loop.
    if no_odour["moved_mm"] > 0.01 or no_odour["spikes"] != 0:
        fails.append(f"the fly moves without sensory input "
                     f"({no_odour['moved_mm']:.3f} mm, {no_odour['spikes']} spikes)")
    # 4) The path must run THROUGH the connectome: with the same stimulus and a
    #    connectome that has no route to the motor pools, nothing may move —
    #    even though sensory neurons fire, so `spikeCount > 0` still holds. This
    #    is exactly the distinction the old vacuous assertion could not make.
    if cut_path["spikes"] <= 0:
        fails.append("the cut-connectome control produced no spikes at all "
                     "(control is not doing what it claims)")
    if cut_path["moved_mm"] > 0.01:
        fails.append(f"the fly moves ({cut_path['moved_mm']:.3f} mm) with no path from "
                     "the sensory region to the motor pools")

    for f in fails:
        print(f"[FAIL] {f}")
    print("FAILED:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())