#!/usr/bin/env python3
"""Feeding-loop mirror: hunger -> taste gain -> SEZ -> proboscis -> ingestion -> energy.

There is no Swift toolchain on this device, so this reproduces the arithmetic of
the closed feeding loop offline, reading every constant OUT of the Swift source
rather than restating it. A number typed here twice can drift from the code; a
number read from the code cannot.

WHY THIS GATE EXISTS. TASK-005 was "feeding and energy are not closed". The
symptoms were individually small and jointly total:

  * `InternalState.hungerDrive` had NO consumer. It was computed correctly and
    read by nothing. A modulatory signal with no target is a decoration.
  * `InternalState.feed()` had no caller, so `energy` could only ever fall.
  * metabolism ran on NEURAL time, and the neural clock itself runs ~42x slower
    than wall time, so a fly took 40.6 WALL-CLOCK HOURS to get hungry
    (tools/measure_metabolism_balance.py). No session had ever contained a
    hungry fly, so even a perfectly wired loop would have had nothing to drive.

Each fix is asserted here as a SEPARATE signature, because they are separate
bugs that could each be reverted alone:

  SIGNATURE "no consumer"     -> test that a hungrier fly pushes a bigger taste
                                 current through the SAME acceptance.
  SIGNATURE "no ingestion"    -> test that energy rises only by matter that
                                 leaves the substrate, and by that amount.
  SIGNATURE "unreachable"     -> test that hunger is reachable within a session.
  SIGNATURE "one gain"        -> test that hunger raises sweet and LOWERS
                                 bitter, i.e. that the modulation is signed.
                                 (A single scalar saturated both directions.)

Run: python3 tools/mirror_feeding_loop.py
"""
from __future__ import annotations

import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
STATE = ROOT / "ios/Sources/FlyBrainCore/InternalState.swift"
WORLD = ROOT / "ios/Sources/FlyBrainCore/World.swift"
BODY = ROOT / "ios/Sources/FlyBrainCore/BodyModel.swift"
ENGINE_SRC = ROOT / "ios/Sources/FlyBrainCore/NeuralEngine.swift"
DYN = ROOT / "ios/Sources/FlyBrainCore/BodyDynamics.swift"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def read(src: str, pattern: str, what: str) -> float:
    m = re.search(pattern, src)
    if not m:
        print(f"could not read {what} from the Swift source — "
              f"the gate refuses to guess a value it cannot find")
        sys.exit(1)
    return float(m.group(1).replace("_", ""))


core = CORE.read_text(encoding="utf-8")
state = STATE.read_text(encoding="utf-8")
world = WORLD.read_text(encoding="utf-8")
body = BODY.read_text(encoding="utf-8")
engine_src = ENGINE_SRC.read_text(encoding="utf-8")
dyn_src = DYN.read_text(encoding="utf-8")

# ---- constants, all read from Swift ----------------------------------------
GUST_REFERENCE = read(
    ROOT.joinpath("ios/Sources/FlyBrainCore/SensoryInterface.swift").read_text(encoding="utf-8"),
    r"current: a \* ([0-9.]+)", "gustatory current scale")
APPETITIVE_FLOOR = read(state, r"gustatoryGainFloor\s*:\s*Float\s*=\s*([0-9.]+)",
                        "appetitive gain floor")
APPETITIVE_SPAN = read(state, r"hungerGustatoryGainSpan\s*:\s*Float\s*=\s*([0-9.]+)",
                       "appetitive gain span")
AVERSIVE_SPAN = read(state, r"aversiveBluntingSpan\s*:\s*Float\s*=\s*([0-9.]+)",
                     "aversive blunting span")
AVERSIVE_ONSET = read(state, r"aversiveBluntingOnsetHunger\s*:\s*Float\s*=\s*([0-9.]+)",
                      "aversive blunting onset")
HUNGER_REF = read(state, r"hungerDrive\s*:\s*Float\s*\{\s*max\(0,\s*1\s*-\s*energy\s*\*\s*([0-9.]+)\)",
                  "hunger reference energy")
METABOLIC_CLOCK = read(state, r"metabolicHoursPerNeuralHour\s*:\s*Float\s*=\s*([0-9_]+)",
                       "metabolic clock ratio")
INGEST_RATE = read(core, r"ingestionRatePerMs\s*:\s*Float\s*=\s*([0-9.]+)",
                   "ingestion rate per ms")
ENERGY_PER_RESERVE = read(core, r"energyPerReserveUnit\s*:\s*Float\s*=\s*([0-9.]+)",
                          "energy per reserve unit")
CONTACT_REACH = read(world, r"contactReach\s*:\s*Float\s*=\s*([0-9.]+)", "contact reach")
PATCH_RADIUS = read(world, r"radius:\s*Float\s*=\s*([0-9.]+)\)", "default patch radius")
PROBOSCIS_LEN = read(body, r"proboscisLengthMm\s*:\s*Float\s*=\s*([0-9.]+)", "proboscis length")
PROBOSCIS_REACH = read(body, r"proboscisReachAngle\s*:\s*Float\s*=\s*([0-9.]+)",
                       "proboscis reach angle")
DT_MS = read(engine_src, r"dt:\s*Double\s*=\s*([0-9.]+)", "neural dt")

print(f"gustatory current @ |acceptance|=1 : {GUST_REFERENCE} nA-scale units")
print(f"appetitive gain  floor/span        : {APPETITIVE_FLOOR} -> "
      f"{APPETITIVE_FLOOR + APPETITIVE_SPAN}")
print(f"aversive  gain   blunting/onset    : -{AVERSIVE_SPAN} past hunger {AVERSIVE_ONSET}")
print(f"metabolic clock                    : {METABOLIC_CLOCK:,.0f} metab-h per neural-h")
print(f"ingestion                          : {INGEST_RATE}/ms  -> "
      f"{INGEST_RATE * 1000:.3f} reserve / neural s")
print(f"contact reach / patch radius       : {CONTACT_REACH} mm / {PATCH_RADIUS} mm")
# ---- the mirrored functions -------------------------------------------------

def hunger_drive(energy: float) -> float:
    """InternalState.hungerDrive"""
    return max(0.0, 1.0 - energy * HUNGER_REF)


def appetitive_gain(energy: float) -> float:
    """InternalState.gustatoryAppetitiveGain"""
    return APPETITIVE_FLOOR + hunger_drive(energy) * APPETITIVE_SPAN


def aversive_gain(energy: float) -> float:
    """InternalState.gustatoryAversiveGain"""
    h = hunger_drive(energy)
    excess = max(0.0, h - AVERSIVE_ONSET) / max(1 - AVERSIVE_ONSET, 1e-4)
    return APPETITIVE_FLOOR * (1 - excess * AVERSIVE_SPAN)


def hunger_modulated(acceptance: float, energy: float) -> float:
    """SimulationCore.hungerModulated — sign preserved, magnitude scaled."""
    if acceptance == 0:
        return 0.0
    g = appetitive_gain(energy) if acceptance > 0 else aversive_gain(energy)
    sign = -1.0 if acceptance < 0 else 1.0
    return sign * min(abs(acceptance) * g, 1.0)


def gustatory_current(acceptance: float, energy: float) -> float:
    """SimulationCore -> SensoryInterface.gustatoryInput: a * 40."""
    return hunger_modulated(acceptance, energy) * GUST_REFERENCE


def metabolic_energy(energy: float, neural_ms: float, activity: float,
                     temp_c: float = 25.0) -> float:
    """InternalState.advance — energy after `neural_ms` of neural time."""
    dt_h = neural_ms / 3_600_000.0
    metabolic_h = dt_h * METABOLIC_CLOCK
    metabolic = 0.02 * (0.3 + activity * 0.7) * (1 + max(0, temp_c - 25) * 0.01)
    return max(0.0, energy - metabolic * metabolic_h)


def ingest(reserve: float, neural_ms: float) -> tuple[float, float]:
    """World.ingest + SimulationCore.feed. Returns (taken, new_reserve)."""
    want = neural_ms * INGEST_RATE
    taken = min(want, reserve)
    return taken, reserve - taken


# ---- SIGNATURE: "unreachable" ----------------------------------------------
# metabolism ran on neural time; the neural clock is ~42x slow-motion. The wall
# time to go from fed to hungry is the number that decides whether a user can
# ever see this.
STEPS_PER_FRAME = 4
FRAME_MS = 1000.0 / 60
neural_ms_per_wall_s = (STEPS_PER_FRAME * DT_MS) / (FRAME_MS / 1000.0)
slow_motion = 1.0 / (neural_ms_per_wall_s / 1000.0)

energy = 0.8
hungry_at = (1.0 / HUNGER_REF)          # hungerDrive > 0
steps = 0
neural_ms = 0.0
while energy > hungry_at and steps < 200_000_000:
    energy = metabolic_energy(energy, DT_MS, activity=0.5)
    neural_ms += DT_MS
    steps += 1
wall_s = neural_ms / neural_ms_per_wall_s
print(f"\nfull -> hungry : {wall_s:,.0f} s of wall time "
      f"({wall_s / 60:.1f} min, {wall_s / 3600:.1f} h)")
check(wall_s < 3600,
      "hunger is reachable within a one-hour session",
      f"{wall_s:.0f} s vs 3600 s — if this fails, no user has ever seen "
      f"a hungry fly, and the loop below it is unobservable however correct")
check(wall_s > 20,
      "hunger is not INSTANT (a fly that starves in seconds is a timer, "
      "not a motivation)",
      f"{wall_s:.0f} s")# ---- SIGNATURE: "one gain" -------------------------------------------------
# The first version of this code used a single `gustatoryGain` on a signed
# acceptance. That makes a starving fly MORE repelled by bitter food, which is
# backwards (Inagaki/Anderson 2014, PMID 25451195: starvation raises sweet and
# LOWERS bitter sensitivity, independently and reciprocally).
fed, starved = 0.95, 0.05          # energy
print(f"\nhunger at energy {fed} / {starved}: "
      f"{hunger_drive(fed):.3f} / {hunger_drive(starved):.3f}")
check(appetitive_gain(starved) > appetitive_gain(fed),
      "hunger RAISES the appetitive (sweet) gain",
      f"{appetitive_gain(fed):.3f} -> {appetitive_gain(starved):.3f}")
check(aversive_gain(starved) < aversive_gain(fed),
      "hunger LOWERS the aversive (bitter) gain",
      f"{aversive_gain(fed):.3f} -> {aversive_gain(starved):.3f}")
check(aversive_gain(0.0) > 0,
      "a starving fly is still AVERSIVE to bitter, not blind to it",
      f"gain at full hunger = {aversive_gain(0.0):.3f} > 0")

# reciprocal, not one scalar: the two gains must move in OPPOSITE directions
check((appetitive_gain(starved) - appetitive_gain(fed)) > 0
      and (aversive_gain(starved) - aversive_gain(fed)) < 0,
      "sweet and bitter are modulated RECIPROCALLY (not by one scalar)",
      "a single multiplier would move both the same way")

# the ordering from the paper: the low-risk change (more sugar) happens at
# LOWER hunger than the high-risk one (less bitterness).
def first_hunger_where(pred, lo=0.0, hi=1.0):
    for i in range(1001):
        h = lo + (hi - lo) * i / 1000
        e = (1 - h) / HUNGER_REF
        if pred(e):
            return h
    return None


sweet_rises = first_hunger_where(lambda e: appetitive_gain(e) > APPETITIVE_FLOOR + 1e-6)
bitter_falls = first_hunger_where(lambda e: aversive_gain(e) < APPETITIVE_FLOOR - 1e-6)
check(sweet_rises is not None and bitter_falls is not None
      and sweet_rises < bitter_falls,
      "the low-risk change (sweeter) precedes the high-risk one (less bitter)",
      f"sweet rises at hunger {sweet_rises:.3f}, bitter falls at {bitter_falls:.3f}")

# a sign can never flip, however hungry
for e in (0.0, 0.3, 0.7, 1.0):
    for acc in (-1.0, -0.5, 0.5, 1.0):
        v = hunger_modulated(acc, e)
        assert (v == 0) == (acc == 0) or (v > 0) == (acc > 0)

sign_ok = all((hunger_modulated(a, e) >= 0) == (a >= 0)
              for e in (0.0, 0.3, 0.7, 1.0) for a in (-1.0, -0.5, 0.5, 1.0))
check(sign_ok, "hunger modulation can never flip the sign of a taste",
      "a phagostimulant never becomes aversive under hunger")

# ---- SIGNATURE: "no consumer" ----------------------------------------------
# Before the fix, hungerDrive was read by nothing, so the SAME acceptance pushed
# the SAME current whatever the energy. The observable difference is the point.
same_food = 0.8
c_fed = gustatory_current(same_food, fed)
c_hungry = gustatory_current(same_food, starved)
print(f"\nidentical food (acceptance {same_food}): "
      f"{c_fed:.2f} nA when fed, {c_hungry:.2f} nA when hungry")
check(abs(c_hungry - c_fed) > 1e-6,
      "hunger CHANGES the current the same food injects (hungerDrive has a consumer)",
      f"{c_fed:.2f} -> {c_hungry:.2f}")
check(c_hungry > c_fed,
      "the change is in the appetitive direction (hungrier -> stronger)",
      f"x{c_hungry / c_fed:.2f}")

# ---- SIGNATURE: "no ingestion" ---------------------------------------------
# energy must rise ONLY by matter that left the substrate, and by that amount.
meal_ms = 500.0
reserve = 0.05
taken, left = ingest(reserve, meal_ms)
energy_after = min(1.0, 0.2 + taken * ENERGY_PER_RESERVE)
print(f"\nmeal: {meal_ms:.0f} ms of neural time on {reserve} reserve "
      f"-> took {taken:.4f}, left {left:.4f}, energy 0.200 -> {energy_after:.4f}")
check(abs(left - (reserve - taken)) < 1e-9,
      "the reserve removed EQUALS the amount swallowed (matter is conserved)",
      f"{reserve} - {taken:.4f} = {left:.4f}")
check(abs((energy_after - 0.2) - taken * ENERGY_PER_RESERVE) < 1e-9,
      "energy rose by exactly the reserve swallowed",
      f"dE = {energy_after - 0.2:.4f} = {taken:.4f}")

# a source cannot be eaten past empty, and an empty source yields nothing
t1, r1 = ingest(0.01, 5000.0)
t2, r2 = ingest(r1, 5000.0)
check(abs(r1) < 1e-9, "a source is drained to exactly zero, not below",
      f"after a big bite: reserve {r1:.2e}")
check(t2 == 0.0, "an exhausted source yields NOTHING",
      f"second bite on an empty patch took {t2:.2e}")

# ---- the loop actually closes: starve, eat, recover ------------------------
energy = 0.8
for _ in range(2000):                      # starve (synthetic neural seconds)
    energy = metabolic_energy(energy, 100.0, activity=0.5)
starved_energy = energy
reserve = 1.0
# now stand on food: ingestion only while the labellum is on it, at the loop's
# rate, for as long as it takes to refill.
neural_ms_on_food = 0.0
while energy < 0.9 and reserve > 0 and neural_ms_on_food < 1_000_000:
    taken, reserve = ingest(reserve, DT_MS)
    energy = min(1.0, energy + taken * ENERGY_PER_RESERVE)
    neural_ms_on_food += DT_MS
print(f"\nstarved to {starved_energy:.4f}, then {neural_ms_on_food:.1f} ms on food "
      f"-> energy {energy:.4f}, reserve left {reserve:.4f}")
check(energy > starved_energy,
      "a hungry fly that finds food gets its energy back (loop closes)",
      f"{starved_energy:.4f} -> {energy:.4f}")
check(hunger_drive(energy) < hunger_drive(starved_energy),
      "eating REDUCES hunger (the satiety half of the loop is real)",
      f"hunger {hunger_drive(starved_energy):.3f} -> {hunger_drive(energy):.3f}")

# ---- the contact geometry the bootstrap needs ------------------------------
# Feet sit at the body origin at `standHeightMm`; the proboscis hangs off the
# HEAD segment (y offset 0) and swings down as the joint opens. Two things must
# both hold, or the feeding loop is unreachable no matter how well it is wired:
#   (a) the extended labellum must come within contact reach of the ground, or
#       no joint angle touches food and ingestion can never happen;
#   (b) foot and mouth must be able to touch the SAME patch, or "taste with the
#       feet, confirm with the mouth" — the pathway's bootstrap — is impossible.
stand_h = read(dyn_src, r"standHeightMm\s*:\s*Float\s*=\s*([0-9.]+)", "stand height")
head_m = re.search(r'name: "head", position: SIMD3\(([-0-9.]+), ([-0-9.]+), ([-0-9.]+)\)',
                   body)
assert head_m, "could not read the head segment offset — the gate refuses to guess"
head_x, head_y = float(head_m.group(1)), float(head_m.group(2))
max_angle = read(body, r"proboscisMaxAngle\s*:\s*Float\s*=\s*([0-9.]+)",
                 "max proboscis angle")

# The denominator is read from `proboscisExtensionFraction` itself. Hardcoding
# `max_angle / max_angle == 1` hid the exact bug this gate exists to catch: the
# tip geometry used to divide by a literal 1.4, so "full drive" extended only
# 57% and the labellum never reached the substrate. A mirror that assumes the
# fraction it should be measuring cannot detect that.
m = re.search(r"func proboscisExtensionFraction\(angle: Float\) -> Float \{\s*\n\s*min\(max\(angle / ([A-Za-z0-9_.]+)",
              body)
if not m:
    print("could not find proboscisExtensionFraction's denominator in the Swift "
          "source — the gate refuses to guess a value it cannot find")
    sys.exit(1)
ext_denom = m.group(1).split(".")[-1]
ext_denom_val = read(body, rf"{re.escape(ext_denom)}\s*:\s*Float\s*=\s*([0-9.]+)",
                     "extension denominator value")
print(f"extension denominator              : {ext_denom} = {ext_denom_val} rad")
frac = min(max(max_angle / ext_denom_val, 0), 1)
axis_y = -math.sin(max_angle) * PROBOSCIS_LEN * frac
axis_x = math.cos(max_angle) * PROBOSCIS_LEN * frac
tip_y = stand_h + head_y + axis_y
tip_x = head_x + axis_x
print(f"\nhead offset x={head_x:.2f} y={head_y:.2f}, stand height {stand_h:.2f} mm")
print(f"extended labellum: {tip_x:.3f} mm forward of the feet, "
      f"{tip_y:.3f} mm above the ground plane")

check(tip_y <= CONTACT_REACH,
      "the extended labellum can actually reach the substrate",
      f"tip is {tip_y:.3f} mm up vs {CONTACT_REACH:.2f} mm contact reach — "
      f"above this no joint angle touches food and ingestion is unreachable")
check(tip_y >= 0,
      "the labellum reaches DOWN to the ground, not through it",
      f"tip {tip_y:.3f} mm above the plane (negative would mean the mouth is "
      f"buried in the substrate)")
check(PATCH_RADIUS + CONTACT_REACH >= tip_x,
      "a patch is wide enough that feet AND mouth can be on the same food",
      f"patch {PATCH_RADIUS:.2f} + reach {CONTACT_REACH:.2f} = "
      f"{PATCH_RADIUS + CONTACT_REACH:.2f} mm >= {tip_x:.2f} mm needed — a "
      f"point source could not satisfy this, so the bootstrap would be impossible")


# ---- source pins: the mirror cannot EXECUTE Swift, so it pins the shape -----
# `hungerModulated` in Swift must branch on the taste sign and reference BOTH
# gains. A mirror cannot detect a Swift-side regression here by running it, so
# this checks the source text: with a single unsigned gain, a starving fly would
# become MORE repelled by bitter food — backwards on the nutrient state where it
# most needs calories (Inagaki 2014, PMID 25451195).
mod_m = re.search(r"func hungerModulated[^}]*?\{[\s\S]*?\n    \}", core)
assert mod_m, "hungerModulated not found in SimulationCore.swift"
mod_src = mod_m.group(0)
check("acceptance > 0" in mod_src and "gustatoryAversiveGain" in mod_src
      and "gustatoryAppetitiveGain" in mod_src,
      "Swift hungerModulated branches on taste sign and uses BOTH gains",
      "a single unsigned gain would invert the aversive half")
check("min(abs(acceptance) * g, 1)" in mod_src,
      "hunger modulation clamps while preserving sign", "")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed — hunger has a consumer, a meal is matter that moves,\n"
      "the modulation is signed like the animal's, and the loop closes inside\n"
      "a session.\n"
      "  HOW THIS IS EVIDENCED: every constant above is read from the Swift\n"
      "  source by regex, so changing one fails here rather than drifting. This\n"
      "  does NOT execute Swift — the loop is asserted semantically by\n"
      "  FeedingLoopTests.swift, which runs only in CI on a macOS runner.")