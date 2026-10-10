#!/usr/bin/env python3
"""Gate for the spawned-at-rest rule, and for the last spike in the silence test.

WHY THIS GATE EXISTS
--------------------
`tools/probe_rest_activity.py` removed the eye's phantom OFF flash and
`testTheConnectomeIsSilentWithNoStimulus` then measured 0 spikes OFFLINE. CI
still reported **1 spike** on the same fixture. A mirror and a compiler that
disagree is not a flake to round away: the mirror was not modelling a channel
Swift was, so the test was being kept green by the gap.

Attribution (measured, in this file, per-channel):

  * the EYE arm contributes 0 spikes — already fixed, and re-asserted here;
  * the ODOUR channel carries a standing `c * 40 - 5` = **-5 nA** into both
    antennal-lobe neurons in a zero-odour world. It contributes **0 spikes**:
    the offset is behaviourally inert on the shipped asset. It is left alone
    deliberately — it is mirrored in five tools and is a transduction
    baseline, not a typo — but it means "no stimulus" is false for that
    channel, so the constant is pinned below and the docstring it belongs to
    is no longer allowed to call the transfer a "saturating curve";
  * the TARSAL arm contributes the spike. It is a REAL contact transient, and
    the fixture was manufacturing it.

THE BUG THE TARSAL TRANSIENT EXPOSED
------------------------------------
`setPose` dropped the body at the caller's raw y. "On the substrate" is
written as `groundY + standHeightMm` (tests) or an origin-ish `y = 0.2` (the
app), and the contact penalty must compress by `weight / contactStiffness`
before it can carry the weight, so BOTH are below the height the body
balances at. Measured below: the body spawns 0.78 mm into the floor at a
launch load of **42 body weights**, and drives the tarsal channel for 67 steps
(118 at the app's height). The contact is OVERDAMPED (damping 2500 > critical
1117), so the launch decays in place rather than throwing the fly airborne —
the load spike is the whole defect. The `TasteWorld` in the silence test has no taste at all, so every
one of those injections was a stimulus the fixture created — which is also
why the contact gate in `emitMechanosensoryReafference` had to refuse to
report while the fly was flying.

The fix is a force balance, not a fudge factor:
`restHeightMm = groundY + standHeightMm - weight / contactStiffness`.
`BodyDynamics` spawns there and `teleport` snaps any at-or-below-standing
request to it, so a pose is a pose and not an impact.

WHAT IS ASSERTED
----------------
  1. CONTROL that the bug is real: at the UNBALANCED height (stand height,
     spawn as it was) the tarsal arm drives the network. Without this the
     "quiet now" result could be silence for any reason.
  2. The balance height produces ZERO tarsal injections — and the exact
     balance point is derived from the constants, not tuned to fit.
  3. `teleport` snaps, so every existing `setPose` call site is quiet without
     being rewritten — including the app's y=0.2 — while a spawn ABOVE the
     substrate is still placed where it was asked for (a flying spawn is not
     grounded and must not be yanked down).
  4. The odour offset is still exactly what the Swift source emits, so this
     gate moves if the transfer function does.
"""
import math
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

from fbpack import parse, neuron_at                 # noqa: E402
from sim_neural_engine import Engine, edge_current  # noqa: E402
import sim_body_dynamics as body_mod                # noqa: E402

ASSET = ROOT / "data" / "generated" / "demo_micro.fbpack"
CORE = ROOT / "ios" / "Sources" / "FlyBrainCore" / "SimulationCore.swift"
SENSORY = ROOT / "ios" / "Sources" / "FlyBrainCore" / "SensoryInterface.swift"
BODY = ROOT / "ios" / "Sources" / "FlyBrainCore" / "BodyDynamics.swift"
TYPES = ROOT / "ios" / "Sources" / "FlyBrainCore" / "Types.swift"

# Parameter profiles by region, mirroring `SimulationParameters.profile(for:)`
# the same way `tools/probe_rest_activity.py` does.
MOTOR_REGIONS = (15, 16, 17, 18, 19)     # VNC .. abdominalNeuromere
MB_CC_REGIONS = (9, 11)                  # mushroomBody, centralComplex

checks = []


def check(ok, label, detail=""):
    checks.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    return ok


def read(pattern, src, what, cast=float):
    m = re.search(pattern, src)
    if not m:
        print(f"could not read {what} from source — refusing to guess")
        sys.exit(1)
    return cast(m.group(1))


# ---------------------------------------------------------------- source reads
core_src = CORE.read_text()
sen_src = SENSORY.read_text()
body_src = BODY.read_text()

GAIN = read(r"reafferenceGain: Float = ([0-9.]+)", core_src, "reafferenceGain")
DEADBAND = read(r"reafferenceDeadband: Float = ([0-9.]+)", core_src,
                "reafferenceDeadband")
TAU_MS = read(r"reafferenceAdaptationTauMs: Double = ([0-9.]+)", core_src,
              "reafferenceAdaptationTauMs")

m = re.search(r"public func odorInput\(.*?\n(.*?)\n    \}", sen_src, re.S)
m2 = re.search(r"cL \* ([0-9.]+)\s*-\s*([0-9.]+)", m.group(1) if m else "")
if not m2:
    print("could not read the odour transfer from source — refusing to guess")
    sys.exit(1)
ODOUR_SPAN, ODOUR_BIAS = float(m2.group(1)), float(m2.group(2))

# The transfer must be described as what it is. This is not pedantry: the
# docstring said "saturating curve" for a plain linear transfer, and a channel
# documented as something other than what it emits is how the -5 nA survived
# unreviewed.
odour_doc = sen_src[:sen_src.index("public func odorInput")]
last_doc = odour_doc[odour_doc.rindex("/// Inject an odor-concentration"):]
doc_lower = last_doc.lower()
claims_saturation = ("saturating" in doc_lower
                     and "not a saturating" not in doc_lower)
check(not claims_saturation,
      "the odour docstring does not claim a saturation the code does not have",
      "linear transfer, described as linear"
      if not claims_saturation else "still claims a saturating curve")

print(f"[SOURCE] reafferenceGain={GAIN} deadband={DEADBAND} tau={TAU_MS} ms")
print(f"[SOURCE] odour transfer = c*{ODOUR_SPAN} - {ODOUR_BIAS} nA")
check(ODOUR_BIAS != 0,
      "the odour channel declares a NON-ZERO output in a zero-odour world",
      f"c=0 gives {0 * ODOUR_SPAN - ODOUR_BIAS:+g} nA into each antennal-lobe neuron")

# `restHeightMm` must exist and be the force balance it claims to be.
m = re.search(r"public var restHeightMm: Float \{(.*?)\n    \}", body_src, re.S)
if not m:
    print("could not read restHeightMm from source — refusing to guess")
    sys.exit(1)
rest_body = m.group(1)
check("contactStiffness" in rest_body and "bodyMassMg" in rest_body,
      "restHeightMm is written as the force balance of stiffness and weight",
      " ".join(rest_body.split())[:96])

STAND_HEIGHT = read(r"standHeightMm: Float = ([0-9.]+)", body_src, "standHeightMm")
print(f"[SOURCE] FlyBody.standHeightMm = {STAND_HEIGHT} mm")

BALANCE = body_mod.Body.REST_HEIGHT
DERIVED_PEN = body_mod.MASS * body_mod.GRAVITY / body_mod.CONTACT_STIFFNESS
print(f"[MEASURE] balance height {BALANCE:.5f} mm "
      f"(= stand {STAND_HEIGHT} - penetration {DERIVED_PEN:.5f})")
check(abs(BALANCE - (body_mod.GROUND_Y + STAND_HEIGHT - DERIVED_PEN)) < 1e-9,
      "the mirror's REST_HEIGHT is the same force balance as Swift's",
      f"{BALANCE:.6f} mm")

# ------------------------------------------------------------------ asset load
hdr, blocks = parse(str(ASSET))
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


SYN = [edges(u) for u in range(n)]
print(f"asset: {n} neurons, {sum(len(s) for s in SYN)} edges")


def build_engine():
    e = Engine(n, regions=regions, syn_out=SYN)
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


def input_neuron(region, side):
    """`SensoryInterface.inputNeuron(region:side:)`: first in-region cell of
    that side, else the region's first cell."""
    first = None
    for i in range(n):
        if regions[i] != region:
            continue
        if first is None:
            first = i
        if sides[i] == side:
            return i
    return first


def region_ordinals():
    """`RegionID` declares explicit ordinals, so the assignment is the source
    of truth — not declaration order, which would shift if a case were added."""
    m = re.search(r"enum RegionID[^{]*\{(.*?)\n\}", TYPES.read_text(), re.S)
    return {name: int(val) for name, val in
            re.findall(r"case (\w+)\s*=\s*(\d+)", m.group(1))} if m else {}


ORD = region_ordinals()
AL_REG = ORD.get("antennalLobe")
LEG_REG = ORD.get("legNeuromere")
print(f"[SOURCE] RegionID.antennalLobe={AL_REG} legNeuromere={LEG_REG}")

ODOUR_TARGETS = [t for t in (input_neuron(AL_REG, 1), input_neuron(AL_REG, 2))
                 if t is not None]
TARSAL = input_neuron(LEG_REG, 1)
print(f"[SOURCE] odour afferents {ODOUR_TARGETS}, tarsal afferent {TARSAL}")

DT_MS = 0.1
STEPS = 600


def tarsal_series(spawn_y, steps=STEPS, snap=True):
    """Contact-load high-pass series for a body placed at `spawn_y`, mirroring
    `emitMechanosensoryReafference` (rectifying high-pass, adapting baseline,
    grounded gate)."""
    b = body_mod.Body()
    if snap:
        b.teleport([-40.0, spawn_y, 0.0])
    else:
        b.pos = [-40.0, spawn_y, 0.0]
        b.vel = [0.0, 0.0, 0.0]
        b.grounded = spawn_y <= body_mod.GROUND_Y + body_mod.STAND_HEIGHT
        b.normal = 0.0
    placed_y = b.pos[1]
    b.grounded_at_spawn = b.grounded
    b.airborne_seen = False
    out, base, init = [], None, False
    for _ in range(steps):
        b.step(legs_in_contact=True, dt=0.0001)
        if b.pos[1] > body_mod.GROUND_Y + body_mod.STAND_HEIGHT:
            b.airborne_seen = True
        load = b.normal / (body_mod.MASS * body_mod.GRAVITY)
        if not init:
            base, init = load, True
        base += (load - base) * min(1.0, 0.0001 * 1000.0 / max(TAU_MS, 1e-3))
        out.append((load - base, bool(b.grounded)))
    b.placed_y = placed_y
    return out, b


def run(series, odour=True, steps=STEPS, with_eye_flash=False):
    e = build_engine()
    for s in range(steps):
        t = s * DT_MS
        if odour:
            for tgt in ODOUR_TARGETS:
                e.inject(tgt, 0 * ODOUR_SPAN - ODOUR_BIAS, at=t)
        if series is not None:
            dev, grounded = series[s]
            inten = dev * GAIN
            if grounded and inten > DEADBAND:
                e.inject(TARSAL, inten * 50, at=t)
        e.step()
    return e.cumTotal


INJ = lambda series: sum(1 for d, g in series if g and d * GAIN > DEADBAND)  # noqa: E731

# 1) control: the bug is real — spawn as it was, WITHOUT the snap.
# The raw y the silence test passes is 0, i.e. 0.78 mm INTO the floor. That is
# the launch; the unloaded stand height is a gentler second case.
sunk, body_sunk = tarsal_series(body_mod.GROUND_Y, snap=False)
unloaded, body_unloaded = tarsal_series(body_mod.GROUND_Y + STAND_HEIGHT, snap=False)
print(f"[MEASURE] raw y=0:      peak load {body_sunk.peak_load:7.2f} body weights, "
      f"airborne steps {sum(1 for _, g in sunk if not g):4d}, "
      f"bounces {body_sunk.bounces}")
print(f"[MEASURE] y=standHeight: peak load {body_unloaded.peak_load:7.2f} body weights, "
      f"airborne steps {sum(1 for _, g in unloaded if not g):4d}, "
      f"bounces {body_unloaded.bounces}  (zero penetration, so no launch)")

check(body_sunk.peak_load > 10,
      "CONTROL: the raw spawn height (y=0) is a launch, not a pose",
      f"peak {body_sunk.peak_load:.1f} body weights — the spring is compressed "
      f"by the full stand height, against a rest penetration of "
      f"{DERIVED_PEN / STAND_HEIGHT * 100:.1f}% of it")
check(sum(1 for _, g in sunk if not g) == 0,
      "CONTROL: the launch decays IN PLACE — the contact is overdamped",
      f"0 airborne steps at {body_sunk.peak_load:.1f} body weights, so the "
      f"load spike is the defect, not a bounce")
check(INJ(sunk) > 0 and run(sunk, odour=False) > 0,
      "CONTROL: the raw spawn drives the network — this is the CI spike",
      f"{INJ(sunk)} tarsal injections, {run(sunk, odour=False)} spikes")

# Even the unloaded stand height — the "on the substrate" spelling — injects:
# penetration is zero there, so the contact only builds as the body settles.
check(INJ(unloaded) > 0,
      "CONTROL: spawning at the unloaded stand height also injects",
      f"{INJ(unloaded)} injections from the sag to equilibrium; a pose should "
      f"not need a settling transient")

# 2) the balance height is quiet.
balanced, body_balanced = tarsal_series(BALANCE, snap=False)
check(not body_balanced.airborne_seen,
      "the fly spawned at the balance height never leaves the ground",
      "grounded every step" if not body_balanced.airborne_seen else "airborne")
check(INJ(balanced) == 0,
      "the force-balance height produces NO tarsal injection",
      f"peak deviation {max(d for d, _ in balanced):.5f} "
      f"(deadband {DEADBAND / GAIN:.5f}), load settles at "
      f"{body_balanced.normal / (body_mod.MASS * body_mod.GRAVITY):.5f} body weights")
# Both halves of "no stimulus": the tarsal contact transient is gone AND the
# odour channel's standing -5 nA offset does not on its own drive the network.
silent_free = run(balanced, odour=False)
silent_with_odour = run(balanced, odour=True)
check(silent_free == 0 and silent_with_odour == 0,
      "the silence test's fixture is silent at the balance height",
      f"0 spikes / {STEPS} steps both with the odour offset standing "
      f"({silent_with_odour}) and without it ({silent_free})")

# 3) teleport snaps, so existing call sites are fixed without being rewritten.
for raw, name in [(body_mod.GROUND_Y, "y=0"),
                  (body_mod.GROUND_Y + STAND_HEIGHT, "y=standHeight"),
                  (0.2, "the app's y=0.2")]:
    snapped, b = tarsal_series(raw, snap=True)
    check(INJ(snapped) == 0 and abs(b.placed_y - BALANCE) < 1e-6,
          f"teleport snaps {name} to the balance height",
          f"placed at {b.placed_y:.5f} (balance {BALANCE:.5f}), "
          f"{INJ(snapped)} injections")

# a spawn ABOVE the substrate must be left where it was asked for.
flying, bf = tarsal_series(5.0, snap=True)
check(abs(bf.placed_y - 5.0) < 1e-6 and not bf.grounded_at_spawn,
      "a spawn above the substrate is NOT yanked to the ground",
      f"placed at {bf.placed_y:.5f}, grounded={bf.grounded_at_spawn}")

# 4) the odour offset is inert here, and pinned to source.
check(run(None, odour=True) == 0,
      "the odour offset alone does not drive the shipped asset",
      f"{0 * ODOUR_SPAN - ODOUR_BIAS:+g} nA standing yields 0 spikes / {STEPS} steps")

ok = all(checks)
print(f"\n{sum(checks)}/{len(checks)} checks passed")
print("the last 'spike at rest' was the fixture spawning the fly 0.78 mm into "
      "the floor;\nthe spawn is now a force balance, and the pose call sites "
      "are quiet without being rewritten.")
sys.exit(0 if ok else 1)