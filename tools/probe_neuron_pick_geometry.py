#!/usr/bin/env python3
"""Does the tap-pick fixture in NeuronInspectionTests.swift actually separate
the neurons it claims to?

WHY THIS EXISTS
---------------
`NeuronInspectionTests` aims at a known neuron by projecting the fixture's own
instance and tapping that NDC point, then asserts the pick returns that neuron.
That is only meaningful if the neurons project to DIFFERENT points: if they all
project to one point, "nearest to the tap" is decided by depth, every assertion
passes for a reason the test never states, and the suite reports confidence it
has not earned.

The first version of that fixture put the neurons in a row along x, in front of
a camera looking down -x (`RenderCamera.basis(forUp: +y)` resolves east to +z,
so yaw=0 puts the eye on +x). A row along x is a row along the VIEW axis, so all
of its points projected to the same NDC point. The comment claimed the camera
looked down -z. Nothing in the suite noticed.

This probe answers the question with the camera's own matrix code rather than a
second copy of it: it executes the matrix helpers at the top of
`verify_render_camera.py` (that file mirrors `RenderCamera.swift`), stopping
before its own asset-bounds block. If that marker is ever renamed the probe
fails loudly instead of silently checking a truncated mirror.

Run: python3 tools/probe_neuron_pick_geometry.py     (exit 1 on any failure)
"""

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAMERA_MIRROR = ROOT / "tools/verify_render_camera.py"
# Everything above this marker in the mirror is pure helpers; everything below
# is that file's own checks, which would print here and exit this process.
MARKER = "# --- the real asset's bounds, MEASURED"

source = CAMERA_MIRROR.read_text()
if MARKER not in source:
    raise SystemExit(f"marker not found in {CAMERA_MIRROR.name} — the mirror "
                     f"was reshaped; update MARKER rather than deleting the check")
helpers = source.split(MARKER)[0]

namespace = {"__name__": "camera_mirror_helpers",
             # the helper block resolves its own paths from __file__
             "__file__": str(CAMERA_MIRROR)}
exec(compile(helpers, str(CAMERA_MIRROR), "exec"), namespace)

norm = namespace["norm"]
dot = namespace["dot"]
sub = namespace["sub"]
add = namespace["add"]
mul = namespace["mul"]
basis = namespace["basis"]
mat_mul = namespace["mat_mul"]
transform = namespace["transform"]
view_matrix = namespace["view_matrix"]
proj_matrix = namespace["proj_matrix"]
camera_eye = namespace["camera_eye"]
MAX_PITCH = namespace["MAX_PITCH"]

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not cond:
        failures.append(name)
    return cond


# The test fixture's camera: RenderCameraTests-style explicit construction.
# yaw = pitch = 0, up = +y, distance = 10, fov 1.0, near 0.1, far 1000.
CAM = dict(target=(0.0, 0.0, 0.0), distance=10.0, yaw=0.0, pitch=0.0,
           up=(0.0, 1.0, 0.0), near=0.1, far=1000.0, fov=1.0)
ASPECT = 1.0


def vp(cam, aspect=ASPECT):
    eye = camera_eye(cam)
    v = view_matrix(eye, cam["target"], cam["up"])
    p = proj_matrix(aspect, cam["near"], cam["far"], cam["fov"])
    return mat_mul(p, v), eye


def project(cam, point, aspect=ASPECT):
    m, _ = vp(cam, aspect)
    r = transform(m, (point[0], point[1], point[2], 1.0))
    if abs(r[3]) <= 1e-9:
        return None
    return (r[0] / r[3], r[1] / r[3], r[2] / r[3])


def ncd(a, b):
    """NDC distance, which is what `maxNDCDistance` is measured in."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --- 1. which way does this camera actually look? ---------------------------
eye = camera_eye(CAM)
check("fixture camera eye is on +x (so the view axis is -x)",
      all(abs(a - b) < 1e-6 for a, b in zip(eye, (10.0, 0.0, 0.0))),
      f"eye = {tuple(round(c, 4) for c in eye)}")
east = basis(CAM["up"])[0]
check("basis(forUp: +y) resolves east to +x — which IS the view axis at yaw=0",
      abs(east[0] - 1.0) < 1e-9 and abs(east[2]) < 1e-9,
      f"east = {tuple(round(c, 4) for c in east)}; that is why an x-row is a view-axis row")


# --- 2. the shipped fixture: a row along x --------------------------------
# `rowConnectome` places neuron i at (i, 0, 0).
ROW_X = [(float(i), 0.0, 0.0) for i in range(4)]
proj_row_x = [project(CAM, p) for p in ROW_X]
check("every neuron of the x-row projects to a finite point",
      all(p is not None for p in proj_row_x),
      f"{[None if p is None else tuple(round(c, 4) for c in p) for p in proj_row_x]}")
# The claim that is FALSE as written: if any two land on the same NDC point,
# "aim at neuron i, expect neuron i" is decided by depth, not by the row.
closest_pair = min(
    ((ncd(proj_row_x[i], proj_row_x[j]), i, j)
     for i in range(4) for j in range(i + 1, 4)),
    key=lambda t: t[0])
degenerate = closest_pair[0] < 1e-6
check("x-row DOES collapse to one NDC point (this is the fixture bug)",
      degenerate,
      f"closest pair {closest_pair[1]},{closest_pair[2]} at NDC distance "
      f"{closest_pair[0]:.3e}")
if degenerate:
    print("       => the suite's comment claims the camera looks down -z. It does not;")
    print("          the row runs along the view axis, so 'aim at i' == 'aim at all of them'.")


# --- 3. the corrected fixture: a row that is not along the view axis -------
# Diagonal in +x+y+z, so consecutive neurons differ in DEPTH (hierarchical
# separation) as well as on screen: aiming at neuron i cannot reach neuron i+1
# with a tolerance tighter than half their NDC gap.
ROW_DIAG = [(float(i), float(i), float(i)) for i in range(4)]
proj_diag = [project(CAM, p) for p in ROW_DIAG]
gaps = [ncd(proj_diag[i], proj_diag[i + 1]) for i in range(3)]
check("diagonal row projects to strictly separated, monotone NDC points",
      all(g is not None for g in proj_diag)
      and all(gaps[i] < gaps[i + 1] for i in range(2))
      and all(g > 0.02 for g in gaps),
      f"consecutive NDC gaps = {[round(g, 4) for g in gaps]}")

if all(p is not None for p in proj_diag):
    half_min = min(gaps) / 2
    # Distance from a tap on neuron i's own point to every OTHER neuron.
    nearest_other = min(
        min(ncd(proj_diag[i], proj_diag[j]) for j in range(4) if j != i)
        for i in range(4))
    check("half the smallest gap excludes every neighbour (depth-shortest case)",
          nearest_other > half_min,
          f"nearest other neuron is {nearest_other:.4f} NDC away, "
          f"half-gap tolerance is {half_min:.4f}")
    # The 'step away' test: aiming at neuron 0 and stepping along +x in NDC,
    # the nearest must remain neuron 0 until the step reaches the tolerance.
    LIMIT = 0.05
    first_miss = None
    for step in [i * 0.002 for i in range(101)]:
        tap = (proj_diag[0][0] + step, proj_diag[0][1])
        d = [ncd(tap, p) for p in proj_diag]
        in_reach = [i for i, x in enumerate(d) if x <= LIMIT]
        if not in_reach:
            first_miss = step
            break
    check("tolerance cutoff lands on the limit asked for, not before",
          first_miss is not None and abs(first_miss - LIMIT) <= 0.006,
          f"first miss at {first_miss} NDC for a {LIMIT} tolerance")
    if first_miss is not None:
        # and the nearest at that moment is still neuron 0, so the assertion
        # 'the pick did not wander' is about the tolerance, not about a
        # neighbour stealing the hit.
        tap = (proj_diag[0][0] + (first_miss - 0.002), proj_diag[0][1])
        nearest = min(range(4), key=lambda i: ncd(tap, proj_diag[i]))
        check("the pick stays on neuron 0 until the tolerance runs out",
              nearest == 0, f"nearest at the last hit was neuron {nearest}")


# --- 4. the behind-the-camera neuron --------------------------------------
# The eye sits at x = 10 looking down -x, so "behind the camera" means x > 10.
# The SHIPPED test used (0, 0, 500): x = 0 is 10 in FRONT of the eye, so that
# neuron is merely far off-axis (ndc.x ≈ -91), not behind. It is never picked
# because it is 91 NDC units away, which means the test passed without the
# frustum check doing anything — the comment claimed otherwise.
BEHIND = (500.0, 0.0, 0.0)
FRONT = (0.0, 0.0, 0.0)
pb = project(CAM, BEHIND)
pf = project(CAM, FRONT)
check("the behind-camera neuron projects to the SAME screen point as a front one",
      pb is not None and pf is not None
      and abs(pb[0] - pf[0]) < 1e-6 and abs(pb[1] - pf[1]) < 1e-6,
      f"front ndc = ({pf[0]:.4f}, {pf[1]:.4f}, {pf[2]:.4f}); "
      f"behind ndc = ({pb[0]:.4f}, {pb[1]:.4f}, {pb[2]:.4f})")
check("its ndc z is outside [0, 1], which is the check that must reject it",
      not (0.0 <= pb[2] <= 1.0) and (0.0 <= pf[2] <= 1.0),
      f"z_behind = {pb[2]:.4f}, z_front = {pf[2]:.4f}")
print("       => reject-on-z is a real discriminator, but ONLY for a point at x > 10.")

# What the shipped fixture's point really was, for the record.
p_offaxis = project(CAM, (0.0, 0.0, 500.0))
check("the shipped (0,0,500) neuron is off-axis, not behind: |ndc.x| >> 1",
      p_offaxis is not None and abs(p_offaxis[0]) > 1.0
      and 0.0 <= p_offaxis[2] <= 1.0,
      f"ndc = ({p_offaxis[0]:.2f}, {p_offaxis[1]:.2f}, {p_offaxis[2]:.4f}) — "
      f"inside the z range, so the frustum check never fires for it")


# --- 5. discrimination: what would the old fixture actually have done? -----
# Simulate the old pick (nearest in NDC, same tie-break the real code uses:
# lower index wins) against the old fixture, and report the outcome rather
# than assuming it. Measured: every tap lands on neuron 0, so the three
# assertions "aimed at i, picked i" for i = 1, 2, 3 FAIL — the degenerate
# fixture is caught by the suite, loudly, and is not a silent pass. The suite's
# COMMENT is wrong either way: the camera does not look down -z, and the
# "nearest point" the pick searches is the same point for all four neurons.
#
# `cam` is a parameter and not the module-level CAM: an earlier version of this
# probe captured CAM here, so the real-asset section below silently projected
# with the wrong camera and reported `None` as a measured result. A helper that
# reaches out for its own inputs is how a gate ends up testing nothing.
def pick(neurons, tap, limit=0.5, cam=None, aspect=1.0):
    cam = CAM if cam is None else cam
    best = None
    for i, p in enumerate(neurons):
        pr = project(cam, p, aspect)
        if pr is None or not (0.0 <= pr[2] <= 1.0):
            continue
        d2 = (pr[0] - tap[0]) ** 2 + (pr[1] - tap[1]) ** 2
        if d2 > limit * limit:
            continue
        if best is None or d2 < best[0] or (d2 == best[0] and i < best[1]):
            best = (d2, i)
    return None if best is None else best[1]


proj_x = [project(CAM, p) for p in ROW_X]
legacy_results = [pick(ROW_X, p, 0.5) for p in proj_x]
check("on the degenerate x-row the suite FAILS loudly (picks 0,0,0,0)",
      legacy_results == [0, 0, 0, 0],
      f"picks = {legacy_results}; assertions for i=1,2,3 would fail, so this "
      f"fixture is caught by CI rather than passing for a hidden reason")
# NOTE: `pick(neurons, tap)` projects the NEURONS itself, so the tap must be
# the already-projected point. Re-projecting it (an earlier version of this
# probe did) feeds an NDC triple in as a world position and tests nothing.
#
# The suite's pick test sweeps aspect over {1, 1.5, 0.6}; the probe must not
# assert separation at one aspect and leave the others unchecked.
for aspect in (1.0, 1.5, 0.6):
    pr = [project(CAM, p, aspect) for p in ROW_DIAG]
    if any(p is None for p in pr):
        check(f"diagonal row projects at aspect {aspect}", False, "a point was rejected")
        continue
    g = [ncd(pr[i], pr[i + 1]) for i in range(3)]
    got = [pick(ROW_DIAG, p, 0.1, cam=CAM, aspect=aspect) for p in pr]
    check(f"diagonal row stays separated and pickable at aspect {aspect}",
          all(x > 0.02 for x in g) and got == [0, 1, 2, 3],
          f"gaps = {[round(x, 4) for x in g]}, picks = {got}")

diag_results = [pick(ROW_DIAG, p, 0.5) for p in proj_diag]
check("a half-screen tolerance still picks correctly on the separated row",
      diag_results == [0, 1, 2, 3],
      f"picks = {diag_results} at limit 0.5 — separation is what saves it here, "
      f"not the tolerance; the tolerance only becomes binding below the gap")
tight = [pick(ROW_DIAG, p, 0.1) for p in proj_diag]
check("diagonal row + tolerance 0.1 gives 0,1,2,3 for the stated reason",
      tight == [0, 1, 2, 3],
      f"picks = {tight} at limit 0.1, below the smallest gap ({min(gaps):.4f}), "
      f"so only the aimed neuron is within reach")
# Where does the tolerance actually matter? Measured answer: not for an exact
# tap. Tapping a neuron's own projected point gives distance 0 to that neuron,
# so it wins at ANY tolerance — screen separation is what makes that true, the
# tolerance never binds. A version of this probe asserted the picks break once
# the limit passes the tightest gap; they do not, and the assertion was wrong.
exact_wide = [pick(ROW_DIAG, p, 0.5) for p in proj_diag]
exact_tight = [pick(ROW_DIAG, p, 0.001) for p in proj_diag]
check("an exact tap on a neuron is decided by screen separation, not tolerance",
      exact_wide == [0, 1, 2, 3] and exact_tight == [0, 1, 2, 3],
      f"limit 0.5 -> {exact_wide}; limit 0.001 -> {exact_tight}")
# The tolerance DOES bind for a tap that lands between two neurons, which is
# the case the user actually produces. Half the gap is where it switches.
mid_x = (proj_diag[0][0] + proj_diag[1][0]) / 2
mid_y = (proj_diag[0][1] + proj_diag[1][1]) / 2
mid = (mid_x, mid_y)
half_gap = gaps[0] / 2
inside = pick(ROW_DIAG, mid, half_gap + 0.001)
outside = pick(ROW_DIAG, mid, half_gap - 0.001)
check("a tap BETWEEN two neurons is where the tolerance decides",
      inside is not None and outside is None,
      f"between neurons 0 and 1 ({half_gap:.4f} NDC from each): limit "
      f"{half_gap + 0.001:.4f} -> picks {inside}; limit {half_gap - 0.001:.4f} -> "
      f"{outside} (both rejected: neither is within reach)")
check("the probe discriminates: on the degenerate row this same tap gives 0",
      pick(ROW_X, (proj_x[0][0], proj_x[0][1]), half_gap + 0.001) == 0,
      "a tap between the four coincident cells returns the depth-nearest one")

# Deliberately wrong variant: if the corrected fixture were ALSO degenerate,
# the same assertions would collapse — the probe must be able to tell the two
# apart, or it proves nothing.
check("the probe discriminates: a degenerate diagonal would NOT give 0,1,2,3",
      pick(ROW_X, project(CAM, ROW_X[0]), 0.5) == 0
      and [pick(ROW_X, project(CAM, ROW_X[i]), 0.5) for i in (1, 2, 3)] != [1, 2, 3],
      "same call shape, degenerate input: the assertion pattern differs")


# --- 6. the fit camera vs the fixture camera: same axis, different basis ----
# `RenderCamera.fitted` puts screen-up on the LONGEST extent axis (z for the
# shipped asset) and the eye along the SHORTEST one (x). On the shipped BANC
# asset that eye lands on +x just like the fixture camera, but through a
# different route: up=+z with yaw=0, not up=+y with yaw=pi/2. Asserting the
# yaw value would have pinned the wrong thing; what matters is the OPTIC AXIS.
BANC_BOUNDS = ((0.0852, 0.0406, 0.0), (1.6171, 4.0781, 12.0204))
fit = namespace["fitted"](BANC_BOUNDS, 1.0)
fit_eye = camera_eye(fit)
look = norm(sub(fit["target"], fit_eye))
check("the fitted BANC camera's optic axis is -x, the same axis as the fixture",
      abs(dot(look, (-1.0, 0.0, 0.0)) - 1.0) < 1e-6,
      f"look = {tuple(round(c, 4) for c in look)}; fitted up = "
      f"{tuple(round(c, 3) for c in fit['up'])} (the longest axis), yaw = {fit['yaw']:.4f}")

# The fixture camera has hardcoded near/far (0.1/1000); `fitted` derives them
# from the asset so the whole cloud stays inside the depth range at any yaw.
span = [(0.0852 + 1.5319 * t / 8, 2.0, 6.0) for t in range(8)]
fit_proj = [project(fit, p, 1.0) for p in span]
inside = [p for p in fit_proj if p is not None and 0.0 <= p[2] <= 1.0]
check("the fitted camera keeps the whole asset inside z in [0, 1]",
      len(inside) == len(span),
      f"{len(inside)} of {len(span)} points inside — the depth range is derived "
      f"from the bounds, so the pick's z-rejection never fires on a framed asset")

# But a row along the OPTIC axis does not COLLAPSE on the real asset: the cells
# sit at different depths, so the 0.01 mm-scale perspective shrink makes their
# ndc.x differ in the 4th decimal. They are effectively coincident rather than
# identical, and the measured spread is what a tolerance has to beat — so
# report the spread and show that a normal tolerance cannot separate them.
xs = sorted(round(p[0], 9) for p in fit_proj if p is not None)
spread = max(xs) - min(xs)
check("an optic-axis row keeps 8 nearly-coincident screen points",
      len(set(xs)) == len(xs) and spread < 0.002,
      f"8 distinct ndc.x values spanning only {spread:.6f} NDC "
      f"({xs[0]:.6f} .. {xs[-1]:.6f}) — they differ by perspective alone, not "
      f"by position across the screen")
# With any usable tap tolerance only the nearest-in-depth cell can be selected,
# which is the point: on screen these cells are one dot. The picker must be
# given the fitted camera here — the fixture camera's near/far would reject
# every point of a 12 mm asset and report `None`, which looks like a bug in the
# picker but is a bug in the probe. The tap must also take BOTH coordinates
# from the same cell; mixing x from one and y from another (an earlier version
# did) taps a point that belongs to no neuron.
far = fit_proj[7]
with_picker = pick(span, (far[0], far[1]), 0.04, cam=fit)
check("a tap on the optic-axis row returns that row's own cell",
      with_picker == 7,
      f"tap at ({far[0]:.6f}, {far[1]:.6f}) picks index {with_picker}; the other "
      f"7 cells sit within {spread:.6f} NDC of the tap, so they are all in reach "
      f"and depth decides — the row is one dot on screen")
# Now the case that shows it is not a distinction the user can make: tapping
# where cell 0 appears gives cell 0, because position still breaks its own tie.
near = fit_proj[0]
check("and a tap on the row's other end returns the other cell",
      pick(span, (near[0], near[1]), 0.04, cam=fit) == 0,
      f"tap at ({near[0]:.6f}, {near[1]:.6f}) picks index "
      f"{pick(span, (near[0], near[1]), 0.04, cam=fit)}")
print("       => the corrected fixture separates neurons in DEPTH, giving")
print("          consecutive cells different ndc.x; the old x-row did not.")

# --- 7. HOW THIS IS EVIDENCED (and what it does NOT evidence) --------------
# Every number above is computed on a Python reimplementation of the picker's
# rule — this probe does not execute Swift. What runs the real code is
# `NeuronInspectionTests` on the macOS runner. So this probe's job is narrower:
# it shows the FIXTURE geometry is what the assertions need, using the same
# camera matrix, and it must therefore pin the Swift it describes. If the
# frustum check, the tie-break, or the diagonal fixture is edited away, the
# claims above quietly become claims about code that no longer exists.
SWIFT_PICKER = ROOT / "ios/Sources/FlyBrainCore/NeuronInspection.swift"
SWIFT_TESTS = ROOT / "ios/Tests/FlyBrainCoreTests/NeuronInspectionTests.swift"
picker_src = SWIFT_PICKER.read_text()
tests_src = SWIFT_TESTS.read_text()

check("the picker still rejects points outside the depth range",
      "ndcP.z >= 0, ndcP.z <= 1" in picker_src,
      "the frustum guard this probe's z-rejection mirrors is present")
check("the picker still breaks ties by lower dense index",
      "Int(inst.index) < Int(b.instance.index)" in picker_src,
      "the tie-break this probe's `pick()` mirrors is present")
check("the test fixture still places neurons on the diagonal",
      "x: Float(i), y: Float(i), z: Float(i)" in tests_src,
      "a row along x is a row down the optic axis — the bug this probe measures")
check("the test fixture still claims the old -z view axis nowhere",
      "view axis is -z" not in tests_src,
      "the corrected comment describes the measured -x axis")
print("       => this probe is a mirror pinned to its source, not an execution")
print("          of it: a rename breaks the mirror loudly, but the semantic")
print("          gate is the XCTest suite in CI.")

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("all checks passed — the corrected fixture separates the neurons at every")
print("aspect the suite uses, and the old one is shown to FAIL LOUDLY rather than")
print("pass for a reason the suite never stated (its pinning comment was wrong).")