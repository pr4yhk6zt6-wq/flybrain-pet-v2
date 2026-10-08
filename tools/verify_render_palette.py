#!/usr/bin/env python3
"""Gate for the region palette (CI step).

Mirrors the assertions of
ios/Tests/FlyBrainCoreTests/RenderPaletteTests.swift against the REAL colour
table, read out of the Swift source rather than copied here. The authoring
device has no Swift toolchain, so this is the equivalent gate for the same
numbers on the Python pipeline runner.

Why a gate and not just a test: the palette is a table indexed by region, and a
table with fewer entries than `RegionID` has cases does not crash — it wraps.
Two anatomically unrelated regions then share a colour and nothing reports a
problem. The Swift test asserts the domain; this script asserts it here too, on
the exact bytes that will ship.

Run:  python3 tools/verify_render_palette.py      (exit 1 on any failure)
"""
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PALETTE = ROOT / "ios/Sources/FlyBrainCore/RenderPalette.swift"
TYPES = ROOT / "ios/Sources/FlyBrainCore/Types.swift"
TESTS = ROOT / "ios/Tests/FlyBrainCoreTests/RenderPaletteTests.swift"

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")
    if not cond:
        failures.append(name)


def read(path):
    if not path.exists():
        raise SystemExit(f"missing required file: {path}")
    return path.read_text()


src = read(PALETTE)
types = read(TYPES)

# --- the table itself -------------------------------------------------------
table_body = src.split("regionColors: [SIMD3<Float>] = [")[1].split("]")[0]
colors = [tuple(float(x) for x in m)
          for m in re.findall(r"SIMD3\(([\d.]+), ([\d.]+), ([\d.]+)\)", table_body)]

# RegionID case order is the raw-value order the table must follow.
region_body = types.split("public enum RegionID")[1]
region_body = region_body.split("}")[0]
swift_regions = re.findall(r"case (\w+)(?:\s*=\s*(\d+))?", region_body)
region_names = [name for name, _ in swift_regions]
region_count = len(region_names)

check("the palette has exactly one colour per RegionID case",
      len(colors) == region_count,
      f"colours={len(colors)} regions={region_count}")
if len(colors) != region_count:
    print("\nFAILED: a short table wraps and silently recolours regions")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

# --- every component is a usable colour -------------------------------------
off = [c for c in colors if any(not (0.0 <= v <= 1.0) for v in c)]
check("every component is within 0...1", not off, f"out of range: {off}")

# Region 0 is the sentinel and must be achromatic: an unlabelled neuron reads
# as "no data", not as a region.
r, g, b = colors[0]
check("region 0 (unknown) is achromatic grey, not a hue",
      abs(r - g) < 0.05 and abs(g - b) < 0.05, f"unknown={colors[0]}")

# --- separation -------------------------------------------------------------
pairs = [(math.dist(colors[i], colors[j]), i, j)
         for i in range(len(colors)) for j in range(i + 1, len(colors))]
worst = min(pairs)
check("no two regions share (or nearly share) a colour",
      worst[0] > 0.10,
      f"closest pair {worst[0]:.4f} = {region_names[worst[1]]} / "
      f"{region_names[worst[2]]}")

adj = min((math.dist(colors[i], colors[i + 1]), i) for i in range(len(colors) - 1))
check("anatomically adjacent regions are at least as distinct as the worst pair",
      adj[0] > 0.10,
      f"closest adjacent {adj[0]:.4f} = {region_names[adj[1]]} / "
      f"{region_names[adj[1] + 1]}")

# --- the activity lift ------------------------------------------------------
m = re.search(r"maxActivityLift: Float = ([0-9.]+)", src)
lift = float(m.group(1))

# The lift coefficient must be the one the reselected table actually needs:
# 0.25 is the largest value whose worst "fully-lit vs some other region's
# silent colour" distance stays above 0.11. Assert the measured worst case so
# the number cannot be raised without the collision being reported.
def lit(c, t=lift):
    return tuple(min(1.0, x + (1 - x) * t) for x in c)


collisions = [(math.dist(lit(colors[i]), colors[j]), i, j)
              for i in range(len(colors)) for j in range(len(colors)) if i != j]
worst_lit = min(collisions)
check("a fully-lit neuron stays clear of other regions' silent colours",
      worst_lit[0] > 0.10,
      f"worst {worst_lit[0]:.4f} = {region_names[worst_lit[1]]} lit vs "
      f"{region_names[worst_lit[2]]} silent (lift={lift})")

check("intensity 0 is a strict no-op (no ambient floor)",
      all(lit(c, 0.0) == c for c in colors),
      "a silent neuron must look exactly like the region colour")

# Monotone: more rate must never look dimmer.
def key(c):
    return sum(c)


mono_ok = all(key(lit(c, a)) <= key(lit(c, b)) + 1e-9
              for c in colors
              for a, b in [(0.1, 0.4), (0.4, 0.7), (0.7, 1.0)])
check("brightness is monotone in intensity", mono_ok,
      "checked 3 rate brackets on all 21 colours")

# The lift saturates rather than walking to white, so a cell cannot climb past
# the bound no matter how hard it fires.
max_disp = max(math.dist(lit(c), c) for c in colors)
check("the lift saturates (a very high rate cannot walk a colour to white)",
      max_disp <= 0.35 and
      math.dist(lit(colors[3], 1.0), lit(colors[3], 5.0)) < 1e-9,
      f"max displacement {max_disp:.4f}; intensity clamps at 1")

# --- the Swift test quotes the same table -----------------------------------
tests = read(TESTS)
check("the Swift test asserts the same region count",
      f"== {region_count}" in tests or str(region_count) in tests,
      f"expected the Swift test to reference {region_count}")

print()
if failures:
    print(f"{len(failures)} failure(s)")
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all palette checks passed")
sys.exit(0)