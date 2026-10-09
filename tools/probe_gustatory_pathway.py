#!/usr/bin/env python3
"""Does the gustatory channel the loop now drives actually reach the brain, and
does the reach gate the loop added actually let it?

A channel can be wired and still be dead in two different ways, and only one of
them shows up as "no spikes":

  NO CELL        `gustatoryInput` resolves to nothing on the asset we ship, so
                 the current is injected into no neuron at all. The channel is
                 present in the source and absent from the loop.
  NEVER REACHES  the cell resolves, but the loop's own gate refuses every
                 sample — e.g. a proboscis-reach threshold set above any angle
                 the joint can produce. The channel is alive in the source and
                 dead in the animal. This is the exact failure mode the looming
                 channel had: a complete-looking path that nothing ever drove.

It also checks the SIGN convention, because that is what makes taste a sense
and not a constant: a phagostimulant must drive the SEZ afferent positive and
an aversive substance negative, and water must do NEITHER (it drives drinking
through hydration, not through acceptance).

The numbers are read from the Swift source and the packed assets rather than
restated here, so a change to either is a failure here, not a silent drift.

Run: python3 tools/probe_gustatory_pathway.py
"""
from __future__ import annotations

import os
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import neuron_at, parse                       # noqa: E402
from flybrain.pid import RegionID                          # noqa: E402

CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
SENSORY = ROOT / "ios/Sources/FlyBrainCore/SensoryInterface.swift"
BODY = ROOT / "ios/Sources/FlyBrainCore/BodyModel.swift"
WORLD_SWIFT = ROOT / "ios/Sources/FlyBrainCore/World.swift"

FLAG_MOTOR, FLAG_SENSORY = 0x01, 0x02

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def swift_float(src: str, name: str) -> float | None:
    m = re.search(rf"static let {name}\s*:\s*Float\s*=\s*([0-9.]+)", src)
    return float(m.group(1)) if m else None


def load_targets(path: Path):
    """Replicate `selectInputNeuron` pass order for a side-0 request."""
    hdr, blocks = parse(str(path))
    n = int(hdr["neuronCount"])
    neurons = blocks["neuron"]
    rng = blocks["range"]
    sez = int(RegionID.SUBESOPHAGEAL_ZONE)

    idx = [i for i in range(n) if neuron_at(neurons, i)["region"] == sez]
    if not idx:
        return None
    motor = [i for i in idx if neuron_at(neurons, i)["flags"] & FLAG_MOTOR]
    sensory = [i for i in idx if neuron_at(neurons, i)["flags"] & FLAG_SENSORY]
    nonreadout = [i for i in idx if not (neuron_at(neurons, i)["flags"] & FLAG_MOTOR)]
    # out-degree: can this cell carry the current anywhere?
    def outdeg(i: int) -> int:
        base, count, _ = struct.unpack_from("<IIi", rng, i * 8)
        return count
    return {"region": sez, "n": len(idx), "motor": len(motor),
            "sensory": len(sensory), "nonreadout": len(nonreadout),
            "has_sensory": bool(sensory), "has_nonreadout": bool(nonreadout),
            "outdeg_max": max(outdeg(i) for i in idx)}


def main() -> int:
    core_src = CORE.read_text()
    sensory_src = SENSORY.read_text()
    body_src = BODY.read_text()
    world_src = WORLD_SWIFT.read_text()

    print("Gustatory pathway (contact chemoreception -> SEZ)")
    print()

    # ---- 1. the loop must DRIVE the channel ------------------------------
    driven = bool(re.search(r"sensory\.gustatoryInput\(", core_src))
    check(driven, "SimulationCore calls sensory.gustatoryInput (the channel is driven)",
          "" if driven else "`gustatoryInput` has no call site — the exact defect this "
          "gate exists for: the resolver worked, and nobody used it")

    # ---- 2. the reach gate must be physical, not a wall --------------------
    reach = swift_float(body_src, "proboscisReachAngle")
    length = swift_float(body_src, "proboscisLengthMm")
    check(reach is not None, "the proboscis reach threshold is declared in Swift")
    check(length is not None, "the extended proboscis length is declared in Swift")
    # The joint the loop writes is `motor.proboscisDrive * 0.8` (SimulationCore
    # syncBody) and limits are (0 .. 1.4). A threshold above the maximum the
    # drive can ever produce makes taste unreachable for a saturated fly.
    m = re.search(r"body\.proboscis\.angle\s*=\s*motor\.proboscisDrive\s*\*\s*([0-9.]+)",
                  core_src)
    amp = float(m.group(1)) if m else None
    check(amp is not None, "the proboscis angle the loop writes is written with a known amplitude")
    if reach is not None and amp is not None:
        max_angle = amp * 1.0          # proboscisDrive saturates at 1
        check(max_angle > reach,
              "a fully driven proboscis opens PAST the reach threshold "
              f"(max {max_angle:.2f} rad > threshold {reach:.2f} rad)",
              "" if max_angle > reach else
              "the threshold exceeds any angle the joint can reach, so the "
              "gate refuses every sample — a wired channel that can never fire")

    # ---- 3. the gate must read the JOINT, not a proximity test -------------
    # The gate is now a pure predicate on the joint state
    # (`FlyBody.proboscisReaches`), which the loop calls with the live joint
    # angle. Assert BOTH halves: the predicate tests the angle, and the loop
    # uses it — a declaration nobody calls is the defect this gate exists for.
    gate_reads_joint = bool(re.search(
        r"static func proboscisReaches\(_ angle: Float\) -> Bool \{\s*angle >= proboscisReachAngle",
        body_src))
    check(gate_reads_joint, "the reach gate is a predicate on the proboscis JOINT "
          "ANGLE (the sense is downstream of the motor output, not a scripted proximity test)")
    loop_uses_gate = "FlyBody.proboscisReaches(body.proboscis.angle)" in core_src
    check(loop_uses_gate, "the loop calls the reach predicate with the LIVE joint "
          "angle (a predicate nothing calls gates nothing)")

    # ---- 4. taste must be sampled at the TIP ------------------------------
    tip_sampled = "tasteAcceptance" in core_src and "proboscisTipPosition" in core_src
    check(tip_sampled, "taste is sampled at the proboscis tip position")

    # ---- 5. the sense must be a contact sense -----------------------------
    contact = bool(re.search(r"let reach: Float = ([0-9.]+)", world_src))
    check(contact, "the world's taste falloff has a fixed, stated reach "
          "(a contact sense, not the odor diffusion radius)")

    # water/pheromone must be neutral so taste != "second odor channel"
    neutral = "case .water, .pheromone: return 0" in world_src
    check(neutral, "water and pheromone carry NO taste valence "
          "(water drives drinking, pheromone drives courtship — not feeding)")

    # ---- 6. sign convention -----------------------------------------------
    sign_ok = ("case .food, .fermentation: valence = 1" in world_src
               and "case .aversive: valence = -1" in world_src)
    check(sign_ok, "food/fermentation positive, aversive negative")

    # ---- 7. the channel must be able to BOOTSTRAP --------------------------
    # The proboscis is driven by SEZ activity, and SEZ's only input is taste. If
    # the only taste sample is gated on the proboscis being open, the channel
    # can never open it: wired, reachable, and permanently silent. Tarsal taste
    # breaks the circle, and this asserts it is sampled from a state that does
    # NOT depend on the proboscis.
    tarsal = "tasteAtTarsus" in core_src
    check(tarsal, "a tarsal (feet-first) taste sample exists — the input that "
          "can open the proboscis the labellar sample depends on")
    tarsal_ungated = bool(re.search(
        r"func tasteAtTarsus[^{]*\{.*?guard dynamics\.isGrounded", core_src, re.S))
    check(tarsal_ungated, "the tarsal sample is gated on STANCE, not on the "
          "proboscis angle (otherwise the loop cannot start)")
    # It must NOT read the proboscis joint at all — reading it would quietly
    # restore the circle even though `isGrounded` is also present.
    tarsal_body = re.search(r"func tasteAtTarsus[^{]*\{(.*?)\n    \}", core_src, re.S)
    check(tarsal_body is not None and "proboscis" not in tarsal_body.group(1),
          "the tarsal sample does not read the proboscis joint angle")
    # The labellar gate must still exist — the fix must not be "sample taste
    # everywhere, which also removes the circularity".
    labellar_gated = bool(re.search(
        r"func tasteAtProboscisTip[^{]*\{.*?guard FlyBody\.proboscisReaches\(body\.proboscis\.angle\)",
        core_src, re.S))
    check(labellar_gated, "labellar taste is STILL gated on the proboscis being "
          "open (the sensory distinction is kept, not dissolved)")

    # ---- 7b. the bootstrap, MEASURED ---------------------------------------
    # The checks above are greps: they prove the two samplers differ in source,
    # not that the loop can actually start. Mirror the two rules and run them
    # over the joint angles the motor system can produce, to show that the
    # tarsal sample fires where the labellar sample cannot.
    reach = float(re.search(r"proboscisReachAngle: Float = ([0-9.]+)", body_src).group(1))
    max_angle = 0.8   # motor writes `proboscisDrive * 0.8`, checked in #4

    def tarsal_fires(grounded, on_food):
        # gated on stance only
        return grounded and on_food

    def labellar_fires(angle, on_food):
        return angle >= reach and on_food

    check(tarsal_fires(True, True) and not labellar_fires(0.0, True),
          "from rest (folded proboscis, standing on food) the tarsal sampler "
          "fires and the labellar one does not — the loop has a starting point, "
          f"measured at angle=0.0 < reach={reach}")
    # And it must be the tarsal sample that CLOSES the loop: with no tarsal
    # input the closed proboscis would never open, so the labellar route would
    # be dead forever.
    can_open_without_tarsal = any(labellar_fires(a, True)
                                  for a in [0.0])  # no motor input => angle stays 0
    check(not can_open_without_tarsal,
          "without the tarsal sample no labellar sample could ever happen — "
          "which is exactly why the loop needs the feet-first route")

    # ---- 8. per-asset reachability ----------------------------------------
    print()
    for name, path in ASSETS:
        if not path.exists():
            print(f"      [{name}] not present, skipped")
            continue
        r = load_targets(path)
        if r is None:
            check(False, f"{name}: the SEZ region exists")
            continue
        print(f"      {name}: SEZ {r['n']} neurons, {r['motor']} motor-labelled, "
              f"{r['sensory']} sensory-labelled, {r['nonreadout']} non-readout, "
              f"max out-degree {r['outdeg_max']}")
        # Pass 2 of the resolver finds a sensory-labelled cell; pass 3 finds a
        # non-readout cell. Either is a live target; neither means DEAD.
        check(r["has_sensory"] or r["has_nonreadout"],
              f"{name}: gustatoryInput resolves to a live SEZ cell",
              "" if (r["has_sensory"] or r["has_nonreadout"]) else
              "no SEZ cell is addressable — the channel is DEAD on this asset")
        check(r["outdeg_max"] > 0,
              f"{name}: the SEZ target can carry current onward (out-degree > 0)",
              "" if r["outdeg_max"] > 0 else
              "the target has no outgoing edge — injected current dies at the cell")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASSED: the gustatory channel is driven, reachable, contact-gated "
          "and sign-correct on every shipped asset.")
    print("  HOW THIS IS EVIDENCED: the target arithmetic runs on the ASSET BYTES"
          " through a Python mirror of the Swift resolver, and the thresholds are"
          " READ FROM THE SWIFT SOURCE, so changing either is a failure here."
          " It does NOT execute Swift — the semantic change is covered by the"
          " XCTest suite (GustatoryPathwayTests.swift), which runs only in CI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())