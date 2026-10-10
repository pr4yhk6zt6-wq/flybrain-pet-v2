#!/usr/bin/env python3
"""Where did the 1384 "spikes at rest" come from? (the eye's phantom OFF flash)

WHY THIS GATE EXISTS
--------------------
`testTheConnectomeIsSilentWithNoStimulus` sets `acceptance = 0` (no taste) and
asserts the connectome is SILENT over 600 steps. It measured **1384 spikes**.
"Ignore the number, the taste channel is off" would be the wrong reading: a test
that says "no stimulus" while a channel is still driven does not test what it
claims, and the drive it was missing was real.

The drive was the EYE, not the taste sense and not instability.

`VisionSystem` seeded both adaptation baselines at a hardcoded **0.5** and then
measured the first sample against them. The world in that test is dark
(`TasteWorld.luminance` returns 0), so every ommatidium compared its first real
sample (luminance 0) against a baseline of 0.5 and reported a FULL-STRENGTH
OFF edge — a flash that exists only because the eye believed a scene it had
never looked at. The transient reaches the lamina through `mapToInput` and is
worth thousands of spikes.

THE FIX, AND WHAT THIS GATE CHECKS
----------------------------------
The baselines are now seeded FROM the first sample instead of from a constant
(`VisionSystem.adaptationPrimed`), so a fresh eye's first frame carries no edge.
This gate checks the fix in three directions rather than one:

  1. CONTROL THAT THE BUG IS REAL. The pre-fix rule (seed 0.5, measure the
     first sample against it) must still produce the OFF flash. Without this
     control the "no spikes now" result below could be silence for any reason.
  2. THE FIX. With the shipped rule, a dark, motionless world must produce no
     visual events on any frame and no spikes at all.
  3. THE EYE IS NOT DEAF. A world that actually changes must still produce
     ON/OFF edges — a fix that silenced the eye by breaking it would pass (2).

HOW THIS IS EVIDENCED
---------------------
The luminance/adaptation/mapping arithmetic and the ommatidial grid are
replicated from `VisionSystem.sample`, `buildRetina` and `mapToInput`. Constants
are READ from the Swift source, so moving one moves this gate. The neuron model
and event queue are the Python mirror in `tools/sim_neural_engine.py` (see
`closed_loop_parity.py`). It does NOT execute Swift: the semantic claim is
`FeedingLoopTests.testTheConnectomeIsSilentWithNoStimulus`, which runs only in
CI on a macOS runner. The [SOURCE] lines below pin the Swift text; a regex is
not execution, and saying which half is measured and which half is pinned is
the point.

Run: python3 tools/probe_rest_activity.py
"""
from __future__ import annotations

import math
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import neuron_at, parse                      # noqa: E402
from sim_neural_engine import Engine, edge_current        # noqa: E402

ASSET = ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"
VISION = ROOT / "ios/Sources/FlyBrainCore/VisionSystem.swift"

REGION_LAMINA = 3
REGION_RETINA_L, REGION_RETINA_R = 1, 2

DT_MS = 0.1

# NeuralEngine model overrides by region (NeuralEngine.init).
MOTOR_REGIONS = (15, 16, 17, 18, 19)
MB_CC_REGIONS = (9, 11)

failures: list[str] = []


def check(ok, label, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def source(label, pattern, what):
    """Read a constant from the Swift source. Refuses to guess."""
    m = re.search(pattern, VISION.read_text())
    if not m:
        print(f"[SOURCE] could not read {what} from VisionSystem.swift — refusing to guess")
        sys.exit(1)
    print(f"[SOURCE] {what} = {m.group(1)}")
    return float(m.group(1).replace("_", ""))


def seeding_rule():
    """What the FIRST frame does to the adaptation baselines, read from Swift.

    Returns "lum" when the first sample becomes the baseline (the shipped rule,
    `adaptedLuminance[i] = lum` under `adaptationPrimed`) and the literal float
    otherwise — so reverting the Swift fix flips this to a number and the whole
    mirror changes behaviour with it. A mirror that hard-codes the rule can only
    fail on a regex, which is the failure mode this gate exists to avoid.
    """
    src = VISION.read_text()
    m = re.search(
        r"let first = !adaptationPrimed\s*"
        r"if first \{\s*"
        r"adaptedLuminance\[i\] = ([^\n]+?)\s*\n"
        r"\s*prevLuminance\[i\] = ([^\n]+?)\s*\n", src)
    if not m:
        print("could not read the first-frame seeding rule from VisionSystem.swift"
              " — refusing to guess")
        sys.exit(1)
    return m.group(1).strip() if m.group(1).strip() == "lum" else float(m.group(1))


def load_asset(path: Path):
    hdr, blocks = parse(str(path))
    n = int(hdr["neuronCount"])
    neurons, syn, rng = blocks["neuron"], blocks["synapse"], blocks["range"]
    regions, sides = [], []
    for i in range(n):
        d = neuron_at(neurons, i)
        regions.append(int(d["region"]))
        sides.append(int(d["side"]))

    def edges(u):
        start, count = struct.unpack_from("<ii", rng, u * 8)
        out = []
        for k in range(start, start + count):
            _p, post, sc, _nt, sign, _c, _d, eff = struct.unpack_from(
                "<iiHBbBB2xf", syn, k * 20)
            cur = edge_current(eff, 1.0, sc, sign)
            if cur is not None:
                out.append((post, cur))
        return out

    return n, regions, sides, edges


def build_engine(n, regions, edges):
    e = Engine(n, regions=regions, syn_out=[edges(u) for u in range(n)])
    for i in range(n):
        r = regions[i]
        if r in MOTOR_REGIONS:
            e.tauM[i] = 5.0
            e.tauRef[i] = 1.0
            e.b[i] = 0.2
            e.a[i] = 0.5
        elif r in MB_CC_REGIONS:
            e.tauM[i] = 15.0
            e.tauAdapt[i] = 150.0
            e.b[i] = 0.5
            e.a[i] = 2.0
    return e


def ommatidia(angular_spacing, fov_x, fov_y):
    """The same grid `VisionSystem.buildRetina` makes (both sides, same order)."""
    n_x = int(fov_x / angular_spacing)
    n_y = int(fov_y / angular_spacing)
    out = []
    for side in (1, 2):
        for iy in range(n_y):
            for ix in range(n_x):
                out.append((side, iy * n_x + ix))
    return out, n_x * n_y


def target_map(regions, sides, oms, per_side, map_gain):
    """`mapToInput` targets for the flat-field case, resolved ONCE.

    This was the probe's hot spot: rebuilding the lamina/retina candidate lists
    for every event made a 600-step run take ~20 minutes, i.e. long enough that
    nobody would run it and it would never have been in CI. The mapping is a
    pure function of (pathway, side, ommatidium index), so it is hoisted.
    """
    lamina = [i for i in range(len(regions)) if regions[i] == REGION_LAMINA]
    retina = {1: [], 2: []}
    for i in range(len(regions)):
        if regions[i] == REGION_RETINA_L:
            retina[1].append(i)
        elif regions[i] == REGION_RETINA_R:
            retina[2].append(i)
    targets = []
    for side, om_index in oms:
        i = om_index % per_side
        cand = [j for j in lamina if sides[j] == side] or lamina
        retina_cand = retina[side] or retina[1] or retina[2]
        targets.append((om_index % len(retina_cand) if retina_cand else -1,
                        cand[om_index % len(cand)] if cand else -1))
    return targets


def run(seed_rule, lum, steps, cfg):
    """Mirror of `VisionSystem.sample` + `mapToInput` for a flat luminance field.

    `seed_rule` is what the FIRST frame does to the adaptation baselines, read
    out of the Swift source by `seeding_rule()` rather than assumed here:
      * the float `0.5`  -> pre-fix: the array starts there and the first sample
                            is MEASURED against it (the phantom OFF flash).
      * the string "lum" -> shipped: the first sample IS the baseline and carries
                            no edge (`VisionSystem.adaptationPrimed`).

    `lum` is a per-step luminance sequence (one value per step, the same value
    for every ommatidium), so a world that changes can be driven too.
    """
    n, regions, sides, edges = cfg["n"], cfg["regions"], cfg["sides"], cfg["edges"]
    oms, per_side = cfg["oms"], cfg["per_side"]
    e = build_engine(n, regions, edges)
    # The array's initial value follows the same rule: a rule that seeds from
    # the sample starts from "nothing seen yet" (0), a rule that measures
    # against a constant must start AT that constant.
    seed_value = 0.0 if seed_rule == "lum" else float(seed_rule)
    adapted = [seed_value] * per_side
    prev = [seed_value] * per_side
    primed = False
    alpha = DT_MS / (DT_MS + cfg["adapt_tau"])
    targets = cfg["targets"]
    first_frame_events = 0
    total_events = 0
    for k in range(steps):
        value = lum[k] if isinstance(lum, (list, tuple)) else lum
        e.time = k * DT_MS
        frame_events = 0
        first = not primed
        for (side, om_index), (_ret_t, lam_t) in zip(oms, targets):
            i = om_index % per_side
            if first:
                adapted[i] = value if seed_rule == "lum" else float(seed_rule)
                prev[i] = value if seed_rule == "lum" else float(seed_rule)
            a = adapted[i] + (value - adapted[i]) * alpha
            adapted[i] = a
            contrast = (value - a) * cfg["contrast_gain"]
            prev[i] = value
            # the edge pathways are suppressed exactly when the baselines were
            # seeded from the sample (a first frame has no "before" to be an
            # edge against)
            if not (first and seed_rule == "lum"):
                if contrast < -cfg["edge_threshold"]:
                    if lam_t >= 0:
                        e.inject(lam_t, (-contrast) * cfg["map_gain"], e.time)
                        frame_events += 1
                elif contrast > cfg["edge_threshold"]:
                    if lam_t >= 0:
                        e.inject(lam_t, contrast * cfg["map_gain"], e.time)
                        frame_events += 1
            if value > 0.02:
                retina_t = cfg["retina_targets"][i]
                if retina_t >= 0:
                    e.inject(retina_t, value * 0.2 * cfg["map_gain"], e.time)
                    frame_events += 1
        primed = True
        if k == 0:
            first_frame_events = frame_events
        total_events += frame_events
        e.step()
    return first_frame_events, total_events, e.cumTotal


def main() -> int:
    if not ASSET.exists():
        print(f"[SKIP] shipped asset not present at {ASSET.relative_to(ROOT)}")
        return 0

    angular_spacing = source("angular spacing",
                             r"angularSpacing\s*:[^=]*=\s*([0-9.]+)\s*\*\s*\.pi",
                             "EyeConfig.angularSpacing (deg)")
    angular_spacing *= math.pi / 180.0
    fov_x = source("fov x", r"fieldOfViewX\s*:[^=]*=\s*([0-9.]+)\s*\*\s*\.pi",
                   "EyeConfig.fieldOfViewX (deg)") * math.pi / 180.0
    fov_y = source("fov y", r"fieldOfViewY\s*:[^=]*=\s*([0-9.]+)\s*\*\s*\.pi",
                   "EyeConfig.fieldOfViewY (deg)") * math.pi / 180.0
    contrast_gain = source("contrast gain", r"contrastGain\s*:\s*Float\s*=\s*([0-9.]+)",
                           "EyeConfig.contrastGain")
    adapt_tau = source("adaptation tau", r"adaptationTau\s*:\s*Float\s*=\s*([0-9.]+)",
                       "EyeConfig.adaptationTau (ms)")
    edge_threshold = source("edge threshold", r"contrast\s*<\s*-([0-9.]+)",
                            "ON/OFF edge threshold")
    map_gain = source("mapToInput gain", r"return\s*\(idx,\s*event\.strength\s*\*\s*([0-9.]+)\)",
                      "mapToInput event gain")

    vision_src = VISION.read_text()
    shipped = seeding_rule()
    check("adaptationPrimed" in vision_src,
          "the retina declares a primed flag instead of trusting a constant seed",
          "VisionSystem.adaptationPrimed")
    check(shipped == "lum",
          "the first sample SEEDS the baselines from the sample itself",
          f"the seeding rule reads from VisionSystem.swift: adaptedLuminance[i] = "
          f"{shipped!r}" + ("" if shipped == "lum" else " (a constant the eye never measured)"))

    n, regions, sides, edges = load_asset(ASSET)
    oms, per_side = ommatidia(angular_spacing, fov_x, fov_y)
    tmap = target_map(regions, sides, oms, per_side, map_gain)
    retina_cells = {1: [], 2: []}
    for i in range(n):
        if regions[i] == REGION_RETINA_L:
            retina_cells[1].append(i)
        elif regions[i] == REGION_RETINA_R:
            retina_cells[2].append(i)
    retina_targets = []
    for side, om_index in oms:
        cand = retina_cells[side] or retina_cells[1] or retina_cells[2]
        retina_targets.append(cand[om_index % len(cand)] if cand else -1)

    cfg = {"n": n, "regions": regions, "sides": sides, "edges": edges,
           "oms": oms, "per_side": per_side, "adapt_tau": adapt_tau,
           "contrast_gain": contrast_gain, "edge_threshold": edge_threshold,
           "map_gain": map_gain, "targets": tmap,
           "retina_targets": retina_targets}

    print(f"\nasset: {n} neurons, retina {len(oms)} ommatidia "
          f"({per_side} per side, from EyeConfig {angular_spacing*180/math.pi:.1f} deg)")

    # --- 1. control: the pre-fix seed at 0.5 still manufactures the flash -----
    #
    # Without this the silence below could have any cause at all. This is the
    # signature of the bug: a dark world + a baseline the eye never measured.
    steps = 120
    c_first, c_events, c_spikes = run(0.5, 0.0, steps, cfg)
    check(c_first > 0,
          "CONTROL (pre-fix seed 0.5): a dark world still emits a first-frame "
          "OFF flash",
          f"{c_first} events on frame 1, every ommatidium reporting an edge")
    check(c_spikes > 0,
          "CONTROL: the phantom flash reaches the lamina and spikes",
          f"{c_events} events, {c_spikes} spikes / {steps} steps")

    # --- 2. with no injected current at all, the network is intrinsically quiet
    e = build_engine(n, regions, edges)
    e.run(steps)
    check(e.cumTotal == 0,
          "with no injected current the network stays silent on its own",
          f"{e.cumTotal} spikes" if e.cumTotal else "0 spikes")

    # --- 3. the fix: a dark, motionless world is silent ----------------------
    f_first, f_events, f_spikes = run(shipped, 0.0, 600, cfg)
    check(f_first == 0,
          "FIXED: the eye's first frame against a dark world emits no edge",
          f"{f_first} events on frame 1 (was {c_first})")
    check(f_spikes == 0,
          "FIXED: `testTheConnectomeIsSilentWithNoStimulus` sees 0 spikes",
          f"{f_events} events, {f_spikes} spikes / 600 steps (was 1384 in CI)")

    # --- 4. the eye is not merely deaf -------------------------------------
    #
    # A fix that silenced the eye by breaking it would pass (3). The world
    # steps dark -> bright -> dark, so a working eye reports both edge
    # polarities; the first frame is still the seeding frame and must be empty.
    seq = [0.0] * 5 + [1.0] * 20 + [0.0] * 20
    s_first, s_events, s_spikes = run(shipped, seq, len(seq), cfg)
    check(s_first == 0, "the seeding frame stays empty even for a lit world",
          f"{s_first} events")
    check(s_events > 0 and s_spikes > 0,
          "a world that CHANGES still drives the eye (the fix is not deafness)",
          f"{s_events} events, {s_spikes} spikes over {len(seq)} steps")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed — the eye's baseline is measured, not assumed;\n"
          "the 1384 spikes were the 0.5 -> 0 OFF transient of a constant the eye\n"
          "had no way to have measured, and that frame is silent now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())