#!/usr/bin/env python3
"""Metabolism vs ingestion — is the energy term a live quantity or a frozen one?

WHY THIS GATE EXISTS
--------------------
TASK-005 asks for a feeding loop, and the obvious work is on the feed side:
`InternalState.feed()` has no consumer. But a loop has two ends, and the drain
end has never been measured. If metabolism is so slow that energy cannot move
within any timescale a user can observe, then wiring `feed()` in produces an
animal that is permanently, silently full — a loop with a working pump and no
pressure. Or the reverse: a drain so fast that the fly starves before it can
walk to food.

Both failures look identical from inside the simulator (an `energy` field that
is never read), which is why this is measured rather than reasoned about.

WHAT IT MEASURES
----------------
Reads the metabolism constants out of the Swift source (no copies that can
drift), rebuilds the `InternalState.advance` arithmetic exactly, and reports:

  * energy lost per SECOND of neural time and per second of WALL time, using the
    ~42x slow-motion factor that `tools/measure_time_scale.py` gates;
  * how long a full-energy fly takes to reach the hunger threshold under that
    clock;
  * the ingestion side, once it exists, expressed in the same units.

HOW THIS IS EVIDENCED
---------------------
This does NOT execute Swift. It is arithmetic on constants regexed out of
`InternalState.swift`, and the same arithmetic is asserted in
`FeedingLoopTests.swift`, which runs only in CI. What this gate adds is the
answer to "is the balance physically sensible at the clock this device runs",
which is a question a unit test on a single step cannot ask.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
STATE = ROOT / "ios/Sources/FlyBrainCore/InternalState.swift"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def swift_number(src: str, pattern: str, name: str) -> float:
    m = re.search(pattern, src)
    if not m:
        raise SystemExit(f"could not read {name} from Swift source — the gate "
                         f"refuses to guess a value it cannot find")
    return float(m.group(1))


state = STATE.read_text(encoding="utf-8")

# `energy = max(0, energy - metabolic * dtH * 10)`
#   metabolic = 0.02 * (0.3 + activity*0.7) * (1 + max(0, temp-25)*0.01)
base_rate = swift_number(state, r"let metabolic\s*=\s*([0-9.]+)\s*\*", "metabolic base")
rest_factor = swift_number(state, r"\(([0-9.]+)\s*\+\s*activityLevel", "activity floor")
activity_coeff = swift_number(state, r"activityLevel\s*\*\s*([0-9.]+)\)", "activity coefficient")
metabolic_clock = swift_number(state, r"metabolicHoursPerNeuralHour\s*:\s*Float\s*=\s*([0-9_]+)", "metabolic clock ratio")
energy_default = swift_number(state, r"energy:\s*Float\s*=\s*([0-9.]+)", "default energy")
hunger_coeff = swift_number(state, r"hungerDrive\s*:\s*Float\s*\{\s*max\(0,\s*1\s*-\s*energy\s*\*\s*([0-9.]+)\)", "hunger coefficient")

core = CORE.read_text(encoding="utf-8")
# `dt` lives in `SimulationParameters`, declared in NeuralEngine.swift, next to
# the engine that consumes it — not in SimulationCore.swift.
engine = (ROOT / "ios/Sources/FlyBrainCore/NeuralEngine.swift").read_text(encoding="utf-8")
dt_ms = swift_number(engine, r"dt:\s*Double\s*=\s*([0-9.]+)", "neural dt")

# Real-time factor: steps per frame x dt, against a 60 Hz frame.
app = (ROOT / "ios/Sources/FlyBrainPetApp/AppModel.swift").read_text(encoding="utf-8")
steps_per_frame = swift_number(app, r"core\.run\(steps:\s*([0-9]+)\)", "steps per frame")
frame_ms = 1000.0 / 60.0
neural_ms_per_frame = dt_ms * steps_per_frame
real_time_factor = neural_ms_per_frame / frame_ms

print(f"neural dt          : {dt_ms} ms/step")
print(f"steps per frame    : {int(steps_per_frame)}  -> {neural_ms_per_frame:.3f} ms neural per "
      f"{frame_ms:.3f} ms wall")
print(f"slow-motion factor : {1/real_time_factor:.1f}x")
print()

ACTIVITY = 0.2   # a fly that is walking, not sprinting
metabolic = base_rate * (rest_factor + ACTIVITY * activity_coeff)


def drain_per_ms(neural_ms: float) -> float:
    dt_h = neural_ms / 3_600_000.0
    return metabolic * dt_h * metabolic_clock


per_neural_second = drain_per_ms(1000.0)
per_wall_second = per_neural_second * real_time_factor
hunger_threshold = (1.0 - 0.0) / hunger_coeff   # energy at which hungerDrive leaves 0

print(f"metabolic rate     : {metabolic:.5f} per simulated hour-unit")
print(f"drain / neural s   : {per_neural_second:.3e} energy units")
print(f"drain / wall s     : {per_wall_second:.3e} energy units  (what the user sees)")
print(f"energy starts at   : {energy_default}")
print(f"hunger begins below: {hunger_threshold:.3f}  (hungerDrive > 0)")
print()

# How long to go from full to hungry on the wall clock the app actually runs at.
drop = energy_default - hunger_threshold
if per_wall_second > 0:
    wall_seconds = drop / per_wall_second
    print(f"full -> hungry     : {wall_seconds:,.0f} s of wall time "
          f"({wall_seconds/3600:,.1f} h, {wall_seconds/86400:,.1f} days)")
else:
    wall_seconds = float("inf")
    print("full -> hungry     : never (no drain)")

check(per_neural_second > 0,
      "energy is drained at all (a constant energy field would make hunger decorative)",
      f"{per_neural_second:.3e}/neural s")

# A loop is only observable if hunger can move inside a session. The threshold is
# generous on purpose: an hour of continuous use is a long session, and the fly
# also has to be able to EAT within it.
SESSION_SECONDS = 3600.0
check(wall_seconds < SESSION_SECONDS,
      "hunger is reachable within a one-hour session at the shipped clock",
      f"{wall_seconds:,.0f} s to starve (vs {SESSION_SECONDS:,.0f} s session) — "
      "if this fails, no user has ever seen the fly get hungry")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    print("\nThe failure is a UNIT/CALIBRATION problem, not necessarily a code")
    print("problem. The fix is to state the intended timescale and set the")
    print("constants to it, not to loosen the gate.")
    return_code = 1
else:
    print("all checks passed — the energy term moves on an observable timescale.")
    return_code = 0

sys.exit(return_code)