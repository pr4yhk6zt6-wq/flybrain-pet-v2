#!/usr/bin/env python3
"""Hard gate for the body physics (CI step).

Mirrors the numeric assertions of ios/Tests/FlyBrainCoreTests/BodyDynamicsTests.swift
against tools/sim_body_dynamics.py. This exists because the authoring device has
no Swift toolchain: the XCTest suite is the real gate on the macOS runner, and
this script is the equivalent gate for the same numbers on the Python pipeline
runner, so a physics regression fails CI even before Xcode runs.

Run:  python3 tools/verify_body_physics.py     (exit 1 on any failure)
"""
import sys
import math
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim_body_dynamics import (Body, wing_tip_speed, aerodynamics, WEIGHT,
                               GRAVITY, MASS, MAX_STROKE_AMP, MAX_STROKE_FREQ,
                               STAND_HEIGHT, GROUND_Y)

SILENT = dict(legs_in_contact=False, contact=0.0)
AIR = dict(legs_in_contact=False, contact=0.0)
DRIVEN = dict(amp=MAX_STROKE_AMP, freq=MAX_STROKE_FREQ, dt=0.0001, **AIR)
DT = 0.0001

failures = []


def check(name, cond, detail):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}: {detail}")
    if not cond:
        failures.append(name)


def airborne(height_mm):
    b = Body()
    b.pos = [0.0, height_mm, 0.0]
    b.vel = [0.0, 0.0, 0.0]
    b.grounded = False
    return b


def run(b, steps, **kw):
    for _ in range(steps):
        b.step(**kw)
    return b


# 1. wing geometry / tip speed are the calibrated, published-scale numbers
tip = wing_tip_speed(MAX_STROKE_AMP, MAX_STROKE_FREQ)
check("wingTipSpeedIsMeasuredScale", 1500 < tip < 2100,
      f"{tip:.0f} mm/s (expect ~1799, published ~2000-2500)")

# 2. full-power lift clears body weight by the takeoff margin, silently zero
lift, _ = aerodynamics(MAX_STROKE_AMP, MAX_STROKE_FREQ)
ratio = lift / WEIGHT
check("flightLiftExceedsWeightButOnlyWhenDriven", 1.3 <= ratio <= 1.6 and
      aerodynamics(0.0, 0.0)[0] == 0.0,
      f"lift/weight {ratio:.2f}, silent lift {aerodynamics(0.0, 0.0)[0]:.1f} nN")

# 3. a resting fly is held up by the substrate (penalty contact, not a clamp)
b = run(Body(), 5000, dt=DT)
pen = STAND_HEIGHT - b.pos[1]
check("restingFlyIsSupported", 0 < pen < 0.05 and b.normal > 0 and b.grounded,
      f"penetration {pen*1000:.1f} um, load {b.normal/WEIGHT:.3f} weights")

# 4. a silent motor command produces no walking at all
check("silentMotorDoesNotWalk", abs(b.vel[0]) < 1e-6 and abs(b.vel[2]) < 1e-6,
      f"vx {b.vel[0]:.2e}, vz {b.vel[2]:.2e} mm/s")

# 5. walking speed tracks the neural setpoint, monotonically, under the cap
tracking = []
for target in (7.5, 15.0, 30.0):
    body = run(Body(), 20000, fwd_target=target, contact=0.5, dt=DT)
    tracking.append((target, body.vel[0]))
ok = all(abs(v - t) <= max(0.1, 0.02 * t) for t, v in tracking) and \
     all(tracking[i][1] < tracking[i + 1][1] for i in range(len(tracking) - 1))
check("walkingTracksNeuralSetpoint", ok,
      ", ".join(f"{t:.1f}->{v:.2f}" for t, v in tracking))

# 6. lateral drive moves the body toward the driving side, sign preserved
pairs = []
for t in (20.0, -20.0):
    body = run(Body(), 20000, lat_target=t, contact=0.5, dt=DT)
    pairs.append((t, body.pos[2], body.vel[2]))
ok = all(math.copysign(1, t) == math.copysign(1, z) == math.copysign(1, vz)
         for t, z, vz in pairs)
check("lateralDriveMovesTowardThatSide", ok,
      ", ".join(f"{t:+.0f}->z{z:+.1f}" for t, z, _ in pairs))

# 7. airborne with silent wings: free fall (drag-limited, hence not pure g)
b = run(airborne(1000.0), 2000, amp=0.0, freq=0.0, dt=DT, **AIR)
check("airborneSilentWingsFall", b.vel[1] < -500 and not b.grounded,
      f"vy {b.vel[1]:.1f} mm/s at y {b.pos[1]:.1f} mm")

# 8. airborne with driven wings: climbs and gains altitude
b = run(airborne(100.0), 2000, **DRIVEN)
check("drivenWingsClimb", b.vel[1] > 100 and b.pos[1] > 120,
      f"vy {b.vel[1]:.1f} mm/s, y {b.pos[1]:.1f} mm")

# 9. landing: once wing drive collapses the fly returns to the substrate
b = run(airborne(40.0), 4000, **DRIVEN)
peak = b.pos[1]
run(b, 6000, amp=0.0, freq=0.0, dt=DT, **AIR)
check("landingWhenDriveCollapses", b.pos[1] < 5.0 and b.pos[1] < peak - 20,
      f"peak {peak:.1f} mm -> {b.pos[1]:.2f} mm")

# 10. gravity is applied in mm/s2 on a millisecond clock: one 1 ms step of
#     pure gravity moves the vertical velocity by (almost exactly) g*1e-3
b = airborne(100000.0)
b.step(legs_in_contact=False, contact=0.0, dt=0.001)
expected = -GRAVITY * 0.001
check("unitsAreMillimetresPerSecondSquared", 0.95 <= b.vel[1] / expected <= 1.05,
      f"dv {b.vel[1]:.3f} mm/s vs g*1ms {expected:.3f}")

# 11. determinism: identical inputs give bit-identical state
a = run(Body(), 4000, fwd_target=12.0, contact=0.5, dt=DT)
c = run(Body(), 4000, fwd_target=12.0, contact=0.5, dt=DT)
check("deterministicIntegrator", a.pos == c.pos and a.vel == c.vel, "bit-identical")

print()
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("FAILED: none")