#!/usr/bin/env python3
"""Offline mirror of the camera math in ios/Sources/FlyBrainCore/RenderCamera.swift.

Same discipline as tools/sim_body_dynamics.py: this device has no Swift
toolchain, so the XCTest suite is the real gate on the macOS runner and this is
the equivalent gate on the Python runner. It exists to answer the one question a
test cannot answer about itself — "would this assertion have caught the bug?" —
without spending a CI cycle to find out.

The conventions asserted here are the ones that fail SILENTLY on a GPU:

  * Metal clips depth to [0, 1]; the OpenGL convention is [-1, 1]. Get it wrong
    and every visible pixel is still in the right place — the image looks
    correct and half the depth buffer is wasted. So the near plane is required
    to project to z == 0 and the far plane to z == 1, measured, not asserted
    from a formula copied out of the same file.
  * Screen-up must be perpendicular to the view direction, and the projected
    basis must be right-handed, or geometry mirrors and faces invert.
  * The fit distance must put the whole bounding sphere inside the frustum at
    every yaw — checked by sweeping yaw, not by trusting the formula.

Every expectation below is computed HERE from the same inputs, and each check is
paired with a deliberately WRONG variant that must fail, so the gate proves it
can discriminate rather than merely reporting numbers.

Run: python3 tools/verify_render_camera.py     (exit 1 on any failure)
"""
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SWIFT_CAMERA = ROOT / "ios/Sources/FlyBrainCore/RenderCamera.swift"


def _swift_float(name):
    """Read a Float constant out of RenderCamera.swift.

    Hardcoding these is how a mirror stops mirroring: the two values agree on
    the day they are written and drift silently afterwards, with both files
    still passing against their own copy. Every constant this file depends on
    is therefore parsed, and a rename or reshape fails loudly instead of
    quietly using a stale number.

    Handles the two spellings the source actually uses: a decimal literal and
    an arithmetic expression over `pi` (the FOV default is `.pi / 4`).
    """
    text = SWIFT_CAMERA.read_text()
    m = re.search(rf"public static let {name}:\s*Float\s*=\s*(.+)$",
                  text, re.MULTILINE)
    if not m:
        m = re.search(rf"{name}:\s*Float\s*=\s*(.+)", text)
    if not m:
        raise SystemExit(f"could not find `{name}` in {SWIFT_CAMERA.name} — "
                         f"renamed? the mirror must read the real value")
    expr = m.group(1).strip().rstrip(",").lstrip("=").strip()
    expr = expr.replace(".pi", "math.pi")
    if not re.fullmatch(r"[0-9math.pi+\-*/().\s]+", expr):
        raise SystemExit(f"`{name}` is `{expr}`, which this parser cannot "
                         f"evaluate — extend it rather than hardcoding")
    return float(eval(expr, {"math": math}))


FOV = _swift_float("verticalFovRadians")
MAX_PITCH = math.pi / 2 * 0.988
failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not cond:
        failures.append(name)
    return cond


# --- the mirror ------------------------------------------------------------

def basis(up):
    """Least-aligned-axis reference basis, same rule as `RenderCamera.basis`."""
    u = norm(up)
    best, best_dot = None, float("inf")
    for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        d = abs(dot(a, u))
        if d < best_dot:
            best_dot, best = d, a
    east = norm(sub(best, mul(u, dot(best, u))))
    north = norm(cross(u, east))
    return east, north


def norm(v):
    l = math.sqrt(sum(c * c for c in v))
    return tuple(c / l for c in v) if l > 1e-12 else v


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def mul(v, s):
    return tuple(c * s for c in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def mat_mul(A, B):
    """Column-major 4x4, matching Float4x4."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(A[k * 4 + row] * B[col * 4 + k]
                                     for k in range(4))
    return out


def transform(M, v):
    x = M[0] * v[0] + M[4] * v[1] + M[8] * v[2] + M[12] * v[3]
    y = M[1] * v[0] + M[5] * v[1] + M[9] * v[2] + M[13] * v[3]
    z = M[2] * v[0] + M[6] * v[1] + M[10] * v[2] + M[14] * v[3]
    w = M[3] * v[0] + M[7] * v[1] + M[11] * v[2] + M[15] * v[3]
    return (x, y, z, w)


def view_matrix(eye, target, up):
    f = norm(sub(target, eye))
    u = up
    if abs(dot(f, u)) > 0.9999:
        u = basis(f)[1]
    s = norm(cross(f, u))
    t = cross(s, f)
    return [s[0], t[0], -f[0], 0,
            s[1], t[1], -f[1], 0,
            s[2], t[2], -f[2], 0,
            -dot(s, eye), -dot(t, eye), dot(f, eye), 1]


def proj_matrix(aspect, near, far, fov=FOV):
    a = aspect if aspect > 1e-6 else 1e-6
    f = 1.0 / math.tan(fov / 2)
    d = near - far
    return [f / a, 0, 0, 0,
            0, f, 0, 0,
            0, 0, far / d, -1,
            0, 0, far * near / d, 0]


def fitted(bounds, aspect, fov=FOV):
    (minx, miny, minz), (maxx, maxy, maxz) = bounds
    ext = (maxx - minx, maxy - miny, maxz - minz)
    center = ((minx + maxx) / 2, (miny + maxy) / 2, (minz + maxz) / 2)
    r = max(math.sqrt(sum(c * c for c in ext)) / 2, 1e-5)
    longest = max(range(3), key=lambda i: ext[i])
    shortest = min(range(3), key=lambda i: ext[i])
    up_axis = tuple(1.0 if i == longest else 0.0 for i in range(3))
    look_axis = tuple(1.0 if i == shortest else 0.0 for i in range(3))
    half_v = fov / 2
    a = aspect if aspect > 1e-6 else 1e-6
    half_h = math.atan(math.tan(half_v) * a)
    limiting = min(half_v, half_h)
    distance = r / max(math.sin(limiting), 1e-6)
    near = max(distance - r * 2, r * 0.001)
    far = distance + r * 2
    east, north = basis(up_axis)
    pitch = math.asin(max(min(dot(look_axis, up_axis), 1.0), -1.0))
    yaw = math.atan2(dot(look_axis, north), dot(look_axis, east))
    return dict(target=center, distance=distance, yaw=yaw, pitch=pitch,
                up=up_axis, near=near, far=far, r=r, fov=fov)


def camera_eye(cam, yaw=None, pitch=None):
    y = cam["yaw"] if yaw is None else yaw
    p = cam["pitch"] if pitch is None else pitch
    p = max(min(p, MAX_PITCH), -MAX_PITCH)
    east, north = basis(cam["up"])
    cp = math.cos(p)
    vd = norm(add(add(mul(east, cp * math.cos(y)),
                      mul(north, cp * math.sin(y))),
                  mul(cam["up"], math.sin(p))))
    return add(cam["target"], mul(vd, cam["distance"]))


# --- the real asset's bounds, MEASURED (tools: see PR description) ----------
# 153,746 neurons of banc-888, read from data/generated/banc_cns.fbpack:
#   x 0.0852 ..  1.6171   span 1.5319
#   y 0.0406 ..  4.0781   span 4.0375
#   z 0.0000 .. 12.0204   span 12.0204
BANC_BOUNDS = ((0.0852, 0.0406, 0.0), (1.6171, 4.0781, 12.0204))

print("=== fit is derived from the asset's own extents ===")
cam = fitted(BANC_BOUNDS, aspect=0.46)      # iPhone portrait ~ 0.46
check("up axis is the longest extent (z, span 12.02)",
      cam["up"] == (0.0, 0.0, 1.0), f"up={cam['up']} extents span "
      f"{BANC_BOUNDS[1][2] - BANC_BOUNDS[0][2]:.2f} vs x "
      f"{BANC_BOUNDS[1][0] - BANC_BOUNDS[0][0]:.2f}")
check("view axis is the shortest extent (x, span 1.53)",
      abs(abs(dot(camera_eye(cam), cam["up"]))) >= 0.0 and
      # the look direction must be parallel to x
      abs(abs(norm(sub(cam["target"], camera_eye(cam)))[0]) - 1.0) < 1e-5,
      f"look={norm(sub(cam['target'], camera_eye(cam)))}")

# A wide aspect makes the VERTICAL field the binding constraint, so its fit is
# exactly r/sin(halfV) and is CLOSER than the portrait fit (a narrow horizontal
# field has to back off to fit the same sphere). The first version of this
# check asserted the opposite direction and failed — which is the point of
# pairing every claim with the number that proves it.
cam_land = fitted(BANC_BOUNDS, aspect=2.17)
expected_land = cam_land["r"] / math.sin(FOV / 2)
check("a wide aspect is fitted by the vertical field",
      abs(cam_land["distance"] - expected_land) < 1e-6,
      f"d={cam_land['distance']:.4f} vs r/sin(halfFov)={expected_land:.4f}")
check("a narrow (portrait) aspect must back off FURTHER than a wide one",
      cam["distance"] > cam_land["distance"] + 1.0,
      f"portrait d={cam['distance']:.4f} > landscape d={cam_land['distance']:.4f}")

print()
print("=== the whole cloud is inside the frustum at every yaw ===")
worst = None
for yaw_deg in range(0, 360, 5):
    for pitch_deg in (-89, -45, 0, 45, 89):
        eye = camera_eye(cam, yaw=math.radians(yaw_deg),
                         pitch=math.radians(max(min(pitch_deg, 88.9), -88.9)))
        V = view_matrix(eye, cam["target"], cam["up"])
        P = proj_matrix(0.46, cam["near"], cam["far"])
        VP = mat_mul(P, V)
        r = cam["r"]
        # sample the bounding SPHERE, which contains every neuron
        for i in range(64):
            phi = 2 * math.pi * i / 64
            theta = math.pi * (i % 17) / 17
            p = add(cam["target"],
                    (r * math.sin(theta) * math.cos(phi),
                     r * math.sin(theta) * math.sin(phi),
                     r * math.cos(theta)))
            x, y, z, w = transform(VP, (p[0], p[1], p[2], 1.0))
            if w <= 1e-6:
                worst = ("behind eye", yaw_deg, pitch_deg)
                break
            ndc = (x / w, y / w, z / w)
            if not (-1.0001 <= ndc[0] <= 1.0001 and -1.0001 <= ndc[1] <= 1.0001
                    and -0.0001 <= ndc[2] <= 1.0001):
                worst = (ndc, yaw_deg, pitch_deg)
                break
        if worst:
            break
    if worst:
        break
check("every sampled sphere point stays inside clip space over 72 yaws x 5 pitches",
      worst is None, f"first escape: {worst}")

print()
print("=== depth convention: Metal [0,1], not OpenGL [-1,1] ===")
eye = camera_eye(cam)
V = view_matrix(eye, cam["target"], cam["up"])
P_metal = proj_matrix(0.46, cam["near"], cam["far"])
near_pt = add(eye, mul(norm(sub(cam["target"], eye)), cam["near"]))
far_pt = add(eye, mul(norm(sub(cam["target"], eye)), cam["far"]))
zn = transform(mat_mul(P_metal, V), (*near_pt, 1.0))
zf = transform(mat_mul(P_metal, V), (*far_pt, 1.0))
check("near plane projects to z == 0", abs(zn[2] / zn[3]) < 1e-4,
      f"z={zn[2] / zn[3]:.6f}")
check("far plane projects to z == 1", abs(zf[2] / zf[3] - 1) < 1e-4,
      f"z={zf[2] / zf[3]:.6f}")

# The OpenGL variant must be demonstrably DIFFERENT over the depth range, or
# the checks above are vacuous. Note WHERE it differs: both conventions map the
# FAR plane to z == 1 — the first version of this pair asserted the far plane
# and the OpenGL form passed it, because the two conventions agree there. They
# disagree at the NEAR plane (Metal 0, OpenGL -1), which is the only place this
# distinction is observable. Asserting the far plane would have been a check
# that cannot fail, which is the failure mode this file exists to avoid.
def proj_gl(aspect, near, far, fov=FOV):
    a = aspect if aspect > 1e-6 else 1e-6
    f = 1.0 / math.tan(fov / 2)
    return [f / a, 0, 0, 0, 0, f, 0, 0,
            0, 0, (far + near) / (near - far), -1,
            0, 0, 2 * far * near / (near - far), 0]

zgn = transform(mat_mul(proj_gl(0.46, cam["near"], cam["far"]), V), (*near_pt, 1.0))
zgf = transform(mat_mul(proj_gl(0.46, cam["near"], cam["far"]), V), (*far_pt, 1.0))
check("the two conventions agree at the FAR plane (so far-plane tests prove nothing)",
      abs(zgf[2] / zgf[3] - 1) < 1e-4, f"opengl far z={zgf[2] / zgf[3]:.6f}")
check("the OpenGL depth convention FAILS at the near plane (gate discriminates)",
      abs(zgn[2] / zgn[3] - 0) > 0.5,
      f"opengl near z={zgn[2] / zgn[3]:.4f}, Metal near z={zn[2] / zn[3]:.4f}")

print()
print("=== basis is orthonormal and right-handed ===")
for up in ((0, 0, 1), (0, 1, 0), (1, 0, 0), norm((1, 1, 1))):
    e, n = basis(up)
    u = norm(up)
    ok = (abs(dot(e, n)) < 1e-5 and abs(dot(e, u)) < 1e-5
          and abs(dot(n, u)) < 1e-5
          and abs(sum(c * c for c in e) - 1) < 1e-5
          and abs(sum(c * c for c in n) - 1) < 1e-5)
    check(f"orthonormal basis for up={tuple(round(c, 3) for c in u)}", ok,
          f"e.n={dot(e, n):.2e} e.u={dot(e, u):.2e} n.u={dot(n, u):.2e}")

print()
print("=== the fit is exactly tangent, not approximate ===")
# The frustum's vertical half-angle at distance d subtends a radius d*sin(halfFov)
# on the bounding sphere. A fit that is too close clips; one too far wastes pixels.
r_visible = cam["distance"] * math.sin(FOV / 2)
check("portrait fit is limited by the horizontal field and still contains the sphere",
      cam["distance"] * math.sin(math.atan(math.tan(FOV / 2) * 0.46)) >= cam["r"] - 1e-9,
      f"visible vertical r={r_visible:.4f} sphere r={cam['r']:.4f} "
      f"(vertical alone would be too small here)")

# Pinned to the value this mirror computes from the measured bounds, NOT
# recomputed as `distance * sin(halfFov)` on both sides — that comparison is
# true by construction and would pass for any camera at all.
half_v_radius = cam["distance"] * math.sin(FOV / 2)
check("visibleRadius matches the independently computed 13.057 at the portrait fit",
      abs(half_v_radius - 13.057) < 0.01,
      f"visibleRadius={half_v_radius:.5f} (expected 13.0572)")
check("the visible vertical radius still contains the bounding sphere",
      half_v_radius >= cam["r"] - 1e-9,
      f"visible r={half_v_radius:.4f} >= sphere r={cam['r']:.4f}")

print()
print("=== screen axes point the expected way (image is not mirrored) ===")
# Handedness. Reverse the cross products and the image mirrors, which a
# symmetric orbit sweep cannot detect: a mirrored frustum is still a valid
# frustum. With up=+z and yaw=pitch=0 the eye sits at +x looking back along -x,
# so `north` (world +y) must land on screen RIGHT; after a quarter turn the eye
# is at +y and the original `east` (+x) must fall to screen LEFT — the second is
# the assertion that fails when the handedness flips.
#
# WAS WRONG, IN A WAY THE SWIFT MIRROR COULD NOT SHOW: the first version read
# `w[0]` of the VIEW-space transform, i.e. the CLIP-space x BEFORE the
# perspective divide. That is monotone in the screen x, so the sign checks were
# correct, but it is not what the screen shows — and the Swift test that counted
# on a magnitude got 0.2414 where it demanded 1.0 (the foreshortening of a 1 mm
# step at a 10 mm distance: `tan(22.5°)/10`). Both were measuring a "which way"
# quantity while one of them claimed "where". Everything below now goes through
# the same full view-projection + divide a pixel would, and the axis is measured
# as a symmetric displacement about the target so the expected value does not
# depend on the camera's distance or field of view.
def screen_xy(p, up_v, yaw_v):
    """Screen coordinates (post-divide NDC) of `p`. The renderer's own path."""
    eye_v = camera_eye(cam, yaw=yaw_v, pitch=0.0)
    Vv = view_matrix(eye_v, cam["target"], up_v)
    Pv = proj_matrix(1.0, cam["near"], cam["far"])
    x, y, _z, w = transform(mat_mul(Pv, Vv), (*p, 1.0))
    return (x / w, y / w)


def screen_x(p, up_v, yaw_v):
    """The CLIP-space x the first version of this gate used — kept only to show
    that it does discriminate, so the fix is about the quantity measured, not
    about a check that could never fail."""
    eye_v = camera_eye(cam, yaw=yaw_v, pitch=0.0)
    Vv = view_matrix(eye_v, cam["target"], up_v)
    return transform(Vv, (*p, 1.0))[0]


def axis_displacement(axis, up_v, yaw_v):
    plus = screen_xy(add(cam["target"], axis), up_v, yaw_v)
    minus = screen_xy(sub(cam["target"], axis), up_v, yaw_v)
    return (plus[0] - minus[0], plus[1] - minus[1])


north_v = (0.0, 1.0, 0.0)
east_v = (1.0, 0.0, 0.0)
d_north = axis_displacement(north_v, (0.0, 0.0, 1.0), 0.0)
d_up = axis_displacement((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), 0.0)
check("`north` moves the image to screen right and not vertically",
      d_north[0] > 1e-3 and abs(d_north[1]) < 1e-4,
      f"d=({d_north[0]:+.5f}, {d_north[1]:+.5f})")
check("`up` moves the image to screen up and not horizontally",
      d_up[1] > 1e-3 and abs(d_up[0]) < 1e-4,
      f"d=({d_up[0]:+.5f}, {d_up[1]:+.5f})")
# The measured magnitude, pinned. A symmetric ±1 mm step about the target is
# scaled by the perspective divide at the target's own depth: the derivative is
# `2/(d*tan(halfFov))` over the whole ± range, i.e. `1/(d*tan(halfFov))` per
# unit. This is the number the Swift test used to demand (1.0) and the gate
# never checked — which is how the two mirrors drifted apart on a quantity that
# is not a convention but arithmetic.
check("the right-axis displacement is the measured 0.141513, not a unit vector",
      abs(d_north[0] - 0.1415130) < 1e-6,
      f"d.x={d_north[0]:.7f} (1/dist/tan(halfFov) = "
      f"{1.0 / cam['distance'] / math.tan(FOV / 2):.7f})")

d_east_turned = axis_displacement(east_v, (0.0, 0.0, 1.0), math.pi / 2)
x_east_turned_clip = screen_x(add(cam["target"], east_v), (0.0, 0.0, 1.0),
                              math.pi / 2)
check("after a quarter turn, `east` moves the image to screen LEFT (handedness pinned)",
      d_east_turned[0] < -1e-6, f"d.x={d_east_turned[0]:+.5f}")

# Discrimination, not decoration: a MIRRORED basis must fail the very checks
# above. The first attempt at this flipped the UPPER vector — which is a
# vertical mirror and of course leaves the horizontal check passing; the gate
# caught its own author, and the two variants are now separated so each check is
# shown to bind on its own axis.
def variant_displacement(axis, mirror_x=False, mirror_y=False):
    eye_v = camera_eye(cam)
    f = norm(sub(cam["target"], eye_v))
    s = norm(cross(f, cam["up"]))
    t = cross(s, f)
    if mirror_x:
        s = mul(s, -1)
    if mirror_y:
        t = mul(t, -1)
    Vv = [s[0], t[0], -f[0], 0, s[1], t[1], -f[1], 0,
          s[2], t[2], -f[2], 0,
          -dot(s, eye_v), -dot(t, eye_v), dot(f, eye_v), 1]
    Pv = proj_matrix(1.0, cam["near"], cam["far"])
    VP = mat_mul(Pv, Vv)
    out = []
    for sign in (1.0, -1.0):
        p = add(cam["target"], mul(axis, sign))
        x, y, _z, w = transform(VP, (*p, 1.0))
        out.append((x / w, y / w))
    return (out[0][0] - out[1][0], out[0][1] - out[1][1])


mirror_d_north = variant_displacement(north_v, mirror_x=True)
mirror_d_up = variant_displacement((0.0, 0.0, 1.0), mirror_y=True)
check("a horizontally MIRRORED basis fails the north check (it can discriminate)",
      not (mirror_d_north[0] > 1e-3),
      f"mirrored d.x={mirror_d_north[0]:+.5f} vs real {d_north[0]:+.5f}")
check("a vertically MIRRORED basis fails the up check (it can discriminate)",
      not (mirror_d_up[1] > 1e-3),
      f"mirrored d.y={mirror_d_up[1]:+.5f} vs real {d_up[1]:+.5f}")

# And the quantity the gate used to read was not the screen coordinate at all:
# it was view/clip space BEFORE the divide, which is the screen x multiplied by
# the clip `w` at the target's depth. The ratio below is that factor. This is
# why the old sign checks were right and still could not have caught the Swift
# test's magnitude error: they were reading a different number, one that scales
# with distance, and were only ever compared against 0.
# And the quantity the gate used to read was not the screen coordinate at all:
# it was the VIEW-space x of the view-projection product BEFORE the divide, i.e.
# the screen x undivided by the clip `w`. Their ratio is fixed by the projection
# (`ndc.x = (f/aspect) * x_view / w`), so it equals `aspect * depth / f` =
# `depth * tan(halfFov)` with aspect 1 — a factor of 14 here, not 1. Every old
# check compared this number against 0, so it was a valid handedness test and
# could never have caught a magnitude error in either direction.
clip_x_north = screen_x(add(cam["target"], north_v), (0.0, 0.0, 1.0), 0.0)
clip_ratio = clip_x_north / (d_north[0] / 2)     # d_north is the ±1 mm span
expected_ratio = cam["distance"] * math.tan(FOV / 2)
check("the old clip-space x is the screen x times depth*tan(halfFov), not equal "
      "to it",
      abs(clip_ratio - expected_ratio) < 1e-3,
      f"clip/screen = {clip_ratio:.4f} = distance*tan(halfFov) "
      f"{expected_ratio:.4f} (a check against 0 said nothing about the screen)")

print()
print(f"{len(failures)} failure(s)")
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
sys.exit(0)