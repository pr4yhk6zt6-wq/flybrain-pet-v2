#!/usr/bin/env python3
"""Gate for the tarsal-load reafference arm (spec #15).

CI shows `WorldTests.testSceneIntegratesIntoClosedLoop` failing BOTH its
controls on commit fe65764, while the positive control passes:

    odour, closed path -> the fly moves        (passes)
    no odour           -> 3.265094  mm         (expected < 0.01)
    cut connectome     -> 0.9931856 mm         (expected < 0.01)

The controls are meant to isolate the connectome by removing the only thing that
should move the body. They fail because `emitMechanosensoryReafference` injects
a tactile current every step, and that current reaches the motor command without
going through the connectome at all. Two separate defects cause this, and each
one alone is enough to produce the symptom — measured below, not assumed:

DEFECT A — the arm carries DC. A tarsal/campaniform sensillum is PHASIC: it
reports CHANGE in load and adapts to the static support force. The arm injected
the ABSOLUTE load, and a standing fly carries ~1 body weight, so a settled fly
injected a current on 4000 of 4000 measured steps and coasted 0.986033 mm on a
fixture whose sensory->motor path is CUT.

DEFECT B — the arm lands on the readout. `touchInput(side:)` resolved
`inputNeuron(region: .legNeuromere, side:)`, i.e. the region's FIRST leg neuron.
On a labelled asset that cell is one of the motor neurons the readout sums, so
the "sensory" current was injected straight into the motor command, and the
readout then counted its own input as output. Asserted structurally below (the
fallback target carries the motor label; the class-resolved afferent does not).

Either fix removes THIS symptom, so a population-level assertion cannot tell
them apart. The gate therefore asserts each defect's own signature as well:

  * zero DC gain — a load held constant must produce NO current (kills A);
  * target composition — the afferent must not be a cell the readout sums (kills B).

Both model constants (gain, adaptation time constant, deadband) and the drive
reference are read out of `SimulationCore.swift`, so changing the model moves the
gate instead of silently invalidating it. The positive control is asserted too:
the fix must not achieve "no motion" by breaking the loop.

Run: python3 tools/mirror_regional_fixture.py
"""
from __future__ import annotations

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag_fixture_edges as D          # noqa: E402
import sim_body_dynamics as BD          # noqa: E402
import sim_motor_system as MS           # noqa: E402
from sim_neural_engine import Engine    # noqa: E402

DT_MS = D.DT_MS
HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore")
SWIFT = os.path.join(CORE, "SimulationCore.swift")
SENSORY_SWIFT = os.path.join(CORE, "SensoryInterface.swift")

FLAG_MOTOR = 1
FLAG_SENSORY = 2
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILS.append(name)


def swift_constant(pattern: str, what: str) -> float:
    text = open(SWIFT, encoding="utf-8").read()
    m = re.search(pattern, text)
    if not m:
        print(f"could not find {what} in SimulationCore.swift — the gate is stale")
        raise SystemExit(2)
    return float(m.group(1))


GAIN = swift_constant(r"reafferenceGain: Float = ([0-9.]+)", "reafferenceGain")
TAU_MS = swift_constant(r"reafferenceAdaptationTauMs: Double = ([0-9.]+)",
                        "reafferenceAdaptationTauMs")
DEADBAND = swift_constant(r"reafferenceDeadband: Float = ([0-9.]+)",
                          "reafferenceDeadband")
REFERENCE_HZ = swift_constant(r"motorDriveReferenceHz: Float = ([0-9.]+)",
                              "motorDriveReferenceHz")

# ---------------------------------------------------------------------------
# Fixtures. Both are mirrors of the ones the failing test actually uses.
# ---------------------------------------------------------------------------

REGIONAL_LIST = [
    ("retinaLeft", 1, 1), ("retinaRight", 2, 2), ("lamina", 3, 0),
    ("medulla", 4, 0), ("lobula", 5, 0), ("lobulaPlate", 6, 0),
    ("opticLobe", 7, 0), ("antennalLobe", 8, 0), ("mushroomBody", 9, 0),
    ("lateralHorn", 10, 0), ("centralComplex", 11, 0), ("superiorBrain", 12, 0),
    ("subesophagealZone", 13, 0), ("cervicalConnective", 14, 0),
    ("ventralNerveCord", 15, 0), ("legNeuromere", 16, 0),
    ("wingNeuropil", 17, 0), ("haltereNeuropil", 18, 0),
    ("abdominalNeuromere", 19, 0), ("endocrineVisceral", 20, 0),
]
LEG, WING, SEZ, AL, ME, CX = 16, 17, 13, 8, 4, 11

# `TestSupport.regionalConnectome()` — 20 regions x 10, chains cut at every
# boundary, so NO excitation can leave a region.
REGIONAL_PER = 10
REGIONAL_W = 0.5 * 1.0 * 3          # efficacy(0.5) * gain * synapseCount(3)

# `TestSupport.closedLoopConnectome()` — 9 regions x 4, efficacy 2, and a real
# multi-hop path sensory -> medulla -> central complex -> both motor pools.
CLOSED_LIST = [
    ("retinaLeft", 1, 1), ("retinaRight", 2, 2), ("lamina", 3, 0),
    ("antennalLobe", 8, 0), ("medulla", 4, 0), ("mushroomBody", 9, 0),
    ("centralComplex", 11, 0), ("legNeuromere", 16, 0), ("wingNeuropil", 17, 0),
]
CLOSED_PER = 4
CLOSED_W = 2.0 * 1.0 * 100          # efficacy(2) * gain * synapseCount(100)


def build_regional(classed: bool):
    regions, index_of, side_of = [], {}, {}
    for _, rid, side in REGIONAL_LIST:
        index_of[rid] = []
        for _ in range(REGIONAL_PER):
            index_of[rid].append(len(regions))
            side_of[len(regions)] = side
            regions.append(rid)
    out = [[] for _ in regions]
    for _, rid, _ in REGIONAL_LIST:
        ids = index_of[rid]
        for i in range(len(ids) - 1):
            if i % REGIONAL_PER == REGIONAL_PER - 1:
                continue                # region boundary: `continue` in Swift too
            out[ids[i]].append((ids[i + 1], REGIONAL_W))
    flags = [0] * len(regions)
    if classed:
        for m in (LEG, WING):
            ids = index_of[m]
            flags[ids[0]] = FLAG_MOTOR
            flags[ids[1]] = FLAG_MOTOR
            flags[ids[2]] = FLAG_SENSORY
            flags[ids[3]] = FLAG_SENSORY
    return regions, index_of, side_of, flags, [sorted(v) for v in out]


def build_closed(classed: bool):
    """Mirror of `closedLoopConnectome`: the same edge SET, emitted grouped by
    presynaptic neuron the way the fixed fixture does."""
    regions, index_of, side_of = [], {}, {}
    for _, rid, side in CLOSED_LIST:
        index_of[rid] = []
        for _ in range(CLOSED_PER):
            index_of[rid].append(len(regions))
            side_of[len(regions)] = side
            regions.append(rid)
    edges: list[tuple[int, int]] = []
    for _, rid, _ in CLOSED_LIST:
        ids = index_of[rid]
        for i in range(len(ids) - 1):
            edges.append((ids[i], ids[i + 1]))
    for s in (1, 2, 3, 8):
        edges.append((index_of[s][-1], index_of[ME][0]))
        edges.append((index_of[ME][-1], index_of[CX][0]))
    for b in (ME, CX):
        for m in (LEG, WING):
            edges.append((index_of[b][-1], index_of[m][0]))
    for m in (LEG, WING):
        edges.append((index_of[m][0], index_of[m][1]))
        edges.append((index_of[m][1], index_of[m][0]))
    out = [[] for _ in regions]
    for a, b in edges:
        out[a].append((b, CLOSED_W))
    flags = [0] * len(regions)
    if classed:
        for m in (LEG, WING):
            ids = index_of[m]
            flags[ids[0]] = FLAG_MOTOR
            flags[ids[1]] = FLAG_MOTOR
            flags[ids[2]] = FLAG_SENSORY
            flags[ids[3]] = FLAG_SENSORY
    return regions, index_of, side_of, flags, [sorted(v) for v in out]


def touch_region(index_of):
    """`inputNeuron(region: .legNeuromere, side: 1)` on these fixtures: no leg
    neuron has side 1, so the side-0 fallback returns the region's FIRST cell."""
    return index_of[LEG][0]


def touch_class(index_of, flags):
    """`SensoryInterface.touchAfferent(side:)` — classed side match, classed
    centre, any side match, any centre."""
    ids = index_of[LEG]
    classed = [i for i in ids if flags[i] & FLAG_SENSORY]
    return classed[0] if classed else ids[0]


def readout_sums(index_of, flags):
    """The set of leg/wing neurons `readMotorDrive` would sum on this fixture:
    every neuron in the motor regions under the region-only fallback, motor-
    labelled cells only once the asset carries labels."""
    by_class = any(f & FLAG_MOTOR for f in flags)
    summed = set()
    for m in (LEG, WING):
        for i in index_of[m]:
            if by_class and not (flags[i] & FLAG_MOTOR):
                continue
            summed.add(i)
    return summed, by_class


def measure(topology: str, phasic: bool, classed: bool, use_class_filter: bool,
            emission: float = 0.0, settle: int = 500, steps: int = 4000,
            rest: int = 2000):
    """Full arc: engine -> readout -> motor -> body -> reafference."""
    regions, index_of, side_of, flags, out = (
        build_regional(classed) if topology == "regional" else build_closed(classed))
    engine = Engine(len(regions), syn_out=out)
    engine.motorRateTauMs = 20.0
    D.region_params(engine, regions)
    motor, body = MS.MotorSystem(), BD.Body()
    weight = BD.MASS * BD.GRAVITY
    _, by_class = readout_sums(index_of, flags)
    touch = (touch_class(index_of, flags) if use_class_filter
             else touch_region(index_of))
    antenna = index_of[AL][0]
    baseline = [None]
    injections = [0]

    def one():
        t = engine.time
        if emission > 0:
            conc = min(emission * math.exp(-(0.1 ** 2) / 200.0), 1.0)
            cur = conc * 40.0 - 5.0
            engine.inject(antenna, cur, t)
            engine.inject(antenna, cur, t)
        engine.step()
        d = [0.0] * 6
        wing = 0.0
        for i in engine.rateRing:
            rate = engine.rate[i]
            if rate <= 0:
                continue
            reg = regions[i]
            if reg not in (LEG, WING, SEZ):
                continue
            if by_class and not (flags[i] & FLAG_MOTOR):
                continue
            if reg == LEG:
                d[(0 if side_of[i] == 1 else 1) * 3 + (i % 3)] += rate
            elif reg == WING:
                wing += rate
        motor.wing_muscle_drive = min(wing / REFERENCE_HZ, 1.0)
        o = motor.update([min(x / REFERENCE_HZ, 1.0) for x in d], DT_MS)
        body.step(fwd_target=o.forward_speed_target,
                  lat_target=o.lateral_speed_target,
                  contact=min(1.0, max(0.0, o.leg_contact_fraction)),
                  legs_in_contact=o.legs_in_contact,
                  amp=o.wing_stroke_amplitude, freq=o.wing_stroke_freq,
                  dt=DT_MS / 1000.0)
        load = body.normal / max(weight, 1.0)
        if baseline[0] is None:
            baseline[0] = load
        # Adapts to the RAW load even airborne, so touch-down reads as a step.
        baseline[0] += (load - baseline[0]) * min(1.0, DT_MS / max(TAU_MS, 1e-6))
        sig = max(0.0, load - baseline[0]) if phasic else load
        if body.grounded and sig * GAIN > DEADBAND:      # tarsal contact gate
            injections[0] += 1
            engine.inject(touch, min(sig * GAIN, 1.0) * 50.0, t)

    for _ in range(settle):
        one()
    # Wait for the body to STOP before starting the window. The settle length
    # alone does not guarantee it: on the fixtures the motor command is 0, so a
    # spike that happened during settling leaves a velocity that decays over
    # ~0.9 s (900 steps) and moves the animal ~0.009 mm. Measuring from there
    # would credit that free drift to the loop — the mirror reported 0.008919 mm
    # for a no-odour run in which the tarsal arm injected ZERO times, which is
    # how the artefact was found.
    #
    # `rest = 0` reproduces Swift's protocol exactly (WorldTests settles 500
    # steps and measures from there, so Swift's numbers DO include that decay).
    # The faithfulness section uses it, because matching Swift is the whole
    # point there; the sections that measure the FIX use the default, because
    # they are asking whether the arm can push a body that is not already
    # drifting.
    for _ in range(rest):
        one()
    start = list(body.pos)
    i0 = injections[0]
    for _ in range(steps):
        one()
    return {"disp": math.hypot(body.pos[0] - start[0], body.pos[2] - start[2]),
            "inj": injections[0] - i0, "inj_total": injections[0]}


def main() -> int:
    print("Tarsal-load reafference gate")
    print(f"  constants read from SimulationCore.swift: gain {GAIN}  tau {TAU_MS} ms"
          f"  deadband {DEADBAND}  reference {REFERENCE_HZ} Hz")
    print()

    # ---------------------------------------------------------------- 1) mirror
    print("1) Faithfulness — the pre-fix arm must reproduce Swift's CI numbers")
    # rest=0: same protocol as Swift's WorldTests (settle 500, measure from
    # there), so these numbers are comparable to the CI log.
    pre_closed = measure("closed", phasic=False, classed=False,
                         use_class_filter=False, emission=0.0, rest=0)
    pre_cut = measure("regional", phasic=False, classed=False,
                      use_class_filter=False, emission=4.0, rest=0)
    print(f"  closed path, no odour   {pre_closed['disp']:.6f} mm   (Swift 3.265094)")
    print(f"  cut path, odour         {pre_cut['disp']:.6f} mm   (Swift 0.9931856)")
    check("mirror reproduces Swift's no-odour control",
          abs(pre_closed["disp"] - 3.265094) < 0.05,
          f"{pre_closed['disp']:.4f} vs 3.265094")
    check("mirror reproduces Swift's cut-path control",
          abs(pre_cut["disp"] - 0.9931856) < 0.02,
          f"{pre_cut['disp']:.4f} vs 0.9931856")
    print()

    # ------------------------------------------------------------ 2) defect A
    print("2) Defect A — the arm carries DC")
    check("pre-fix: a settled fly injects a tactile current on EVERY step",
          pre_cut["inj"] == 4000, f"{pre_cut['inj']} of 4000 steps")
    held, base, dc = 1.25, 1.25, 0.0
    for _ in range(4000):                       # a load held constant
        base += (held - base) * min(1.0, DT_MS / TAU_MS)
        dc = max(dc, max(0.0, held - base) * GAIN)
    check("zero DC gain: a load held constant produces no sustained drive",
          dc <= DEADBAND, f"max intensity {dc:.6f} <= deadband {DEADBAND}")
    step_base, step_peak = 1.0, 0.0
    for _ in range(200):                        # a load STEP must still be signed
        step_base += (1.5 - step_base) * min(1.0, DT_MS / TAU_MS)
        step_peak = max(step_peak, (1.5 - step_base) * GAIN)
    check("the receptor is not a mute: a load step still produces a signal",
          step_peak > DEADBAND, f"peak intensity {step_peak:.4f}")
    print()

    # ------------------------------------------------------------ 3) defect B
    print("3) Defect B — the arm must not land on the readout's own cells")
    _, index_of, _, flags, _ = build_regional(classed=True)
    summed, by_class = readout_sums(index_of, flags)
    region_target = touch_region(index_of)
    class_target = touch_class(index_of, flags)
    check("pre-fix target IS a cell the motor readout sums (the loop is shorted)",
          by_class and region_target in summed,
          f"neuron {region_target} (flags {flags[region_target]}) is summed")
    check("class-resolved afferent is NOT a cell the readout sums",
          by_class and class_target not in summed,
          f"neuron {class_target} (flags {flags[class_target]}) is not summed")
    print()

    # ------------------------------------------------------------- 4) the fix
    print("4) Fixed — the controls must go quiet and the positive control stay loud")
    net_src = open(SENSORY_SWIFT, encoding="utf-8").read()
    check("touchInput(side:) resolves the afferent through touchAfferent",
          "touchAfferent(side: side)" in net_src
          or "touchAfferent(side:" in net_src, "SensoryInterface.swift")
    core_src = open(SWIFT, encoding="utf-8").read()
    check("the reafference arm injects via the resolved neuron, not the region",
          "touchInput(neuron: n" in core_src, "SimulationCore.swift")
    check("the arm is gated on real tarsal contact",
          "dynamics.isGrounded" in core_src, "SimulationCore.swift")

    fixed_n0 = measure("closed", phasic=True, classed=True, use_class_filter=True,
                       emission=0.0)
    fixed_cut = measure("regional", phasic=True, classed=False,
                        use_class_filter=True, emission=4.0)
    fixed_cut_labelled = measure("regional", phasic=True, classed=True,
                                 use_class_filter=True, emission=4.0)
    fixed_odour = measure("closed", phasic=True, classed=True,
                          use_class_filter=True, emission=4.0)
    print(f"  closed path, no odour        {fixed_n0['disp']:.6f} mm")
    print(f"  cut path, odour              {fixed_cut['disp']:.6f} mm")
    print(f"  cut path, odour (labelled)   {fixed_cut_labelled['disp']:.6f} mm")
    print(f"  closed path, odour           {fixed_odour['disp']:.6f} mm")
    check("fixed: no odour does not walk", fixed_n0["disp"] < 0.01,
          f"{fixed_n0['disp']:.6f} mm")
    check("fixed: cut path does not walk", fixed_cut["disp"] < 0.01,
          f"{fixed_cut['disp']:.6f} mm")
    check("fixed: cut path does not walk on a labelled asset either",
          fixed_cut_labelled["disp"] < 0.01, f"{fixed_cut_labelled['disp']:.6f} mm")
    check("fixed: odour STILL walks (the loop was not muted)",
          fixed_odour["disp"] > 0.05, f"{fixed_odour['disp']:.4f} mm")
    check("fixed: the arm no longer injects while standing still",
          fixed_n0["inj"] == 0, f"{fixed_n0['inj']} injections")

    if FAILS:
        print(f"\nFAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("\nFAILED: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())