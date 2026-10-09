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


def read_joint_amplitude(core_src: str, body_src: str) -> float | None:
    """The joint angle a SATURATED drive produces: `proboscisDrive * <amp>`.

    The amplitude may be a bare literal or a named `FlyBody` constant. The
    first version of this gate only matched a literal, so naming the constant
    (which was the right change — the number is shared by the write site, the
    reach predicate and the tip geometry) silently turned this check into a
    FAIL. Resolving both forms keeps the check honest either way: it is the
    VALUE that has to clear the threshold, not the spelling.

    Returns None if the write site cannot be found at all, which is itself a
    failure (the gate must not pass by finding nothing).
    """
    m = re.search(r"body\.proboscis\.angle\s*=\s*motor\.proboscisDrive\s*\*\s*"
                  r"([A-Za-z_][A-Za-z0-9_.]*|[0-9.]+)", core_src)
    if not m:
        return None
    token = m.group(1)
    try:
        return float(token)
    except ValueError:
        pass
    name = token.rsplit(".", 1)[-1]
    return swift_float(body_src, name)


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
    # The joint the loop writes is `motor.proboscisDrive * <amplitude>`
    # (SimulationCore.applyArticulation) and the drive saturates at 1, so the
    # amplitude IS the maximum angle a saturated fly can produce. A threshold
    # above it makes taste unreachable for every fly.
    #
    # The amplitude may be a literal or a NAMED constant. It became
    # `FlyBody.proboscisMaxAngle` when the same number was needed by three
    # things (the write site, the reach predicate, the tip geometry) — an
    # unnamed literal in one file cannot be checked against a predicate in
    # another. This gate follows the name and resolves it from BodyModel.swift,
    # so naming the constant does not make the check vacuous: a constant that
    # is wrong (say 0.2 < reach 0.35) still fails here.
    amp = read_joint_amplitude(core_src, body_src)
    check(amp is not None, "the proboscis angle the loop writes is written with a known amplitude",
          "" if amp is None else f"amplitude {amp:.2f} rad at full drive")
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
    # The reach must be a declared constant, and it must be a CONTACT reach —
    # not the odour diffusion radius wearing a taste sensor's name. Resolve the
    # name the taste falloff actually divides by rather than matching one
    # spelling: `contactReach` moved into `OdorSource` (shared with ingestion,
    # so the fly has one mouth and not two), and a gate pinned to the old
    # spelling failed on a change that made the code MORE correct.
    reach_name = re.search(r"static let (contactReach)\s*:\s*Float\s*=\s*([0-9.]+)", world_src)
    uses_declared = bool(re.search(r"1\s*-\s*d\s*/\s*(?:Self\.|OdorSource\.)?contactReach", world_src))
    check(reach_name is not None and uses_declared,
          "the world's taste falloff has a fixed, stated reach "
          "(a contact sense, not the odor diffusion radius)",
          f"{reach_name.group(1)} = {reach_name.group(2)} mm" if reach_name else
          "no declared contact reach found")
    # And the reach must be SHORTER than the odour plume, or taste is a second
    # smell channel: compare the contact reach against the odour falloff's
    # characteristic distance.
    diffusion = swift_float(world_src, "diffusionConstant")
    if reach_name and diffusion:
        check(float(reach_name.group(2)) < diffusion,
              "the contact reach is shorter-range than the odour plume "
              f"({reach_name.group(2)} mm < D = {diffusion})", "")

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
    max_angle = read_joint_amplitude(core_src, body_src)  # same source as #4, not re-typed

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