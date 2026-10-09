#!/usr/bin/env python3
"""App stimulus reachability — a channel the app can never trigger is not a
sense, it is an ornament.

WHY THIS GATE EXISTS
--------------------
Two sensors in this project were fully wired and fully tested in the CORE while
being unreachable in the SHIPPED APP:

  * looming  — `VisionSystem` grew a looming channel with its own tests and a
     producer gate, but no code in the app ever created a `MovingObject`, so
     the channel ran every frame against an empty world. Radial expansion needs
     something to approach.
  * gustation — `SensoryInterface.gustatoryInput` resolved a live target on the
    real assets and had ZERO call sites anywhere, app or test.

Both looked complete from the core, because that is where every gate pointed.
This gate points at the boundary the others do not: the app must be able to
PRODUCE the stimulus each sensory channel consumes.

HOW THIS IS EVIDENCED
---------------------
Static call-site analysis over the Swift sources (this does not execute Swift).
A channel counts as reachable when the app target references the producer API
that feeds it. The semantic behaviour of each channel is covered by the XCTest
suite; this gate only answers "can the app ever exercise it?".
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "ios/Sources/FlyBrainPetApp")
CORE = os.path.join(ROOT, "ios/Sources/FlyBrainCore")

failures = []


def check(ok, msg):
    print(("  [PASS] " if ok else "  [FAIL] ") + msg)
    if not ok:
        failures.append(msg)


def read_all(d):
    out = {}
    for dirpath, _, names in os.walk(d):
        for n in names:
            if n.endswith(".swift"):
                p = os.path.join(dirpath, n)
                out[p] = open(p, encoding="utf-8").read()
    return out


app_src = read_all(APP)
core_src = read_all(CORE)
app_text = "".join(app_src.values())
core_text = "".join(core_src.values())

print("App stimulus reachability — can the shipped app exercise each channel?")

# ---- 1. every sensory channel must have a call site in the CORE ----------
# (This is the gustation defect: the resolver worked, nobody called it.)
CHANNELS = ["odorInput", "loomingInput", "gustatoryInput", "haltereInput",
            "wingStrainInput", "touchAfferent", "touchInput"]
for ch in CHANNELS:
    n = len(re.findall(r"sensory\.%s\(" % ch, core_text))
    check(n > 0, f"{ch} is called by the simulation core ({n} call site(s))")# ---- 2. the APP must be able to produce the stimulus ---------------------
# looming needs a moving object; taste needs something edible/aversive placed;
# odor needs a source; light needs a light. Each is a producer the app must call.
PRODUCERS = {
    "looming (escape)": "addMovingObject",
    "odour": "addOdorSource",
    "light": "addLight",
    "obstacle": "addObstacle",
}
for label, api in PRODUCERS.items():
    n = len(re.findall(r"\b%s\(" % api, app_text))
    check(n > 0, f"the app can create {label} (calls `{api}`, {n} site(s))")

# ---- 3. and the UI must expose it ----------------------------------------
# A producer with no control is reachable only from code that never runs.
ui = app_src.get(os.path.join(APP, "RootView.swift"), "")
check("loomAt(" in ui,
      "the UI exposes the looming stimulus (`loomAt`) — a producer the user "
      "cannot trigger is still unreachable")
check(re.search(r"app\.(dropFood|dropWater)\(|app\.dropFood\(|dropFood\(at:", ui) is not None,
      "the UI exposes the odour/food stimuli (a control reaches `dropFood`/"
      "`dropWater`; the button body spans lines, so the match is on the call)")

# ---- 4. the looming stimulus must actually CLOSE on the fly ---------------
# `MovingObject` with zero velocity never expands the retinal image, so it
# would be a moving-object by type only. The app must aim it at the eye.
appmodel = app_src.get(os.path.join(APP, "AppModel.swift"), "")
aims_at_fly = bool(re.search(
    r"func loomAt\([^)]*\)[^{]*\{.*?core\.position\s*-\s*spot", appmodel, re.S))
check(aims_at_fly,
      "`loomAt` aims the object AT the fly's live position (a stationary or "
      "misdirected object produces no expansion)")

if failures:
    print("\nFAILED: %d check(s)" % len(failures))
    print("NOTE: this gate reads source text; it does not run the app. The "
          "behaviour of each channel is covered by XCTest.")
    sys.exit(1)
print("\nAll app stimulus reachability checks passed.")