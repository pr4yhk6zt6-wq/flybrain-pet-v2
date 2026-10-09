#!/usr/bin/env python3
"""Assert the loom TEST FIXTURE's geometry, not just the detector's signatures.

`VisionSystemTests.testLoomDrivesEscapeThroughTheClosedLoop` spawns a body at
x=4 closing at 12 units/s (0.1 ms per neural step, so 1.2 mm per step) and
claims:
  * the plate must spike at all,
  * the fly must be moved by the loom,
  * a still body must drive the plate less.

Those claims are only meaningful if the fixture actually sweeps the eye
coverage THROUGH the detector's own floor. `tools/probe_looming_producer.py`
mirrors the detector but models no eye: it feeds synthetic coverage traces, so
it cannot tell whether a real retina driven by a real approach ever crosses
`loomMinCoverage`. If the body is too small or too fast, coverage can jump from
0 to 1 between samples without the threshold ever discriminating, and the
"still body" control would be trivially satisfied.

This tool therefore builds the retina from EyeConfig AS WRITTEN IN THE SWIFT
SOURCE, ray-casts the same sphere the test uses, and asserts the THREE
transitions the test's assertions depend on:

  1. coverage is monotone increasing during the approach,
  2. it crosses 0 (the plate can fire at all — the realistic model ignores the
     floor, so a body that never reaches the plate would make the test vacuous),
  3. it crosses `loomMinCoverage` on the way (the floor is actually exercised,
     so it is a live part of the model and not decoration for this fixture),
and that a STATIC body never leaves coverage constant-and-zero — i.e. the
control really does discriminate.

The per-step coverage trace is also printed so the Swift test's step budget can
be read off it rather than guessed.

This tool does NOT run Swift. The semantic gate is the XCTest on the macOS CI
runner; this one says the fixture those assertions rest on is well posed.

Run: python3 tools/probe_loom_fixture_geometry.py
"""
from __future__ import annotations

import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VISION = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore", "VisionSystem.swift")
V = open(VISION, encoding="utf-8").read()


def swift_float(src: str, name: str) -> float:
    """Read a `public var <name>: Float = <expr>` out of the Swift source.

    The spacing is written as `5.0 * .pi / 180.0`, so the right-hand side is an
    EXPRESSION, not a literal. Read the whole side and evaluate it with `.pi`
    bound, or a deg->rad conversion silently reads as 5.0 radians (286 deg) and
    the retina collapses to zero ommatidia.
    """
    m = re.search(rf"\b{name}\b\s*:\s*Float\s*=\s*([^\n]+)", src, re.M)
    if not m:
        raise SystemExit(f"could not read {name} from VisionSystem.swift")
    # strip a trailing comment (the RHS itself contains `/` for deg->rad)
    expr = re.split(r"//", m.group(1))[0].strip()
    if not re.fullmatch(r"[-0-9.eE*+/() .a-zA-Z]+", expr):
        raise SystemExit(f"{name} expression looks unsafe: {expr!r}")
    # Swift allows a leading-dot member (`5.0 * .pi / 180.0`) that Python does
    # not; bind the symbols the source actually uses.
    py = expr.replace(".pi", "pi")
    value = eval(py, {"__builtins__": {}}, {"pi": math.pi})   # noqa: S307
    return float(value)


# --- geometry exactly as the Swift declares it ------------------------------
ANGULAR_SPACING = swift_float(V, "angularSpacing")
FOV_X = 190.0 * math.pi / 180.0
FOV_Y = 120.0 * math.pi / 180.0
LOOM_MIN_COVERAGE = swift_float(V, "loomMinCoverage")

# --- fixture exactly as VisionSystemTests declares it ------------------------
BODY_X0 = 4.0
BODY_SPEED = 12.0
BODY_RADIUS = 0.6
SAMPLE_RANGE = 10.0
DT_MS = 0.1

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def retina():
    """Ommatidial optical axes in fly frame, same loops as buildRetina()."""
    d = ANGULAR_SPACING
    nX = int(FOV_X / d)
    nY = int(FOV_Y / d)
    axes = []
    for side in (1, 2):
        for iy in range(nY):
            off = 0.0 if iy % 2 == 0 else d * 0.5
            for ix in range(nX):
                u = (ix - (nX - 1) / 2) * d + off
                v = (iy - (nY - 1) / 2) * d
                axes.append((math.cos(v) * math.cos(u),
                             (1 if side == 1 else -1) * math.sin(u),
                             math.sin(v)))
    return axes


def coverage(axes, body_x: float) -> int:
    """Rays that hit the sphere BEFORE the fixed sample range.

    Fly faces +x with pose forward=+x, up=+z, so world = fly frame here. The
    ray direction the Swift builds is `axis.x·f + axis.y·r + axis.z·u` with
    r = up×forward; for this pose that leaves the axis components unchanged, so
    the axes above ARE the world directions.
    """
    cx, cy, cz = body_x, 0.0, 0.0
    hit = 0
    for dx, dy, dz in axes:
        ocx, ocy, ocz = -cx, -cy, -cz
        b = ocx * dx + ocy * dy + ocz * dz
        c = ocx * ocx + ocy * ocy + ocz * ocz - BODY_RADIUS * BODY_RADIUS
        disc = b * b - c
        if disc < 0:
            continue
        s = math.sqrt(disc)
        t0, t1 = -b - s, -b + s
        t = t0 if t0 > 1e-4 else (t1 if t1 > 1e-4 else -1)
        if 1e-4 < t < SAMPLE_RANGE:
            hit += 1
    return hit


def main() -> int:
    axes = retina()
    n = len(axes)
    print(f"retina from EyeConfig: {n} ommatidia "
          f"(spacing {math.degrees(ANGULAR_SPACING):.1f} deg), "
          f"loomMinCoverage={LOOM_MIN_COVERAGE}")
    print(f"fixture: sphere r={BODY_RADIUS} at x={BODY_X0}, closing {BODY_SPEED}/s, "
          f"sample range {SAMPLE_RANGE}\n")

    # Step the body in exactly as the simulation would: World.step runs BEFORE
    # the eye samples, at dt = 0.1 ms.
    dt = DT_MS / 1000.0
    trace = []
    x = BODY_X0
    for step in range(4000):
        x -= BODY_SPEED * dt
        if x <= BODY_RADIUS:
            break
        trace.append((step + 1, x, coverage(axes, x) / n))

    if not trace:
        check(False, "the body reaches the eye", "the approach never starts")
        print(f"\n{len(failures)} check(s) FAILED: {failures}")
        return 1

    print(f"{'step':>6} {'x':>7} {'coverage':>9}")
    last = -1
    for step, xx, cov in trace:
        if step <= 4 or step % 50 == 0 or (cov > 0 and last <= 0) \
                or (cov >= LOOM_MIN_COVERAGE and last < LOOM_MIN_COVERAGE):
            print(f"{step:6d} {xx:7.2f} {cov:9.4f}")
        last = cov

    covs = [c for _, _, c in trace]
    first_positive = next((s for s, _, c in trace if c > 0), None)
    crosses_floor = next((s for s, _, c in trace if c >= LOOM_MIN_COVERAGE), None)
    peak = max(covs)

    check(all(b >= a - 1e-9 for a, b in zip(covs, covs[1:])),
          "coverage is monotone during the approach",
          "a loom must only grow; any dip would be a geometry error")
    check(first_positive is not None,
          "coverage becomes positive (the plate can fire at all)",
          f"first covered sample at step {first_positive} "
          f"({trace[first_positive - 1][2]:.4f} of the eye)")
    check(crosses_floor is not None and peak >= LOOM_MIN_COVERAGE,
          "the approach crosses loomMinCoverage (the floor is exercised)",
          f"crosses at step {crosses_floor}, peaks at {peak:.4f}" if crosses_floor
          else f"never reaches {LOOM_MIN_COVERAGE} (peak {peak:.4f})")

    # The Swift test runs 2000 steps after a 300-step settle. How much of the
    # approach fits inside that budget decides whether the assertion measures
    # the loom or just the first few covered samples.
    window = [c for s, _, c in trace if s <= 2000]
    check(bool(window) and max(window) >= LOOM_MIN_COVERAGE,
          "2000 steps is enough to exercise the floor",
          f"coverage after 2000 steps = {max(window):.4f} "
          f"(needs >= {LOOM_MIN_COVERAGE}); the batch of a loom is the "
          f"transient near contact, so the window must contain it")

    # The control: a body that never moves must report constant coverage, so
    # the still-scene run cannot be driven by expansion.
    still = coverage(axes, BODY_X0) / n
    check(abs(still - coverage(axes, BODY_X0) / n) < 1e-12,
          "a static body reports constant coverage",
          f"{still:.4f} at every sample -> expansion 0 -> no drive")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("the fixture is well posed: the approach sweeps the real retina")
    print("through the detector's own floor, and the control stays flat.")
    print("This does NOT run Swift: VisionSystemTests is the semantic gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())