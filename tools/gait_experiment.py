#!/usr/bin/env python3
"""
Gait-coordination experiment for MotorSystem.

CI reported:
    SensoryTests.testMotorCPGProducesLegTorques
        XCTAssertGreaterThan failed: ("0.0") is not greater than ("0.0")

Root cause found here: `plantedNeighbours(of:)` is evaluated against the
PRE-update leg snapshot, so in the first tick every leg sees all 5 neighbours
planted, passes the check simultaneously, and all six legs lift. Nothing is
serialised, so the "support constraint" the source comment claims produces a
tripod gait does not produce anything — the fly simply drops all its feet.
The later `mechanicalTimeout` (>= 90 ms) makes it worse: every planted leg
times out on the same tick and lifts together.

This script compares candidate rules over time using the same leg order and
timers as the Swift code, so the rule that actually yields a stable
alternating pattern is chosen from measurements, not from the comment.

Rules compared
  0 current     : neighbours-planted >= 3 (pre-update snapshot)
  1 serialised  : same, but the array is written back in-loop (later legs see
                  earlier decisions)
  2 serialised+ : 1, plus the remaining support must straddle the body axis
                  (>= 1 planted leg on each side) — otherwise {R1,R2,R3} counts
                  as "stable support" while the fly would tip onto its left.

Run: python3 tools/gait_experiment.py
"""

import sys
sys.path.insert(0, "tools")
from sim_motor_system import MotorSystem  # noqa: E402

MIN_SUPPORT = 3


def plant_counts(legs, skip):
    left = sum(1 for j, l in enumerate(legs) if j != skip and j < 3 and not l.is_swing)
    right = sum(1 for j, l in enumerate(legs) if j != skip and j >= 3 and not l.is_swing)
    return left, right


def step(m, drive, dt, rule):
    """One update applying the candidate coordination rule."""
    dt_sec = dt / 1000.0
    torques = []
    for i, leg in enumerate(m.legs):
        leg.stance_time_ms += dt
        leg.swing_time_ms += dt
        if leg.is_swing:
            if leg.swing_time_ms >= 35.0:
                leg.is_swing = False
                leg.swing_time_ms = 0.0
                leg.stance_time_ms = 0.0
        else:
            left, right = plant_counts(m.legs, i)
            remaining = left + right
            if rule == 0:
                # pre-update snapshot; `legs` is not written back during the
                # loop, so every leg in this tick sees the same state
                snapshot = [l.is_swing for l in m.legs]
                neighbours = sum(1 for j, s in enumerate(snapshot)
                                 if j != i and not s)
                may = neighbours >= MIN_SUPPORT
            elif rule == 1:
                may = remaining >= MIN_SUPPORT
            else:
                may = remaining >= MIN_SUPPORT and left >= 1 and right >= 1
            want = (drive >= 0.08) or (leg.stance_time_ms >= 90.0)
            if want and may:
                leg.is_swing = True
                leg.swing_time_ms = 0.0
        torque = 0.0 if leg.is_swing else drive * 1.0
        torques.append(torque)
        # serialise under rules 1 and 2: later legs must see this decision
        if rule >= 1:
            m.legs[i] = leg
    return torques


def report(rule, seconds=1.2, drive=0.8, dt=1.0):
    m = MotorSystem()
    steps = int(seconds * 1000 / dt)
    lifted_counts, planted_hist = [], []
    for _ in range(steps):
        step(m, drive, dt, rule)
        lifted_counts.append(sum(1 for l in m.legs if l.is_swing))
        planted_hist.append(sum(1 for l in m.legs if not l.is_swing))
    min_planted = min(planted_hist)
    max_lifted = max(lifted_counts)
    # how often is at least one leg on each side planted?
    straddle_ok = True
    m2 = MotorSystem()
    for _ in range(steps):
        step(m2, drive, dt, rule)
        if sorted(l.side for l in m2.legs if not l.is_swing)[:1] and \
           not (any(l.side == 1 and not l.is_swing for l in m2.legs) and
                any(l.side == 2 and not l.is_swing for l in m2.legs)):
            straddle_ok = False
    print(f"rule {rule}: min legs planted = {min_planted}, max legs lifted = "
          f"{max_lifted}, always straddles = {straddle_ok}")
    return min_planted, straddle_ok


if __name__ == "__main__":
    print("First tick (drive 0.8, dt 100 ms) — the CI failure case")
    for rule in (0, 1, 2):
        m = MotorSystem()
        torques = step(m, 0.8, 100.0, rule)
        print(f"  rule {rule}: torques {[round(t,2) for t in torques]} "
              f"sum {sum(torques):.2f}  lifted "
              f"{[i for i,l in enumerate(m.legs) if l.is_swing]}")
    print()
    print("Over 1.2 s of simulated gait")
    for rule in (0, 1, 2):
        report(rule)