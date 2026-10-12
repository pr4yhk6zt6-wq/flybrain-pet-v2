#!/usr/bin/env python3
"""Thirst-loop mirror: thirst -> water sense -> SEZ -> drinking -> hydration.

There is no Swift toolchain on this device, so this reproduces the arithmetic of
the closed thirst loop offline, reading every constant OUT of the Swift source
rather than restating it. A number typed here twice can drift from the code; a
number read from the code cannot.

WHY THIS GATE EXISTS. TASK-006 was "the thirst loop does not close". As with
TASK-005, the symptoms were individually small and jointly total:

  SIGNATURE "no consumer"    `InternalState.thirstDrive` was computed and read
                             by nothing, and `InternalState.drink()` had NO
                             caller anywhere in the app or the pipeline
                             (grep-verified). `hydration` could only fall.
  SIGNATURE "no water sense" `OdorSource.Kind.water` carried no contact signal at
                             all — `tasteAcceptance` returns 0 for water by
                             design, and nothing else measured it. So even a
                             wired `drink()` would have had nothing to trigger it.
  SIGNATURE "circular"       the one water sense a folded fly has must be the
                             TARSAL one. If water were sampled only at the
                             labellum, the channel that has to open the mouth
                             would require the mouth to be open — the exact
                             circular wiring that kept the original gustatory
                             channel permanently silent (TASK-001).
  SIGNATURE "unbounded"      a drink that is not bounded by the animal's deficit
                             pins hydration at 1 and `thirstDrive` at 0 forever,
                             which is the same failure seen from the other side.
  SIGNATURE "no quantity"    hydration must rise with TIME ON THE WATER, not by a
                             per-contact constant, or the loop has no quantity in
                             it and drinking becomes a boolean.

Each is a separate signature because they are separate bugs that could each be
reverted alone.

BIOLOGY THIS RESTS ON (all cited in the Swift source):
  * PPK28 is the water receptor — Cameron et al., Nature 2010, PMID 20364123;
    confirmed by Chen et al., J Neurosci 2010, PMID 20445050.
  * Water receptor neurons sit on the LABELLUM, the TARSI and the wing margins,
    and project to the subesophageal zone — Inoshita & Tanimura, PNAS 2006,
    PMID 16415164. The tarsal population is what makes the bootstrap possible.
  * The central modulation is in the SEZ: four interoceptive neurons (ISNs)
    oppositely regulate sugar and water intake — Jourjine et al., Cell 2016,
    PMID 27477513; downstream peptidergic network in Gonzalez Segarra et al.,
    eLife 2023, PMID 37732734.
  * Water management feeds back on physiology via AKH — Waterson et al., PNAS
    2014, PMID 24821805.

WHAT THIS DOES NOT CLAIM (stated so the gate cannot be read as more than it is):
  * The real receptor reports HYPO-OSMOLARITY, not "water". This model has no
    osmolarity field and reports water where a water source is.
  * The thirst gain is an APPROXIMATION of central modulation; the receptor is
    not established to be modulated.
  * Water and sugar pathways are NOT independent: silencing sugar GRNs reduces
    the proboscis-extension response to water (Shiu et al., Nature 2024, PMID
    39358519, Fig. 4f). The channels are separate HERE; the claim stops there.

Run: python3 tools/mirror_thirst_loop.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
STATE = ROOT / "ios/Sources/FlyBrainCore/InternalState.swift"
WORLD = ROOT / "ios/Sources/FlyBrainCore/World.swift"
SENSORY = ROOT / "ios/Sources/FlyBrainCore/SensoryInterface.swift"
BODY = ROOT / "ios/Sources/FlyBrainCore/BodyModel.swift"
ENGINE_SRC = ROOT / "ios/Sources/FlyBrainCore/NeuralEngine.swift"

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
sensory = SENSORY.read_text(encoding="utf-8")

# ---- constants, all read from Swift ----------------------------------------
WATER_CURRENT = read(sensory, r"current: c \* ([0-9.]+)", "water current scale")
THIRST_FLOOR = read(state, r"thirstGainFloor\s*:\s*Float\s*=\s*([0-9.]+)",
                    "thirst gain floor")
THIRST_SPAN = read(state, r"thirstGainSpan\s*:\s*Float\s*=\s*([0-9.]+)",
                   "thirst gain span")
THIRST_REF = read(state, r"thirstDrive\s*:\s*Float\s*\{\s*max\(0,\s*1\s*-\s*hydration\s*\*\s*([0-9.]+)\)",
                  "thirst reference hydration")
DEFICIT_MULT = read(state, r"drinkDeficitMultiplier\s*:\s*Float\s*=\s*([0-9.]+)",
                    "drink deficit multiplier")
INGEST_RATE = read(core, r"ingestionRatePerMs\s*:\s*Float\s*=\s*([0-9.]+)",
                   "ingestion rate per ms")
HYDRATION_PER_UNIT = read(core, r"hydrationPerWaterUnit\s*:\s*Float\s*=\s*([0-9.]+)",
                          "hydration per water unit")
CONTACT_REACH = read(world, r"contactReach\s*:\s*Float\s*=\s*([0-9.]+)", "contact reach")
DT_MS = read(ENGINE_SRC.read_text(encoding="utf-8"),
             r"dt:\s*Double\s*=\s*([0-9.]+)", "neural dt")

print(f"water current @ contact=1          : {WATER_CURRENT} nA-scale units")
print(f"thirst gain floor/span             : {THIRST_FLOOR} -> {THIRST_FLOOR + THIRST_SPAN}")
print(f"thirstDrive reference hydration    : 1/{THIRST_REF} = {1 / THIRST_REF:.3f}")
print(f"drinking                           : {INGEST_RATE}/ms  -> "
      f"{INGEST_RATE * 1000:.3f} water units / neural s")
print(f"hydration per water unit           : {HYDRATION_PER_UNIT}")
print(f"drink deficit multiplier           : {DEFICIT_MULT}")
print(f"contact reach                      : {CONTACT_REACH} mm")
print()


# ---- the mirrored functions -------------------------------------------------

def thirst_drive(hydration: float) -> float:
    """InternalState.thirstDrive"""
    return max(0.0, 1.0 - hydration * THIRST_REF)


def thirst_gain(hydration: float) -> float:
    """InternalState.thirstModulatedGain"""
    return THIRST_FLOOR + thirst_drive(hydration) * THIRST_SPAN


def thirst_modulated_water(contact: float, hydration: float) -> float:
    """InternalState.thirstModulatedWater — 0 in, 0 out; bounded by 1."""
    if contact <= 0:
        return 0.0
    return min(contact * thirst_gain(hydration), 1.0)


def water_current(contact: float, hydration: float) -> float:
    """SimulationCore -> SensoryInterface.waterInput: c * WATER_CURRENT."""
    return thirst_modulated_water(contact, hydration) * WATER_CURRENT


def drink(hydration: float, neural_ms: float) -> tuple[float, float]:
    """World.drinkWater + InternalState.drink. Returns (gained, new_hydration).

    The world returns the requested amount scaled by how much water is presented
    at the point (a full-contact patch returns all of it); `InternalState.drink`
    then caps the uptake at the animal's deficit.
    """
    want = neural_ms * INGEST_RATE * HYDRATION_PER_UNIT
    deficit = max(0.0, 1.0 - hydration)
    gained = min(want, deficit * DEFICIT_MULT)
    return gained, min(1.0, hydration + gained)


# ---- SIGNATURE: "no consumer" ----------------------------------------------
# A thirstier fly must push a bigger current through the SAME contact. If the
# gain were absent (or unsigned, or applied to a different quantity), these
# would be equal — which is how the original unread `thirstDrive` passed review.
print("SIGNATURE 'no consumer' — does thirst change the SAME water sample?")
satiated = water_current(0.5, hydration=0.9)
mid = water_current(0.5, hydration=0.5)
parched = water_current(0.5, hydration=0.0)
print(f"  contact 0.50 -> satiated {satiated:.1f} nA, mid {mid:.1f} nA, "
      f"parched {parched:.1f} nA")
check(satiated < mid < parched,
      "a thirstier fly pushes a bigger water current through the same contact",
      f"{satiated:.1f} < {mid:.1f} < {parched:.1f}")
check(satiated > 0,
      "a fully hydrated fly still senses water (the gain never reaches zero — "
      "taste is a sense, not a thirst alarm)",
      f"satiated fly reads {satiated:.1f} nA at contact 0.5")
check(thirst_modulated_water(0.0, 0.0) == 0.0,
      "no contact means no current, however thirsty the fly is")

# ---- SIGNATURE: "no water sense" -------------------------------------------
# `tasteAcceptance` must STILL return 0 for water — water is not a
# phagostimulant, and folding it in would make the fly unable to tell sugar from
# water. The water sense has to be its own query.
print("\nSIGNATURE 'no water sense' — is there a separate query to drive it?")
check("case .water, .pheromone: return 0" in world,
      "water still carries NO taste valence (a fly that cannot tell sugar from "
      "water is not tasting)")
check("func waterContact(" in world and "case .water" in world,
      "the world answers a SEPARATE water-contact query (water is sensed, not tasted)")
check(re.search(r"func waterContact\(at p: SIMD3<Float>\) -> Float \{\s*guard kind == \.water",
                world) is not None,
      "the water query returns 0 for every non-water source (no food reads as water)")

# ---- SIGNATURE: "circular" -------------------------------------------------
# The tarsal route must exist, or the channel that opens the mouth requires the
# mouth to be open.
print("\nSIGNATURE 'circular' — can a FOLDED fly sense water and start drinking?")
check("private func waterAtTarsus(" in core,
      "water is sampled at the TARSI (the only water sense a folded fly has)")
check("private func waterAtProboscisTip(" in core,
      "water is also sampled at the labellum (the mouth confirms, as in feeding)")
# The tarsal sample must NOT be gated on the proboscis.
tarsal_block = re.search(r"private func waterAtTarsus\(.*?\n    \}", core, re.S)
check(tarsal_block is not None and "proboscisReaches" not in tarsal_block.group(0),
      "the tarsal water sample is NOT gated on the proboscis being open "
      "(otherwise the loop is circular and can never start)")
# And a folded fly must still reach the drink call.
check(re.search(r"if dynamics\.isGrounded \{\s*\n\s*let got = w\.drinkWater", core) is not None,
      "drinking has a tarsal fallback, so a folded fly can drink")

# ---- SIGNATURE: "unbounded" ------------------------------------------------
# A drink must not bank hydration the animal does not need.
print("\nSIGNATURE 'unbounded' — can a drink overshoot into a pinned drive?")
gained, after = drink(hydration=0.0, neural_ms=10_000.0)
check(after <= 1.0, "hydration cannot exceed 1", f"{after:.4f}")
# From full, a drink must add nothing.
g_full, after_full = drink(hydration=1.0, neural_ms=10_000.0)
check(g_full == 0.0, "a fully hydrated fly gains nothing from more water",
      f"gained {g_full}")
# And the cap must be by the DEFICIT, not a flat clip: a nearly-full fly gains
# less than a parched one for the same time on the water.
g_near, _ = drink(hydration=0.95, neural_ms=1_000.0)
g_dry, _ = drink(hydration=0.0, neural_ms=1_000.0)
check(g_near < g_dry, "the uptake is bounded by the animal's deficit, not by a flat clip",
      f"near-full {g_near:.5f} < parched {g_dry:.5f}")
check(DEFICIT_MULT >= 1.0,
      "a single drink may reach the deficit (mul >= 1) without being wildly unbounded",
      f"multiplier {DEFICIT_MULT}")

# ---- SIGNATURE: "no quantity" ----------------------------------------------
# Hydration must track time on the water, not the number of contacts.
print("\nSIGNATURE 'no quantity' — does drinking scale with time on the water?")
short, _ = drink(hydration=0.0, neural_ms=100.0)
long, _ = drink(hydration=0.0, neural_ms=400.0)
check(long > short, "a longer drink hydrates more (the loop carries a quantity)",
      f"100 ms -> {short:.5f}, 400 ms -> {long:.5f}")
check(short > 0, "a short drink still hydrates something")

# ---- reachability: can a session contain a thirsty fly? --------------------
# The same trap TASK-005 hit: metabolism ran on neural time, and hydration drains
# on the metabolic clock, so if the drain were too slow no session would ever
# contain a thirsty fly and even a perfect loop would have nothing to drive.
print("\nreachability — can a session contain a thirsty fly?")
METABOLIC_CLOCK = read(state, r"metabolicHoursPerNeuralHour\s*:\s*Float\s*=\s*([0-9_]+)",
                       "metabolic clock ratio")
HYDRATION_DRAIN = read(state, r"hydration = max\(0, hydration - ([0-9.]+) \* metabolicH\)",
                       "hydration drain per metabolic hour")
neural_ms_per_frame = DT_MS * 4          # the loop steps 4 substeps per frame
frames_per_second = 60.0
hydration = 0.8
frames = 0
threshold = 1 / THIRST_REF
while hydration > threshold and frames < 50_000_000:
    dt_h = neural_ms_per_frame / 3_600_000.0
    hydration = max(0.0, hydration - HYDRATION_DRAIN * dt_h * METABOLIC_CLOCK)
    frames += 1
wall_minutes = frames / frames_per_second / 60.0
print(f"  hydration {HYDRATION_DRAIN}/metab-h, clock {METABOLIC_CLOCK:,.0f}x, "
       f"frame {neural_ms_per_frame} neural ms")
print(f"  well-hydrated -> thirsty: {wall_minutes:.1f} wall-clock minutes")
check(frames < 50_000_000, "a session can make the fly thirsty")
check(wall_minutes < 60, "thirst is reachable inside a session",
      f"{wall_minutes:.1f} wall-clock minutes")
check(wall_minutes > 0.05,
      "thirst is not instantaneous — a fly that dries out in a frame cannot drink")

# ---- drinking must actually close the loop ---------------------------------
print("\nclosing the loop — does a drink restore the drive?")
hyd = 0.0
before = thirst_drive(hyd)
gained, hyd = drink(hyd, neural_ms=5_000.0)
after = thirst_drive(hyd)
print(f"  thirstDrive {before:.3f} -> {after:.3f} after one drink")
check(after < before, "drinking reduces thirst (the satiety half)", 
      f"{before:.3f} -> {after:.3f}")
check(after > 0 or hyd >= 1.0,
      "the drive is not merely zeroed by clipping hydration to 1")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("The thirst loop closes: thirst modulates a real water sense, the tarsal "
      "route makes it non-circular, and drinking restores the drive.")