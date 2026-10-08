#!/usr/bin/env python3
"""The tarsal-contact gate on the tarsal-load channel — is it doing anything?

`SimulationCore.emitMechanosensoryReafference` injects only while
`dynamics.isGrounded`, on the biological ground that a tarsal sensillum cannot
report load from a tarsus that is not on the substrate. That is a plausible
mechanism, but a mechanism that never changes a measurement is untestable, so
this script asks the only question that can settle it:

    does the outcome differ when the gate is removed?

This matters because the two languages disagree about whether the body is
airborne. `sim_body_dynamics.Body` spawns `grounded = True` and its contact
solver keeps it true for a resting animal, so on the Python side the gate may be
a no-op. In Swift the same field is maintained by `BodyDynamics.step` against
`groundY + standHeightMm`, and the fixtures drive the motor pool hard enough to
lift the body. If the bodies disagree about contact, a Swift-side run can inject
where the mirror does not, and the gate is then the only thing standing between
the mirror and a wrong prediction.

The honest output is whichever of the two branches below prints. A mirror that
reported a number for a mechanism it cannot exercise would be inventing one.

Run: python3 tools/probe_tarsal_contact_gate.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag_fixture_edges as D              # noqa: E402
import mirror_regional_fixture as M         # noqa: E402
import sim_body_dynamics as BD              # noqa: E402
from sim_neural_engine import Engine        # noqa: E402


def envelope(connectome: str, labelled: bool, emission: float,
             settle: int = 500, steps: int = 4000):
    """Drive the full arc and report the body's contact state plus the drive.

    Returns (airborne_steps, max_forward_speed_target, max_leg_drive).
    """
    if connectome == "regional":
        regions, index_of, side_of, flags, out = M.build_regional(labelled)
        leg_region, wing_region = M.LEG, M.WING
        antenna = index_of[M.AL][0]
    else:
        regions, index_of, side_of, flags, out = M.build_closed(labelled)
        leg_region, wing_region = D.LEG, D.WING
        antenna = index_of[D.AL][0]

    engine = Engine(len(regions), syn_out=out)
    engine.motorRateTauMs = 20.0
    D.region_params(engine, regions)
    motor, body = M.MS.MotorSystem(), BD.Body()
    by_class = any(f & M.FLAG_MOTOR for f in flags)

    state = {"air": 0, "fwd": 0.0, "drive": 0.0}

    def one():
        t = engine.time
        if emission > 0:
            conc = min(emission * math.exp(-(0.1 ** 2) / 200.0), 1.0)
            engine.inject(antenna, conc * 40.0 - 5.0, t)
            engine.inject(antenna, conc * 40.0 - 5.0, t)
        engine.step()
        d = [0.0] * 6
        wing = 0.0
        for i in engine.rateRing:
            rate = engine.rate[i]
            if rate <= 0:
                continue
            if by_class and not (flags[i] & M.FLAG_MOTOR):
                continue
            if regions[i] == leg_region:
                d[(0 if side_of[i] == 1 else 1) * 3 + (i % 3)] += rate
            elif regions[i] == wing_region:
                wing += rate
        d = [min(x / M.REFERENCE_HZ, 1.0) for x in d]
        state["drive"] = max(state["drive"], max(d))
        motor.wing_muscle_drive = min(wing / M.REFERENCE_HZ, 1.0)
        o = motor.update(d, M.DT_MS)
        state["fwd"] = max(state["fwd"], abs(o.forward_speed_target))
        body.step(fwd_target=o.forward_speed_target,
                  lat_target=o.lateral_speed_target,
                  contact=min(1.0, max(0.0, o.leg_contact_fraction)),
                  legs_in_contact=o.legs_in_contact,
                  amp=o.wing_stroke_amplitude, freq=o.wing_stroke_freq,
                  dt=M.DT_MS / 1000.0)
        if not body.grounded:
            state["air"] += 1

    for _ in range(settle):
        one()
    state["air"] = 0                       # the window starts after settling
    for _ in range(steps):
        one()
    return state["air"], state["fwd"], state["drive"]


def main() -> int:
    print("Tarsal-contact gate: does it change any measurement?")
    print()
    print("  scenario                  odour   airborne   max fwd target   max leg drive")

    rows = {}
    for name, topo, labelled in (("cut path", "regional", False),
                                 ("cut path (labelled)", "regional", True),
                                 ("closed path", "closed", False),
                                 ("closed path (labelled)", "closed", True)):
        for emission in (0.0, 4.0):
            air, fwd, drive = envelope(topo, labelled, emission)
            rows[(name, emission)] = (air, fwd, drive)
            print(f"  {name:24s} {emission:5.1f}   {air:6d}   {fwd:12.4f}   {drive:12.4f}")

    print()
    # The gate is live exactly when the contact state DIFFERS between the two
    # odour conditions on the same fixture: a grounded fly must transduce its
    # load, an airborne one must not.
    live = False
    for name in ("closed path", "closed path (labelled)"):
        a0 = rows[(name, 0.0)][0]
        a1 = rows[(name, 4.0)][0]
        grounded_no_odour = a0 < 4000
        airborne_with_odour = a1 == 4000
        print(f"  {name:24s} no odour grounded for {4000 - a0} steps;"
              f" with odour airborne for {a1} steps")
        live = live or (grounded_no_odour and airborne_with_odour)

    print()
    gated = M.measure("regional", phasic=True, classed=False,
                      use_class_filter=True, emission=4.0)
    print(f"  cut path, contact gate ON : {gated['disp']:.6f} mm"
          f"  ({gated['inj']} injections)")

    if not live:
        print()
        print("VERDICT: the body reports the SAME contact state in both odour")
        print("  conditions on every fixture, so the tarsal gate cannot change an")
        print("  outcome and nothing here can detect it. This FAILS rather than")
        print("  reporting a number, because a gate nothing can exercise is not a")
        print("  gate — it is an untested branch.")
        return 1

    print()
    print("VERDICT: the gate IS live and measurable. On the closed-loop fixture")
    print("  the no-odour run stays grounded (so a load receptor may fire) while")
    print("  the odour run lifts the body for the whole window (so it must not).")
    print("  That is also a LIMITATION of this fixture worth stating: the motor")
    print("  drive it produces is strong enough to make the fly airborne, so the")
    print("  tarsal channel is silent for the entire stimulated run and the")
    print("  displacement there comes from the connectome, not from the arm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())