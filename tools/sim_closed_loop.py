#!/usr/bin/env python3
"""Closed-loop mirror: world -> sensory -> connectome -> motor -> body -> world.

The individual stages already have mirrors (sim_neural_engine.py,
sim_motor_system.py, sim_body_dynamics.py), and the Swift XCTest suite covers
each of them. Nothing checked the ARC: that excitation entering the connectome
actually comes out the other end as joint torque and then as displacement of a
rigid body.

That gap hid a real problem. `WorldTests.testSceneIntegratesIntoClosedLoop`
asserts `core.position.x >= 0` after 300 steps — but the fly spawns at x = 0 and
moves forward, so the assertion is true whether the loop works or the fly never
moves at all. A vacuous assertion is worse than no assertion, because it looks
like coverage.

This tool runs the real arc at the real clock (0.1 ms neural steps) with a
synthetic connectome and reports:

  1. whether spikes reach the motor readout at all,
  2. whether the readout produces a non-zero leg drive and joint torque,
  3. whether the body actually DISPLACES, and by how much,
  4. how the distance scales with drive, so the claim is checked for monotonic
     dependence rather than assumed.

It also states the honest number: how far the animal travels in real time given
the ~42x slow-motion factor.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_neural_engine import Engine, driveBurst
from sim_motor_system import MotorSystem
from sim_body_dynamics import Body

# ---- Region ids (must match FlyBrainCore/Types.swift RegionID) -------------
REGION_RETINA_L = 1
REGION_RETINA_R = 2
REGION_LAMINA = 3
REGION_MEDULLA = 4
REGION_LOBULA_PLATE = 6
REGION_ANTENNAL_LOBE = 8
REGION_MUSHROOM_BODY = 9
REGION_CENTRAL_COMPLEX = 11
REGION_SEZ = 13
REGION_VNC = 15
REGION_LEG = 16
REGION_WING = 17
REGION_HALTERE = 18

DT_MS = 0.1                 # neural step, milliseconds (SimulationParameters.dt)
STEPS_PER_FRAME = 4         # AppModel.tick()
FRAME_HZ = 60.0

# The motor readout saturates at a group rate of 100 Hz (motorDriveReferenceHz).
DRIVE_REFERENCE_HZ = 100.0
# Leaky rate time constant, ms of neural time (NeuralEngine.motorRateTauMs).
MOTOR_TAU_MS = 20.0


def build_connectome():
    """Synthetic region-laid connectome with a brain->VNC motor pathway.

    Layout: retinotopic input neurons (retina/lamina) feed a small brain
    layer (medulla/mushroom body/central complex), which drives the leg and
    wing motor pools in the VNC/SEZ. Anatomically simplified, but the PATHWAY
    is real in its structure: input region -> interneuron -> motor pool, all
    excitatory here so the arc can be measured without sign ambiguity.
    """
    regions = []
    # input
    for _ in range(4):
        regions.append(REGION_RETINA_L)
        regions.append(REGION_RETINA_R)
    for _ in range(6):
        regions.append(REGION_LAMINA)
    for _ in range(4):
        regions.append(REGION_ANTENNAL_LOBE)
    # brain
    for _ in range(8):
        regions.append(REGION_MEDULLA)
    for _ in range(6):
        regions.append(REGION_MUSHROOM_BODY)
    for _ in range(6):
        regions.append(REGION_CENTRAL_COMPLEX)
    for _ in range(4):
        regions.append(REGION_SEZ)
    # VNC + motor pools
    for _ in range(6):
        regions.append(REGION_VNC)
    for _ in range(6):
        regions.append(REGION_LEG)          # one per leg slot
    for _ in range(4):
        regions.append(REGION_WING)
    for _ in range(2):
        regions.append(REGION_HALTERE)

    n = len(regions)
    sides = [0] * n
    for i, r in enumerate(regions):
        if r == REGION_RETINA_L:
            sides[i] = 1
        elif r == REGION_RETINA_R:
            sides[i] = 2

    def first(region, side=None):
        for i, r in enumerate(regions):
            if r == region and (side is None or sides[i] == side):
                return i
        return None

    brain = [i for i, r in enumerate(regions)
             if r in (REGION_MEDULLA, REGION_MUSHROOM_BODY, REGION_CENTRAL_COMPLEX)]
    legs = [i for i, r in enumerate(regions) if r == REGION_LEG]
    wings = [i for i, r in enumerate(regions) if r == REGION_WING]
    inputs = [i for i, r in enumerate(regions)
              if r in (REGION_RETINA_L, REGION_RETINA_R, REGION_LAMINA, REGION_ANTENNAL_LOBE)]

    out = [[] for _ in range(n)]
    C = 100.0     # synapses per connection (mirrors the test helper's convention)
    EFF = 0.5
    for i in inputs:                      # input -> brain
        for b in brain[:4]:
            out[i].append((b, EFF * C))
    for b in brain:                       # brain -> motor pools
        for m in legs + wings:
            out[b].append((m, EFF * C * 0.6))
    # Motor pools are self-sustaining enough to hold a rate once driven, so the
    # drive does not collapse between sensory events.
    for m in legs + wings:
        out[m].append((m, EFF * C * 0.5))
    return regions, sides, out


def motor_drive_from_engine(engine, regions):
    """Mirror of SimulationCore.readMotorDrive over the firing active set."""
    drive = [0.0] * 6
    wing = 0.0
    sez = 0.0
    for r_i in engine.rateRing:
        rate = engine.rate[r_i]
        if rate <= 0:
            continue
        r = regions[r_i]
        if r == REGION_LEG:
            slot = r_i % 6
            drive[slot] += rate
        elif r == REGION_WING:
            wing += rate
        elif r == REGION_SEZ:
            sez += rate
    d = [min(x / DRIVE_REFERENCE_HZ, 1.0) for x in drive]
    return d, min(wing / DRIVE_REFERENCE_HZ, 1.0), min(sez / DRIVE_REFERENCE_HZ, 1.0)


def run_closed_loop(steps, sensory_current=300.0, sensory_interval_ms=5.0,
                    record=False, target_motor=False):
    """The real arc at the real clock. Returns a report dict."""
    regions, sides, out = build_connectome()
    engine = Engine(len(regions), syn_out=out)
    engine.motorRateTauMs = MOTOR_TAU_MS
    motor = MotorSystem()
    body = Body()

    # Which neuron each sensory channel targets (mirror of inputNeuron()).
    def first_region(region):
        for i, r in enumerate(regions):
            if r == region:
                return i
        return None
    retina_l = first_region(REGION_RETINA_L)
    retina_r = first_region(REGION_RETINA_R)
    antenna_l = first_region(REGION_ANTENNAL_LOBE)

    # CONTROL: inject the SAME stimulus straight into the motor pools. If the
    # fly walks here but not through the sensory neurons, then the "loop" is
    # really a direct wire and the connectome path is idle — the difference
    # between the two runs is the only evidence that the arc is real.
    if target_motor:
        motor_pool = [i for i, r in enumerate(regions)
                      if r in (REGION_LEG, REGION_WING)]
        retina_l = motor_pool[0]
        retina_r = motor_pool[1] if len(motor_pool) > 1 else motor_pool[0]
        antenna_l = motor_pool[2] if len(motor_pool) > 2 else None

    sensory_events = 0
    for s in range(steps):
        t_ms = s * DT_MS
        # 1) sensory sampling -> injection (a repeating stimulus, because the
        #    synthetic connectome has no photoreceptors of its own)
        if s % max(int(sensory_interval_ms / DT_MS), 1) == 0:
            engine.inject(retina_l, sensory_current, t_ms)
            engine.inject(retina_r, sensory_current, t_ms)
            if antenna_l is not None:
                engine.inject(antenna_l, sensory_current * 0.7, t_ms)
            sensory_events += 1

        # 2) neural step
        engine.step()

        # 3) motor readout -> CPG
        neural_drive, wing_drive, sez_drive = motor_drive_from_engine(engine, regions)
        motor.wing_muscle_drive = wing_drive
        motor.proboscis_drive = sez_drive
        o = motor.update(neural_drive, DT_MS)

        # 4) body integration with the command the CPG produced
        body.step(fwd_target=o.forward_speed_target,
                  lat_target=o.lateral_speed_target,
                  contact=o.leg_contact_fraction,
                  legs_in_contact=o.legs_in_contact,
                  amp=o.wing_stroke_amplitude, freq=o.wing_stroke_freq,
                  asym=o.left_right_asymmetry, pitch_bias=o.pitch_bias,
                  haltere=o.haltere_drive, dt=DT_MS / 1000.0)

    torque = sum(o.leg_torques)
    return {
        "steps": steps,
        "neural_ms": steps * DT_MS,
        "spikes": engine.cumTotal,
        "sensory_events": sensory_events,
        "neural_drive": neural_drive,
        "leg_torque": torque,
        "fwd_target": o.forward_speed_target,
        "distance_mm": math.hypot(body.pos[0], body.pos[2]),
        "x": body.pos[0], "z": body.pos[2],
        "speed_mm_s": body.ground_speed(),
        "planted": sum(1 for l in motor.legs if not l.is_swing),
    }


def main() -> int:
    fails = []
    print("Closed-loop mirror — world -> sensory -> connectome -> motor -> body")
    print(f"  clock: {DT_MS} ms/step, {STEPS_PER_FRAME} steps per {FRAME_HZ:g} Hz frame\n")

    MAIN_STEPS = 8_000               # 0.8 s of neural time
    r = run_closed_loop(MAIN_STEPS)
    print(f"After {r['neural_ms']/1000:.0f} s of NEURAL time ({MAIN_STEPS:,} steps):")
    print(f"  sensory injections : {r['sensory_events']}")
    print(f"  spikes             : {r['spikes']}")
    print(f"  leg drive (6 slots): {[round(d, 3) for d in r['neural_drive']]}")
    print(f"  sum leg torque     : {r['leg_torque']:.3f}")
    print(f"  fwd setpoint       : {r['fwd_target']:.2f} mm/s")
    print(f"  displacement       : x={r['x']:.3f} mm  z={r['z']:.3f} mm  "
          f"(|d|={r['distance_mm']:.3f} mm)")
    print(f"  planted legs       : {r['planted']}/6")

    if r["spikes"] == 0:
        fails.append("no spikes reached the connectome at all")
    if r["sensory_events"] == 0:
        fails.append("no sensory events were injected")
    if sum(r["neural_drive"]) <= 0:
        fails.append("sensory input produced NO motor drive "
                     "(the arc is broken between connectome and readout)")
    if r["leg_torque"] <= 0:
        fails.append("motor drive produced no joint torque")
    if r["distance_mm"] <= 0.001:
        fails.append("the body did not displace despite a non-zero command "
                     "(the arc is broken between motor and body)")

    # Monotonicity: more drive must mean more distance, not a threshold switch.
    print("\nDrive -> distance (the claim is checked, not assumed):")
    dists = []
    for drive in (0.1, 0.4, 0.8):
        regions, sides, out = build_connectome()
        engine = Engine(len(regions), syn_out=out)
        engine.motorRateTauMs = MOTOR_TAU_MS
        motor = MotorSystem()
        body = Body()
        motor.wing_muscle_drive = 0.0
        for s in range(8_000):       # 0.8 s of neural time
            o = motor.update([drive] * 6, DT_MS)
            body.step(fwd_target=o.forward_speed_target,
                      lat_target=o.lateral_speed_target,
                      contact=o.leg_contact_fraction,
                      legs_in_contact=o.legs_in_contact, dt=DT_MS / 1000.0)
        d = math.hypot(body.pos[0], body.pos[2])
        dists.append(d)
        print(f"  drive {drive:>4} -> {d:7.3f} mm "
              f"({o.forward_speed_target:6.2f} mm/s setpoint)")
    if not (dists[0] < dists[1] < dists[2]):
        fails.append(f"distance is not monotone in drive: {dists}")

    # (A) The arc must be REAL: cutting the sensory input must stop the animal.
    #     A loop whose output does not depend on its input is not a loop.
    silent = run_closed_loop(MAIN_STEPS, sensory_current=0.0)
    if silent["distance_mm"] > 0.001 or sum(silent["neural_drive"]) > 0:
        fails.append(f"the fly still moved ({silent['distance_mm']:.3f} mm) with NO "
                     "sensory input — the motor output is not driven by the loop")

    # (B) The path must run THROUGH the connectome, not around it. Injecting the
    #     same current directly into the motor pools must produce a DIFFERENT
    #     result; if it does not, the sensory neurons are decorative.
    direct = run_closed_loop(MAIN_STEPS, target_motor=True)
    through = r["distance_mm"]
    bypass = direct["distance_mm"]
    if abs(through - bypass) < 1e-6:
        fails.append("driving the motor pools directly gives exactly the same motion "
                     "as driving the sensory neurons — the connectome path does "
                     "nothing and the loop is a direct wire")
    print("\nLoop integrity (the arc is checked, not asserted):")
    print(f"  through sensory neurons : {through:8.3f} mm")
    print(f"  no sensory input        : {silent['distance_mm']:8.3f} mm")
    print(f"  straight into motor pool: {bypass:8.3f} mm")

    # Real-time honesty: how far does it actually travel per real second?
    slowmo = (1000.0 / (DT_MS * STEPS_PER_FRAME * FRAME_HZ))
    neural_s = r["neural_ms"] / 1000.0
    print(f"\nHonest real-time numbers at the current clock ({slowmo:.1f}x slow motion):")
    print(f"  {neural_s:.0f} s of neural time takes {neural_s * slowmo:.0f} s of WALL time")
    print(f"  => {r['distance_mm']:.3f} mm of travel in {neural_s * slowmo:.0f} s "
          f"≈ {r['distance_mm'] / (neural_s * slowmo) * 1000:.3f} um/s")
    print("  (a real fly walks ~20-30 mm/s, so the simulated animal is ~3 orders")
    print("   of magnitude slower in wall time — a property of the clock, not the CNG)")

    print()
    for f in fails:
        print(f"[FAIL] {f}")
    print("FAILED:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())