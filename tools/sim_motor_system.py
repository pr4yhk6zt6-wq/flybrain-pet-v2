#!/usr/bin/env python3
"""
Line-by-line mirror of ios/Sources/FlyBrainCore/MotorSystem.swift.

The Swift toolchain is not available in this environment, so this mirror lets
the gait generator be checked and calibrated BEFORE a CI run. It reproduced the
real failure that CI then reported verbatim:

    BodyDynamicsTests.testSilentCommandProducesNoHorizontalMotion
        y = 0.8 instead of 0.0

which turned out to be a physico-spatial bug, not a test artefact: the rigid
body's default position (SIMD3(0, 0, 1), a z-up assumption) sat 0.2 mm ABOVE
the y-up ground contact level, so the fly spawned airborne and fell. See
docs/PHYSICS.md for the full list of bugs this mirror has caught.

Run directly for a behaviour report; import it for assertions.
"""

import math

# ---- Leg / joint ------------------------------------------------------------

class JointState:
    def __init__(self, angle=0.0, velocity=0.0, torque=0.0,
                 limit_min=-1.2, limit_max=1.2, damping=2.0):
        self.angle = angle
        self.velocity = velocity
        self.torque = torque
        self.limit_min = limit_min
        self.limit_max = limit_max
        self.damping = damping


class Leg:
    def __init__(self, side, position):
        self.side = side
        self.position = position
        self.coxa = JointState()
        self.femur = JointState()
        self.tibia = JointState()
        self.phase = 0.0
        self.is_swing = False
        self.stance_time_ms = 0.0
        self.swing_time_ms = 0.0


class MotorOutput:
    def __init__(self):
        self.leg_torques = []
        self.forward_speed_target = 0.0
        self.lateral_speed_target = 0.0
        self.leg_contact_fraction = 1.0
        self.legs_in_contact = True
        self.planted_leg_drive = 0.0
        self.walk_drive = 0.0
        self.turning_bias = 0.0
        self.wing_stroke_freq = 0.0
        self.wing_stroke_amplitude = 0.0
        self.left_right_asymmetry = 0.0
        self.haltere_drive = 0.0
        self.pitch_bias = 0.0
        self.proboscis_extension = 0.0
        self.antenna_angle = 0.0
        self.haltere_beat = 0.0


# ---- MotorSystem ------------------------------------------------------------

STEP_ACTIVITY_THRESHOLD = 0.08
MAX_STANCE_MS = 90.0
MIN_SWING_MS = 35.0
STRIDE_RATE_PER_UNIT_DRIVE = 10.0
MIN_SUPPORT_LEGS = 3
MAX_TURN_RATE_DEG_PER_S = 200.0


def support_would_be_stable(legs, i):
    """Can the remaining legs carry the load if leg `i` lifts?

    Requires MIN_SUPPORT_LEGS planted AND at least one on each side: a tripod
    is statically stable only if its support polygon contains the centre of
    mass, and {right-front, right-mid, right-hind} does not. Mirrors
    MotorSystem.supportWouldBeStable(without:).
    """
    left = sum(1 for j, l in enumerate(legs)
               if j != i and not l.is_swing and j < 3)
    right = sum(1 for j, l in enumerate(legs)
                if j != i and not l.is_swing and j >= 3)
    return left + right >= MIN_SUPPORT_LEGS and left >= 1 and right >= 1
STRIDE_LENGTH_MM = 3.0


class MotorSystem:
    def __init__(self):
        self.legs = []
        for side in (1, 2):
            for pos in (0, 1, 2):
                self.legs.append(Leg(side, pos))
        self.output = MotorOutput()
        self.wing_muscle_drive = 0.0
        self.haltere_drive = 0.0
        self.proboscis_drive = 0.0

    def planted_neighbours(self, i):
        partner = i + 3 if i < 3 else i - 3
        n = 0
        if 0 <= partner < len(self.legs) and not self.legs[partner].is_swing:
            n += 1
        for j, l in enumerate(self.legs):
            if j != i and j != partner and not l.is_swing:
                n += 1
        return n

    def update(self, neural_drive, dt):
        """dt in MILLISECONDS (the neural clock), as in the Swift original."""
        out = MotorOutput()
        dt_sec = dt / 1000.0

        for i, leg in enumerate(self.legs):
            drive = neural_drive[i] if i < len(neural_drive) else 0.0
            drive = min(max(drive, 0.0), 1.0)

            # stance / swing sequencing (timers in ms, as in Swift)
            leg.stance_time_ms += dt
            leg.swing_time_ms += dt
            if leg.is_swing:
                if leg.swing_time_ms >= MIN_SWING_MS:
                    leg.is_swing = False
                    leg.swing_time_ms = 0.0
                    leg.stance_time_ms = 0.0   # touchdown: a NEW stance starts
                    leg.phase = 0.5
            else:
                # A leg may only lift if the remaining legs can carry the load
                # AND straddle the body. The drift timeout only REQUESTS a
                # step; it does not grant one (OR-ing it with the support check
                # is what let all six legs lift on the same tick).
                wants_step = (drive >= STEP_ACTIVITY_THRESHOLD
                              or leg.stance_time_ms >= MAX_STANCE_MS)
                if wants_step and support_would_be_stable(self.legs, i):
                    leg.is_swing = True
                    leg.swing_time_ms = 0.0
                    leg.phase = 0.0
            # publish immediately: the next leg must see THIS decision, not the
            # pre-update snapshot (self.legs[i] = leg used to happen at loop end)
            self.legs[i] = leg

            stride_rate = drive * STRIDE_RATE_PER_UNIT_DRIVE
            leg.phase = (leg.phase + dt_sec * stride_rate) % 1.0

            in_swing = leg.is_swing
            target_coxa = 0.6 if in_swing else -0.2
            target_femur = -0.8 if in_swing else 0.3
            stiffness = 4 + 8 * drive
            leg.coxa.velocity += (target_coxa - leg.coxa.angle) * dt_sec * stiffness
            leg.femur.velocity += (target_femur - leg.femur.angle) * dt_sec * stiffness
            leg.coxa.velocity *= max(0.0, 1 - leg.coxa.damping * dt_sec)
            leg.femur.velocity *= max(0.0, 1 - leg.femur.damping * dt_sec)
            leg.coxa.angle = min(max(leg.coxa.angle + leg.coxa.velocity * dt_sec,
                                     leg.coxa.limit_min), leg.coxa.limit_max)
            leg.femur.angle = min(max(leg.femur.angle + leg.femur.velocity * dt_sec,
                                      leg.femur.limit_min), leg.femur.limit_max)
            muscle = drive
            leg.coxa.torque = muscle * 0.5
            leg.femur.torque = muscle * 0.5
            out.leg_torques.append(0.0 if in_swing
                                   else abs(leg.coxa.torque) + abs(leg.femur.torque))

        out.wing_stroke_freq = 180 * min(self.wing_muscle_drive, 1.0)
        out.wing_stroke_amplitude = 0.9 * min(self.wing_muscle_drive, 1.0)
        out.haltere_beat = out.wing_stroke_freq / 180

        planted = [l for l in self.legs if not l.is_swing]
        out.planted_leg_drive = len(planted) / max(len(self.legs), 1)
        out.leg_contact_fraction = out.planted_leg_drive
        out.legs_in_contact = len(planted) > 0
        mean_drive = (sum(l.coxa.torque for l in planted) / len(planted)) if planted else 0.0
        out.walk_drive = mean_drive
        out.forward_speed_target = mean_drive * STRIDE_RATE_PER_UNIT_DRIVE * STRIDE_LENGTH_MM
        left = sum(l.coxa.torque for l in self.legs[:3]) / 3
        right = sum(l.coxa.torque for l in self.legs[3:]) / 3
        out.turning_bias = (left - right) * MAX_TURN_RATE_DEG_PER_S
        out.lateral_speed_target = (left - right) * STRIDE_LENGTH_MM * 4
        out.haltere_drive = min(self.wing_muscle_drive, 1.0)
        out.pitch_bias = ((self.legs[0].coxa.torque + self.legs[3].coxa.torque) / 2
                          - (self.legs[2].coxa.torque + self.legs[5].coxa.torque) / 2)
        out.proboscis_extension = self.proboscis_drive
        self.output = out
        return out


# ---- Behaviour report -------------------------------------------------------

def main():
    failures = []
    print("MotorSystem mirror — 6 legs, drive 0.8, dt = 100 ms")
    m = MotorSystem()
    out = m.update([0.8] * 6, dt=100.0)
    planted = sum(1 for l in m.legs if not l.is_swing)
    print("  leg torques      :", [round(t, 4) for t in out.leg_torques])
    print("  sum of torques   :", round(sum(out.leg_torques), 4))
    print("  swing states     :", [l.is_swing for l in m.legs])
    print("  stance times (ms):", [round(l.stance_time_ms, 1) for l in m.legs])
    print("  fwd setpoint     :", round(out.forward_speed_target, 3), "mm/s")
    print("  turning bias     :", round(out.turning_bias, 3), "deg/s")
    if sum(out.leg_torques) <= 0:
        failures.append("a 100 ms tick produced no leg torque at all")
    if planted < MIN_SUPPORT_LEGS:
        failures.append(f"100 ms tick left only {planted} planted legs")

    print("\nonset sweep (leg torque as drive crosses the step threshold)")
    for d in (0.0, 0.04, 0.09, 0.4):
        m = MotorSystem()
        out = m.update([d] * 6, dt=100.0)
        print(f"  drive {d:4.2f} -> torques {[round(t,4) for t in out.leg_torques]} "
              f"sum {sum(out.leg_torques):.3f} fwd {out.forward_speed_target:.2f}")

    # --- gating: the gait must never lose support, and it must be a tripod ---
    m = MotorSystem()
    tripod_a, tripod_b = {0, 1, 3}, {2, 4, 5}
    visited = set()
    min_planted = 6
    for _ in range(4000):
        m.update([0.8] * 6, dt=0.1)
        swing = frozenset(i for i, l in enumerate(m.legs) if l.is_swing)
        visited.add(swing)
        min_planted = min(min_planted, 6 - len(swing))
    print(f"\ngait over 400 ms: min planted = {min_planted}, "
          f"swing sets = {sorted(sorted(s) for s in visited)}")
    if min_planted < MIN_SUPPORT_LEGS:
        failures.append(f"gait dropped below {MIN_SUPPORT_LEGS} planted legs "
                        f"(min {min_planted})")
    for swing in visited:
        if not (swing <= tripod_a or swing <= tripod_b or len(swing) <= 1):
            failures.append(f"non-tripod swing set {sorted(swing)}")
    if tripod_a not in visited or tripod_b not in visited:
        failures.append("alternating tripod never completed a full cycle")

    print()
    for f in failures:
        print("FAIL:", f)
    print("FAILURES:", len(failures))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()