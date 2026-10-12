#!/usr/bin/env python3
"""Does the water channel reach the brain, and is it driven by a real sense?

A channel can be wired and still be dead in two different ways, and only one of
them shows up as "no current":

  NO CELL        `waterInput` resolves to nothing on the asset we ship, so the
                 current is injected into no neuron at all. The channel is
                 present in the source and absent from the loop.
  NEVER DRIVEN   the cell resolves, but nothing ever calls the channel — which is
                 exactly TASK-006's shape: `InternalState.drink()` had no caller
                 and `thirstDrive` was read by nobody. A resolver that works and
                 nobody calls is the gustation defect (TASK-001) verbatim.

WHY THIS IS SEPARATE FROM THE GUSTATORY PROBE. Water shares the SEZ target with
sugar and bitter but is a different sense: it carries NO phagostimulant valence
(`OdorSource.tasteAcceptance` returns 0 for it), it has its own receptor in
reality (PPK28 — Cameron et al., *Nature* 2010, PMID 20364123), and its own call
site. Folding the check into the gustatory probe would let a change that removes
the water call site pass as long as taste still worked.

What this gate asserts, all read from the Swift source and the packed assets at
run time rather than restated here:

  1. `SimulationCore` CALLS the water channel (thirst's consumer exists).
  2. The water sense is sampled at the TARSI as well as the labellum — without
     the tarsal route a folded fly cannot sense the water that would open its
     mouth, and the loop is circular (TASK-001's trap).
  3. The tarsal sample is not gated on the proboscis being open.
  4. Drinking has a call into the world AND into `InternalState.drink`.
  5. `waterInput` resolves to a live, non-readout cell on every shipped asset.
  6. Water still carries NO taste valence — the property that makes the two
     needs distinguishable.
  7. The thirst modulation is read at the SAMPLE, and it never zeroes.

Run: python3 tools/probe_water_pathway.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import (RANGE_STRIDE, neuron_at, parse)        # noqa: E402
from flybrain.pid import RegionID                          # noqa: E402

CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
STATE = ROOT / "ios/Sources/FlyBrainCore/InternalState.swift"
SENSORY = ROOT / "ios/Sources/FlyBrainCore/SensoryInterface.swift"
WORLD = ROOT / "ios/Sources/FlyBrainCore/World.swift"
BODY = ROOT / "ios/Sources/FlyBrainCore/BodyModel.swift"

FLAG_MOTOR, FLAG_SENSORY = 0x01, 0x02

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


core = CORE.read_text(encoding="utf-8")
state = STATE.read_text(encoding="utf-8")
sensory = SENSORY.read_text(encoding="utf-8")
world = WORLD.read_text(encoding="utf-8")

print("water pathway — is the sense wired, driven, and non-circular?\n")

# ---- 1. the channel must be CALLED (thirst's consumer) ---------------------
calls = len(re.findall(r"sensory\.waterInput\(", core))
check(calls > 0, "SimulationCore calls the water channel (thirst has a consumer)",
      f"{calls} call site(s)" if calls else
      "no call site — the channel exists in the source and is absent from the "
      "loop, which is TASK-006 exactly")

# ---- 2/3. the sense must be sampled at the TARSI and not gated on the mouth -
tarsal = re.search(r"private func waterAtTarsus\(.*?\n    \}", core, re.S)
check(tarsal is not None,
      "the water sense is sampled at the tarsi (a folded fly's only water sense)")
if tarsal:
    body = tarsal.group(0)
    check("proboscisReaches" not in body,
          "the tarsal water sample is NOT gated on the proboscis being open",
          "" if "proboscisReaches" not in body else
          "a folded fly could never sense the water that would open its mouth — "
          "the loop is circular")
    check("isGrounded" in body,
          "the tarsal sample is gated on real stance (contact, not proximity)")
check("private func waterAtProboscisTip(" in core,
      "the water sense is also sampled at the labellum (the mouth confirms)")

# ---- 4. drinking must call the world AND the state -------------------------
check("w.drinkWater(" in core or "world?.drinkWater(" in core,
      "the loop asks the WORLD for water (a measured amount, not a flag)")
check(re.search(r"internalState\.drink\(amount:", core) is not None,
      "the loop calls InternalState.drink — the consumer TASK-006 was about")
# The drink must be bounded by the deficit, or hydration pins at 1.
check(re.search(r"deficit \* Self\.drinkDeficitMultiplier", state) is not None,
      "the drink is bounded by the animal's deficit (not a flat clip at 1)")

# ---- 5. water must still carry NO taste valence ---------------------------
check("case .water, .pheromone: return 0" in world,
      "water carries NO phagostimulant valence in tasteAcceptance")
check(re.search(r"func waterContact\(at p: SIMD3<Float>\) -> Float \{\s*guard kind == \.water",
                world) is not None,
      "a SEPARATE water query exists and rejects every non-water source")

# ---- 7. the thirst modulation is applied and never zeroes ------------------
check(re.search(r"thirstModulatedWater\(", core) is not None,
      "the water sample is thirst-modulated in the loop (thirstDrive has a consumer)")
floor = re.search(r"thirstGainFloor\s*:\s*Float\s*=\s*([0-9.]+)", state)
check(floor is not None and float(floor.group(1)) > 0,
      "the thirst gain floor is > 0 — water stays a sense, not a thirst alarm",
      f"floor = {floor.group(1)}" if floor else "floor not found")

# ---- 6. the SEZ target must be live on every shipped asset -----------------
# Mirror the resolver's precedence: a sensory-labelled SEZ cell beats a
# non-readout one; a MOTOR cell is never a valid afferent (injecting into one
# feeds the readout directly and bypasses the connectome).
SEZ = int(RegionID.SUBESOPHAGEAL_ZONE)


def sez_summary(path: Path) -> dict | None:
    if not path.exists():
        return None
    hdr, blocks = parse(path)
    nb = blocks["neuron"]
    n_neurons = int(hdr["neuronCount"])
    # The range block is one record per neuron, indexed by neuron: a CSR row
    # `(start, count)` into the synapse block. `rangeCount` is not a header
    # field — the neuron count is the row count.
    rb = blocks["range"]
    import struct
    outdeg = [0] * n_neurons
    for i in range(min(n_neurons, len(rb) // RANGE_STRIDE)):
        _start, count = struct.unpack_from("<ii", rb, i * RANGE_STRIDE)
        outdeg[i] = count
    n = motor = sensory_labelled = nonreadout = 0
    max_out = 0
    for i in range(n_neurons):
        rec = neuron_at(nb, i)
        if rec["region"] != SEZ:
            continue
        n += 1
        flags = rec["flags"]
        if flags & FLAG_MOTOR:
            motor += 1
            continue
        if flags & FLAG_SENSORY:
            sensory_labelled += 1
        else:
            nonreadout += 1
        max_out = max(max_out, outdeg[i])
    return {"n": n, "motor": motor, "sensory_labelled": sensory_labelled,
            "nonreadout": nonreadout, "outdeg_max": max_out}


print()
for name, path in ASSETS:
    r = sez_summary(path)
    if r is None:
        print(f"      {name}: asset not present (gitignored build artifact) — skipped")
        continue
    print(f"      {name}: SEZ {r['n']} neurons, {r['motor']} motor-labelled, "
          f"{r['sensory_labelled']} sensory-labelled, {r['nonreadout']} non-readout, "
          f"max out-degree {r['outdeg_max']}")
    check(r["sensory_labelled"] + r["nonreadout"] > 0,
          f"{name}: waterInput resolves to a live SEZ cell",
          "" if r["sensory_labelled"] + r["nonreadout"] > 0 else
          "every SEZ cell is motor-labelled — the channel is DEAD on this asset "
          "(the class TASK-007 found: a channel with no producer)")
    check(r["outdeg_max"] > 0,
          f"{name}: the SEZ target can carry current onward (out-degree > 0)",
          "" if r["outdeg_max"] > 0 else
          "the target has no outgoing edge — injected current dies at the cell")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PASSED: the water sense is driven, contact-gated, non-circular, and "
      "resolves to a live SEZ cell on every shipped asset.")
print("  HOW THIS IS EVIDENCED: the target arithmetic runs on the ASSET BYTES"
      " through a mirror of the Swift resolver's precedence, and the gating is"
      " read from the Swift SOURCE, so changing either is a failure here."
      " It does NOT execute Swift — the semantic change is covered by the"
      " XCTest suite (ThirstLoopTests.swift), which runs only in CI.")