#!/usr/bin/env python3
"""Gate for the looming producer (spec #11, #23).

There is no Swift toolchain on this device, so this mirrors `computeLooming`
in `VisionSystem.swift` and asserts the PROPERTIES that make the signal a
loom rather than a brightness threshold. The model constants are read out of
the Swift source, so changing the model moves this gate instead of silently
invalidating it.

WHY THIS EXISTS. Before this work `computeLooming()` returned `nil`
unconditionally, with a comment saying the real module was "Phase 3b", while
the file header advertised "looming (expansion -> threat drive)", PLAN.md
phase 3 listed "vision (compound eye ON/OFF/looming)" as done, and
docs/VALIDATION.md scenario 04 expected "sudden looming -> escape". Three
documents claimed a capability no code produced. Worse, the eye sampled a
single luminance point at a FIXED range, so a body on a collision course was
invisible: the ray stepped over it and read the sky behind it, and a body
filling the whole visual field reported exactly the same value as empty sky.
The uncommitted half-scaffold declared `rayProvider`/`coverage` and referenced
a type `RayHit` that existed nowhere in the repo, so it could not compile.

WHAT IS MEASURED HERE (the mirror), vs WHAT IS ONLY PINNED:
  * The state machine below reproduces `computeLooming`'s arithmetic, so the
    behavioural signatures are MEASURED on this device.
  * The last section pins the real Swift source by regex (guard conditions,
    the ray/provider wiring in SimulationCore). That is NOT execution —
    the semantic gate is `VisionSystemTests` on the macOS runner, which is
    the only thing that runs the Swift.

The signatures asserted, each of which fails a naive implementation:
  1. a STATIC body covers a constant fraction -> must NOT loom
     (kills a plain "coverage > threshold" detector: it would fire forever);
  2. an APPROACHING body -> must loom, and the drive must grow with the
     closing speed;
  3. a RECEDING body -> must NOT loom (expansion is negative);
  4. a speck growing fast but below `loomMinCoverage` -> must NOT loom;
  5. the first sample -> must NOT loom (no history, no rate).

Run: python3 tools/probe_looming_producer.py
"""
from __future__ import annotations

import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore")
VISION = os.path.join(CORE, "VisionSystem.swift")
SIMCORE = os.path.join(CORE, "SimulationCore.swift")
WORLD = os.path.join(CORE, "World.swift")

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def swift_float(src: str, name: str) -> float:
    """Read `<name>: Float = <value>` out of the Swift source."""
    m = re.search(rf"\b{name}\s*:\s*Float\s*=\s*([0-9.]+)", src)
    if not m:
        raise SystemExit(f"could not read {name} from {VISION}: the gate would "
                         f"be asserting a model the source no longer has")
    return float(m.group(1))


# --- model constants, read from the Swift source ---------------------------
V = read(VISION)
LOOM_EXPANSION_REFERENCE = swift_float(V, "loomExpansionReference")
LOOM_MIN_COVERAGE = swift_float(V, "loomMinCoverage")
LOOM_BASELINE_TAU_MS = swift_float(V, "loomBaselineTauMs")

# The GUARD STRUCTURE is read out too, not hardcoded. If the source drops the
# coverage floor, the mirror must drop it as well — otherwise the mirror would
# keep testing a detector the source no longer has, and only the pinned regex
# would notice. `gate_on_min_coverage` is what makes the speck check below
# load-bearing instead of decorative.
GATE_ON_MIN_COVERAGE = re.search(
    r"guard\s+signal\s*>=\s*loomMinCoverage", V) is not None

print(f"model read from VisionSystem.swift: expansionRef={LOOM_EXPANSION_REFERENCE} "
      f"minCoverage={LOOM_MIN_COVERAGE} baselineTauMs={LOOM_BASELINE_TAU_MS} "
      f"coverageFloorInGuard={GATE_ON_MIN_COVERAGE}")


class Loom:
    """Mirror of computeLooming's state machine."""

    def __init__(self) -> None:
        self.previous = 0.0
        self.baseline = 0.0
        self.initialised = False
        self.coverage = 0.0

    def sample(self, covered: float, dt_ms: float) -> float | None:
        if not self.initialised:
            self.initialised = True
            self.baseline = covered
            self.previous = covered
            self.coverage = covered
            return None
        self.coverage = covered
        beta = min(1.0, dt_ms / max(LOOM_BASELINE_TAU_MS, 1))
        self.baseline += (covered - self.baseline) * beta
        dt_sec = max(dt_ms / 1000.0, 0.0001)
        expansion = (covered - self.previous) / dt_sec
        self.previous = covered
        signal = max(covered - self.baseline, 0.0)
        if expansion <= 0:
            return None
        if GATE_ON_MIN_COVERAGE and signal < LOOM_MIN_COVERAGE:
            return None
        drive = min(expansion / max(LOOM_EXPANSION_REFERENCE, 0.0001), 1.0)
        return drive if drive > 0 else None


DT_MS = 0.1  # SimulationParameters.dt is in ms (0.1 ms per neural step)


def run(coverages: list[float]) -> float | None:
    """Feed a coverage trajectory through the mirror; return the peak drive."""
    lm = Loom()
    peak = None
    for c in coverages:
        d = lm.sample(c, DT_MS)
        if d is not None:
            peak = d if peak is None else max(peak, d)
    return peak


# --- 1. static body: constant coverage must not loom ------------------------
static = run([0.5] * 4000)
check(static is None,
      "a static body does not loom",
      f"constant 0.5 coverage over 4000 steps -> "
      f"{'no drive' if static is None else f'drive {static:.4f}'}")

# --- 2. approaching body: coverage grows -> loom ----------------------------
# A body closing on the eye covers a fraction that grows with proximity. Model
# it geometrically: covered fraction ~ solid angle of a sphere, which grows
# without bound as distance -> radius.
def coverage_of_sphere(distance: float, radius: float) -> float:
    """Fraction of the eye covered by a sphere, from the angular subtense."""
    if distance <= radius:
        return 1.0
    half_angle = math.asin(min(radius / distance, 1.0))
    # Fraction of the hemisphere: solid angle / 2*pi
    return min((1 - math.cos(half_angle)), 1.0)


# closing from 6 to ~1.2 body-radii at a constant speed
traj = [coverage_of_sphere(6.0 - 0.02 * i, 1.0) for i in range(240)]
approach = run(traj)
fast = run([coverage_of_sphere(6.0 - 0.04 * i, 1.0) for i in range(120)])
check(approach is not None and approach > 0,
      "an approaching body looms",
      f"peak drive {approach if approach is None else round(approach, 4)} "
      f"over {len(traj)} steps (final coverage {traj[-1]:.3f})")
check(approach is not None and fast is not None and fast >= approach,
      "a faster approach looms at least as hard",
      f"closing 2x faster -> {fast if fast is None else round(fast, 4)} "
      f"vs {approach if approach is None else round(approach, 4)}")

# --- 3. receding body: coverage shrinks -> no loom --------------------------
recede = run([coverage_of_sphere(1.2 + 0.02 * i, 1.0) for i in range(240)])
check(recede is None,
      "a receding body does not loom",
      f"coverage shrinking 1.2 -> 6.0 -> "
      f"{'no drive' if recede is None else f'drive {recede:.4f}'}")

# --- 4. a speck below the minimum coverage --------------------------------
# A speck is small: it must stay BELOW `loomMinCoverage` for the whole run
# (or it is not a speck — the first draft of this check grew 0.001 -> 0.33 and
# was testing nothing). It still expands fast, so expansion alone would fire:
# linear growth to just under the minimum at ~2.4/s.
speck_traj = [0.001 + (0.001 * 0.9) * i for i in range(1, 28)]  # 0.0019 -> 0.0253
assert max(speck_traj) < LOOM_MIN_COVERAGE, "fixture is not a speck"
speck = run(speck_traj)
check(speck is None,
      "a fast-expanding speck below loomMinCoverage does not loom",
      f"coverage {speck_traj[0]:.4f} -> {speck_traj[-1]:.4f} (below "
      f"{LOOM_MIN_COVERAGE}), expansion ~2.4/s -> "
      f"{'no drive' if speck is None else f'drive {speck:.4f}'}")

# --- 5. the first sample has no history ------------------------------------
lm = Loom()
first = lm.sample(0.5, DT_MS)
check(first is None, "the first sample cannot loom",
      "one sample is not a rate, so the first call returns nothing")

# --- 6. the gate can actually fail -----------------------------------------
# A plain "coverage above a threshold" detector, which is what the OLD stub
# did (it counted bright ommatidia and returned nil anyway). If the signatures
# above would also be satisfied by that, this gate proves nothing. Assert that
# the naive detector DOES sail through the static control, i.e. the controls
# discriminate.
def naive_coverage_threshold(trajectory: list[float]) -> bool:
    return max(trajectory) > 0.05


check(naive_coverage_threshold([0.5] * 4000),
      "the static control discriminates (a threshold detector would fire on it)",
      "so 'a static body does not loom' is a real assertion, not a tautology")

# --- pinned Swift source (NOT execution) -----------------------------------
# These regexes pin the wiring the behaviour above depends on. They report
# whether the source still has it; they do not run it.
def has(src: str, pattern: str) -> bool:
    return re.search(pattern, src, re.S) is not None


C = read(SIMCORE)
W = read(WORLD)

check(has(V, r"guard\s+signal\s*>=\s*loomMinCoverage\s*,\s*expansion\s*>\s*0\s+else\s*\{\s*return\s+nil"),
      "computeLooming requires real coverage AND positive expansion",
      "pinned in VisionSystem.swift (semantic gate: VisionSystemTests)")
check(has(V, r"return\s+VisualEvent\(sourceOmmatidium:\s*0,\s*pathway:\s*\.looming"),
      "computeLooming emits a .looming event",
      "pinned: the detector's only output is the loom channel")
check(has(V, r"if\s+hit\.distance\s*<\s*range\s*\{") and
      has(V, r"if\s+hit\.isMovingBody\s*\{\s*covered\s*\+=\s*1\s*\}"),
      "the eye counts coverage only from a MOVING body hit",
      "pinned: a static obstacle occludes but cannot loom")
check(has(C, r"self\.vision\.rayProvider\s*="),
      "SimulationCore wires the ray caster",
      "pinned: without it the eye never sees a nearer body")
check(has(C, r"scene\?\.step\(dtSeconds:\s*dt\s*/\s*1000\)"),
      "the world is stepped BEFORE the eye samples",
      "pinned: sampling first would compare two samples of one instant")
check(has(C, r"if\s+ev\.pathway\s*==\s*\.looming\s*\{[\s\S]{0,120}loomingInput"),
      "the loom routes through the dedicated escape channel",
      "pinned: 80 nA into the plate, not the generic 1/8-strength edge")
check(has(W, r"public\s+func\s+raycast\(origin:\s*SIMD3<Float>,\s*direction:\s*SIMD3<Float>\)"),
      "the world can answer what a ray hit",
      "pinned: ray/sphere for moving bodies, AABB for obstacles")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    print("NOTE: the behavioural checks are a Python mirror; the semantic gate")
    print("for the Swift is VisionSystemTests on the macOS CI runner.")
    raise SystemExit(1)
print("all checks passed — the producer's signatures hold in the mirror, and")
print("the Swift wiring they depend on is still present in the source.")
print("This does NOT execute Swift: VisionSystemTests is the semantic gate.")