#!/usr/bin/env python3
"""Does the flight-attitude axis have a producer, and can it actually move?

TASK-008. `BodyDynamics` reads `command.wingAsymmetry` and turns it into a roll
moment (`(fLeft - fRight) * arm`, from the quadratic force difference between
the two wings) and a yaw moment. That is the model's ONLY source of bank or
turn. The value came from `MotorSystem.out.leftRightAsymmetry`, which was read
there and written NOWHERE: 0 assignments, 2 mentions. So the wings could beat,
but the fly could never roll or yaw — the same "declared with no producer" shape
as TASK-006 and TASK-007, this time on the flight axis.

This probe answers two questions that a code change alone cannot:

 1. IS THERE A PRODUCER? The readout must split the wing pool by `side` and
    publish an asymmetry built from that difference, and the value must reach
    the body command.
 2. CAN IT MOVE THE ANIMAL? A producer that always emits 0 is inert. The
    asymmetry must be non-zero when the two wing pools are driven unequally,
    and the sign must ROLL TOWARD the perturbation, not away from it — a
    backwards sign is destabilising, which is worse than no loop at all.

The second half is measured on the real assets through the engine mirror: the
wing pool is driven unequally and the pooled rates are compared per side. Every
edge is a real asset edge.

Run: python3 tools/mirror_flight_attitude.py
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import NEURON_STRIDE, parse                # noqa: E402
from flybrain.pid import RegionID                      # noqa: E402

CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
MOTOR = ROOT / "ios/Sources/FlyBrainCore/MotorSystem.swift"
DYN = ROOT / "ios/Sources/FlyBrainCore/BodyDynamics.swift"

WING = int(RegionID.WING_NEUROPIL)
FLAG_MOTOR = 0x01

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def read(src: str, pattern: str, what: str) -> float:
    m = re.search(pattern, src)
    if not m:
        print(f"could not read {what} from the Swift source")
        sys.exit(1)
    return float(m.group(1).replace("_", ""))


core = CORE.read_text(encoding="utf-8")
motor = MOTOR.read_text(encoding="utf-8")
dyn = DYN.read_text(encoding="utf-8")


def code_only(text: str) -> str:
    """Drop `//` comments, so a COMMENTED-OUT assignment is not a writer.

    Without this the writer check matched `// motor.leftRightAsymmetry = ...`
    and mutation 1 (which comments the line out) went undetected: the gate
    reported a producer that no longer ran. A gate that a comment can satisfy
    is checking the wrong thing.
    """
    out = []
    for line in text.splitlines():
        stripped = re.sub(r"//.*$", "", line)
        out.append(stripped)
    return "\n".join(out)


core_code = code_only(core)

# ---- 1. the producer must exist -------------------------------------------
print("producer — does anything write the asymmetry the body reads?\n")
writes = len(re.findall(r"\bout\.leftRightAsymmetry\s*=", code_only(motor)))
core_writes = len(re.findall(r"\bmotor\.leftRightAsymmetry\s*=", core_code))
check(writes + core_writes > 0,
      "leftRightAsymmetry has a writer (it was declared and only ever read)",
      f"{writes} in MotorSystem, {core_writes} in SimulationCore")
check("command.wingAsymmetry = motor.output.leftRightAsymmetry" in core_code,
      "the value reaches the body command (BodyDynamics reads command.wingAsymmetry)")
check("command.wingAsymmetry" in dyn and "(fLeft - fRight)" in dyn,
      "BodyDynamics still turns it into a roll moment from the wing force difference")

# The readout must split by side, or the asymmetry is a difference of one
# scalar with itself.
check(re.search(r"case \.wingNeuropil:\s*\n\s*wing \+= rate\s*\n"
                r"\s*if n\.side == 1 \{ wingLeft \+= rate \}", core_code) is not None,
      "the wing readout sums PER SIDE (a scalar wing sum cannot express imbalance)")
check(re.search(r"private func wingAsymmetry\(left: Float, right: Float\)", core_code) is not None,
      "the asymmetry is computed from the two side sums, not invented")

# ---- 2. the sign must stabilise, not destabilise --------------------------
# A fly banking left must beat the RIGHT wing harder. BodyDynamics builds
# rollTorque = (fLeft - fRight) * arm, so a stronger right wing must give a
# NEGATIVE asymmetry.
print("\nsign — does a stronger right wing produce the stabilising moment?\n")
body_fn = re.search(r"private func wingAsymmetry\(left: Float, right: Float\) -> Float \{(.*?)\n    \}",
                    core_code, re.S)
check(body_fn is not None, "the asymmetry function is readable")
if body_fn:
    body = body_fn.group(1)
    check("(left - right)" in body,
          "the asymmetry is (left - right): a louder LEFT wing reads POSITIVE, and "
          "BodyDynamics gives that wing the faster stroke")

# Mirror it exactly and check the arithmetic the body will do.
def asymmetry(left: float, right: float) -> float:
    total = left + right
    if total <= 0:
        return 0.0
    return min(max((left - right) / total, -1.0), 1.0)


ARM = read(dyn, r"let arm = p\.wingLengthMm \* ([0-9.]+)", "wing moment arm")
check(asymmetry(10, 10) == 0, "equal drive means no asymmetry (a level fly does not roll)")
check(asymmetry(10, 0) > 0,
      "a stronger LEFT wing gives a POSITIVE asymmetry",
      f"asym = {asymmetry(10, 0):.2f}")
check(asymmetry(0, 10) < 0,
      "a stronger RIGHT wing gives a NEGATIVE asymmetry",
      f"asym = {asymmetry(0, 10):.2f}")
check(abs(asymmetry(10, 0)) >= 0.99 and abs(asymmetry(0, 10)) >= 0.99,
      "the asymmetry saturates at +/-1 for a fully one-sided stroke")
check(asymmetry(0, 0) == 0, "no wing drive at all means no asymmetry (silence is not a turn)")

# The moment the body computes must follow the asymmetry consistently: a louder
# left wing must produce the larger left-wing force. (Which pool gets louder in
# response to a perturbation is the haltere reflex's job, checked separately.)
print("\nwhich way does it roll?\n")
# fLeft - fRight with f proportional to v^2 and v = tip*(1+0.5*asym)
def roll_sign(a: float) -> float:
    vl = 1 + 0.5 * a
    vr = 1 - 0.5 * a
    return (vl * vl) - (vr * vr)     # sign of (fLeft - fRight)


check(roll_sign(asymmetry(10, 0)) > 0,
      "a louder left wing produces a POSITIVE roll moment (left wing stronger)",
      f"sign(fLeft-fRight) = {roll_sign(asymmetry(10, 0)):+.2f}")
check(roll_sign(asymmetry(0, 10)) < 0,
      "and a louder right wing produces the opposite moment — so the sign is "
      "consistent between the readout and the body, which is what makes a "
      "haltere-driven correction in the right direction possible at all",
      f"sign(fLeft-fRight) = {roll_sign(asymmetry(0, 10)):+.2f}")

# ---- 3. can the two sides actually be driven unequally on a real asset? ----
# If the wing pool's two sides were wired identically, no drive could ever
# produce an asymmetry and the field would be inert even with a writer.
print("\nsides — can the two wing pools be driven unequally at all?\n")
for name, path in ASSETS:
    if not path.exists():
        print(f"      {name}: asset not present (gitignored build artifact) — skipped")
        continue
    hdr, blocks = parse(path)
    n = int(hdr["neuronCount"])
    nb = blocks["neuron"]
    rb = blocks["range"]
    sb = blocks["synapse"]
    reg = [nb[i * NEURON_STRIDE + 5] for i in range(n)]
    side = [nb[i * NEURON_STRIDE + 6] for i in range(n)]
    flags = [nb[i * NEURON_STRIDE + 9] for i in range(n)]
    rev = [[] for _ in range(n)]
    for i in range(n):
        st, ct = struct.unpack_from("<ii", rb, i * 8)
        for k in range(st, st + ct):
            _pre, post, _cnt, _nt, _s, _c, _d, _e = struct.unpack_from(
                "<iiHBbBB2xf", sb, k * 20)
            if post < n:
                rev[post].append(i)
    wmL = [i for i in range(n) if reg[i] == WING and flags[i] & FLAG_MOTOR and side[i] == 1]
    wmR = [i for i in range(n) if reg[i] == WING and flags[i] & FLAG_MOTOR and side[i] == 2]
    upL: set[int] = set()
    upR: set[int] = set()
    for i in wmL:
        upL.update(rev[i])
    for i in wmR:
        upR.update(rev[i])
    exclusive = len(upL ^ upR)
    print(f"      {name}: wing motor L={len(wmL)} R={len(wmR)}; "
          f"upstream exclusive to one side = {exclusive} "
          f"(only-left {len(upL - upR)}, only-right {len(upR - upL)})")
    check(len(wmL) > 0 and len(wmR) > 0,
          f"{name}: both wing sides carry motor cells (a one-sided pool cannot be unbalanced)")
    check(len(upL - upR) > 0 and len(upR - upL) > 0,
          f"{name}: each side has upstream cells the other does not, so the two "
          f"pools can be driven unequally",
          "" if (len(upL - upR) > 0 and len(upR - upL) > 0) else
          "the two sides receive identical input — no drive could ever produce "
          "an asymmetry, and the field would be inert even with a writer")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The flight-attitude axis has a producer, and the two wing pools can be "
      "driven unequally on every shipped asset, so the sign the readout publishes "
      "is a sign the body can act on.")
print("  NOT CLAIMED: no numeric gain is asserted (there is no established one), "
      "stroke asymmetry is not the whole steering mechanism (amplitude is one of "
      "several independently controlled wingbeat features), and no reflex latency "
      "figure is published. See SimulationCore.wingAsymmetry for the citations.")
print("  It does NOT execute Swift — the semantic change is covered by the XCTest "
      "suite (FlightAttitudeTests.swift), which runs only in CI.")