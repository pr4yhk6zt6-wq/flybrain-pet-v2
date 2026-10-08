#!/usr/bin/env python3
"""Diagnose WHY Swift `WorldTests.testSceneIntegratesIntoClosedLoop` reports the
motor command as zero, while `tools/closed_loop_parity.py` reports a conducting
arc on the "same" fixture.

Two divergences between the mirror and Swift are tested here, one at a time:

 A. OUT-OF-RANGE SEMANTICS.  `TestSupport.closedLoopConnectome` builds its CSR
    with a helper that does `outgoing[from] = OutEdgeRange(start: n, count: 1)`
    — an ASSIGNMENT, not an append. The fixture wires the central complex to
    both motor pools with two calls from the SAME neuron (CX.last -> leg.first
    and CX.last -> wing.first), so the second call silently replaces the first
    and the leg pool loses its only input. The Python mirror appended both
    edges, which is why it disagreed with Swift. `Connectome.validate()` detects
    exactly this ("synapse k referenced by 0 neurons") but no test ran it on
    this fixture.

 B. REGION MODEL PARAMETERS.  `NeuralEngine.init` gives motor/peripheral
    regions tauM = 5 ms, tauRefractory = 1 ms, adaptationCoupling = 0.2,
    spikeTriggeredAdaptation = 0.5, and MB/CX tauM = 15, tauAdapt = 150,
    b = 0.5, a = 2.0. `sim_neural_engine.Engine` accepted a `regions` argument
    and IGNORED it, so every previous closed-loop calibration in this repo ran
    the whole fixture as default LIF (tauM = 10, tauRefractory = 2, b = 0).

Reported for each combination: which neurons spike, the leg drive the readout
would produce, and the displacement.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_neural_engine import Engine          # noqa: E402
import sim_body_dynamics as BD                # noqa: E402
import sim_motor_system as MS                 # noqa: E402

DT_MS = 0.1
DT_SEC = DT_MS / 1000.0
DRIVE_REFERENCE_HZ = 100.0

REGION_ORDER = [
    ("retinaLeft", 1, 1), ("retinaRight", 2, 2), ("lamina", 3, 0), ("antennalLobe", 8, 0),
    ("medulla", 4, 0), ("mushroomBody", 9, 0), ("centralComplex", 11, 0),
    ("legNeuromere", 16, 0), ("wingNeuropil", 17, 0),
]
RL, RR, LAM, AL, ME, MB, CX, LEG, WING = [rid for _, rid, _ in REGION_ORDER]
PER_REGION = 4
W = 2.0 * 1.0 * 100.0        # efficacy * gain * synapseCount

# The fixture's wiring groups, named so gates can reference them without
# re-deriving the sets from REGION_ORDER.
REGIONS = [rid for _, rid, _ in REGION_ORDER]
SOURCES = [RL, RR, LAM, AL]
MOTORS = [LEG, WING]


def region_params(engine, regions):
    """Mirror of NeuralEngine.init's region-based model assignment."""
    for i, r in enumerate(regions):
        if r in (15, 16, 17, 18, 19):        # VNC / leg / wing / haltere / abdominal
            engine.tauM[i] = 5.0
            engine.tauRef[i] = 1.0
            engine.b[i] = 0.2
            engine.a[i] = 0.5
        elif r in (9, 11):                   # mushroom body / central complex
            engine.tauM[i] = 15.0
            engine.tauAdapt[i] = 150.0
            engine.b[i] = 0.5
            engine.a[i] = 2.0


def build_fixture(append_edges: bool):
    """Exact mirror of TestSupport.closedLoopConnectome.

    append_edges=True  reproduces what the fixture INTENDED (each neuron keeps
                       all of its outgoing edges).
    append_edges=False reproduces what the fixture actually BUILDS in Swift,
                       where `wire()` assigns the range, so a neuron wired twice
                       keeps only its last edge.
    """
    regions, index_of, side_of = [], {}, {}
    for _, rid, side in REGION_ORDER:
        index_of[rid] = []
        for _ in range(PER_REGION):
            index_of[rid].append(len(regions))
            regions.append(rid)
            side_of[len(regions) - 1] = side

    out = [[] for _ in regions]
    assigned = {}          # Swift's `outgoing[from] = OutEdgeRange(...)` semantics

    def wire(a, b):
        if append_edges:
            out[a].append((b, W))
        else:
            assigned[a] = (b, W)

    for _, rid, _ in REGION_ORDER:
        ids = index_of[rid]
        for i in range(len(ids) - 1):
            wire(ids[i], ids[i + 1])
    for s in (RL, RR, LAM, AL):
        wire(index_of[s][-1], index_of[ME][0])
        wire(index_of[ME][-1], index_of[CX][0])
    for b in (ME, CX):
        for m in (LEG, WING):
            wire(index_of[b][-1], index_of[m][0])
    for m in (LEG, WING):
        wire(index_of[m][0], index_of[m][1])
        wire(index_of[m][1], index_of[m][0])

    if not append_edges:
        for a, e in assigned.items():
            out[a] = [e]
    # The FIXED Swift groups the edge set by presynaptic neuron so every CSR run
    # is contiguous. The edge SET is what `wire()` collected; grouping does not
    # change reachability, but it is what the reader will actually walk, so the
    # mirror sorts too rather than relying on that equivalence.
    return regions, index_of, side_of, [sorted(v) for v in out]


def motor_drive(engine, regions, side_of):
    """Mirror of SimulationCore.readMotorDrive, including its slot mapping:
    `slot = (side == 1 ? 0 : 1) * 3 + (type % 3)`."""
    drive = [0.0] * 6
    wing = sez = 0.0
    for i in engine.rateRing:
        rate = engine.rate[i]
        if rate <= 0:
            continue
        r = regions[i]
        if r == LEG:
            slot = (0 if side_of[i] == 1 else 1) * 3 + (i % 3)
            drive[slot] += rate
        elif r == WING:
            wing += rate
        elif r == 13:
            sez += rate
    return ([min(x / DRIVE_REFERENCE_HZ, 1.0) for x in drive],
            min(wing / DRIVE_REFERENCE_HZ, 1.0), min(sez / DRIVE_REFERENCE_HZ, 1.0))


def run(steps, emission, append_edges, use_region_params):
    regions, index_of, side_of, out = build_fixture(append_edges=append_edges)
    engine = Engine(len(regions), syn_out=out)
    engine.motorRateTauMs = 20.0
    if use_region_params:
        region_params(engine, regions)
    motor = MS.MotorSystem()
    body = BD.Body()

    antenna = index_of[AL][0]
    d = [0.0] * 6
    wing_drive = 0.0
    for s in range(steps):
        t = s * DT_MS
        if emission > 0:
            conc = min(emission * math.exp(-0.1 ** 2 / (2 * 100.0)), 1.0)
            current = conc * 40.0 - 5.0
            engine.inject(antenna, current, t)
            engine.inject(antenna, current, t)
        engine.step()
        d, wing_drive, _ = motor_drive(engine, regions, side_of)
        motor.wing_muscle_drive = wing_drive
        o = motor.update(d, DT_MS)
        body.step(fwd_target=o.forward_speed_target,
                  contact=min(1.0, max(0.0, o.leg_contact_fraction)), dt=DT_SEC)

    spiking = sorted({regions[i] for i in range(len(regions)) if engine.cum[i] > 0})
    return {
        "spikes": engine.cumTotal,
        "drive": d,
        "wing": wing_drive,
        "walk": o.walk_drive,
        "fwd": o.forward_speed_target,
        "moved": math.hypot(body.pos[0], body.pos[2]),
        "regions_spiking": spiking,
    }


def main() -> int:
    print("Fixture edge semantics vs. region model parameters")
    print("  regression regions: leg=16 wing=17 CX=11 medulla=4 antennalLobe=8\n")
    hdr = ("edges", "regions?", "spikes", "wing", "walkDrive", "fwd mm/s", "moved mm", "spiking regions")
    print("{:<7} {:<9} {:>7} {:>6} {:>10} {:>9} {:>9}  {}".format(*hdr))
    rows = {}
    for append_edges in (True, False):
        for use_region_params in (False, True):
            r = run(4000, 4.0, append_edges, use_region_params)
            rows[(append_edges, use_region_params)] = r
            print("{:<7} {:<9} {:>7} {:>6.3f} {:>10.3f} {:>9.3f} {:>9.4f}  {}".format(
                "append" if append_edges else "assign",
                "yes" if use_region_params else "no",
                r["spikes"], r["wing"], r["walk"], r["fwd"], r["moved"],
                r["regions_spiking"]))

    ok = rows[(True, True)]
    broken = rows[(False, True)]
    print()
    print("The leg pool is reachable only when the fixture keeps BOTH motor edges:")
    print(f"  intended  : leg drive {[round(x, 3) for x in ok['drive']]} "
          f"walkDrive {ok['walk']:.3f} fwd {ok['fwd']:.2f} mm/s")
    print(f"  as built  : leg drive {[round(x, 3) for x in broken['drive']]} "
          f"walkDrive {broken['walk']:.3f} fwd {broken['fwd']:.2f} mm/s")
    if sum(broken["drive"]) <= 0 and sum(ok["drive"]) > 0:
        print("\n=> the zero motor command is a FIXTURE bug (a dropped edge), not a")
        print("   missing link in the production sensory->motor path.")
        return 0
    print("\n=> the fixture is not the cause; look elsewhere.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())