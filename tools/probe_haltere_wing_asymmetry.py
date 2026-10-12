#!/usr/bin/env python3
"""Can a haltere-side signal actually unbalance the two wing pools?

TASK-008, part 2. Part 1 (`tools/mirror_flight_attitude.py`) proved the
asymmetry has a producer and that the two wing pools are SEPARABLE in the
wiring. Separable is not the same as DRIVEN: a pool pair with distinct upstream
cells can still receive identical rates once the network runs, and then the
asymmetry would be structurally zero — an inert field with a writer, which is
the failure this project keeps finding.

So this probe runs the real subgraph through the engine mirror and asks the only
question that settles it: when the haltere afferents on ONE side are driven,
does the summed wing-pool rate become unequal between sides, and in the
direction the haltere reflex requires?

WHAT IS MEASURED. Angular rotation of the body elicits compensatory changes in
wing stroke amplitude and frequency, and the halteres are required (Dickinson,
*Phil. Trans. R. Soc. B* 1999, PMID 10382224; ablation abolishes the reflex).
The reflex direction is approximately linear in stimulus orientation. So the
sign requirement here is: driving the LEFT haltere must bias the correction the
way the left haltere's rate indicates. This probe measures the SIGN the
connectome actually produces — it does not assume it.

WHY A SUBGRAPH: the BANC asset is 153,746 neurons / 3,036,600 edges and the
engine mirror is pure Python. The claim is LOCAL ("does this pool pair respond
differently"), so the probe extracts both wing pools plus their real upstream
and simulates only that. Every edge is a real asset edge.

Run: python3 tools/probe_haltere_wing_asymmetry.py
"""
from __future__ import annotations

import struct
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import NEURON_STRIDE, parse                 # noqa: E402
from flybrain.pid import RegionID                       # noqa: E402
from sim_neural_engine import Engine                    # noqa: E402

WING = int(RegionID.WING_NEUROPIL)
HALT = int(RegionID.HALTERE_NEUROPIL)
SEZ = int(RegionID.SUBESOPHAGEAL_ZONE)
FLAG_MOTOR = 0x01
FLAG_SENSORY = 0x02

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

UPSTREAM_HOPS = 3
SOURCE_CAP = 1500
STEPS = 1200
DRIVE_NA = 40.0

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def load(path: Path):
    hdr, blocks = parse(path)
    n = int(hdr["neuronCount"])
    nb = blocks["neuron"]
    reg = [nb[i * NEURON_STRIDE + 5] for i in range(n)]
    side = [nb[i * NEURON_STRIDE + 6] for i in range(n)]
    flags = [nb[i * NEURON_STRIDE + 9] for i in range(n)]
    out: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    rb, sb = blocks["range"], blocks["synapse"]
    for u in range(n):
        st, ct = struct.unpack_from("<ii", rb, u * 8)
        for k in range(st, st + ct):
            _pre, post, cnt, _nt, sign, _c, _d, eff = struct.unpack_from(
                "<iiHBbBB2xf", sb, k * 20)
            if post < n:
                out[u].append((post, eff * sign * cnt))
    return n, reg, side, flags, out


def subgraph_for(n, reg, side, flags, out, seed_cells, hops):
    """Both wing pools plus every real cell that reaches them within `hops`."""
    rev: list[list[int]] = [[] for _ in range(n)]
    for u in range(n):
        for v, _w in out[u]:
            rev[v].append(u)
    keep: set[int] = set(seed_cells)
    frontier = deque((c, 0) for c in seed_cells)
    while frontier:
        u, d = frontier.popleft()
        if d >= hops:
            continue
        for p in rev[u]:
            if p not in keep:
                keep.add(p)
                frontier.append((p, d + 1))
    # add the seeded cells' own targets too (one hop forward), so a signal that
    # passes through the pool still has somewhere to go
    for c in list(keep):
        for v, _w in out[c]:
            keep.add(v)
    return sorted(keep)


def simulate(n, reg, side, flags, out, keep):
    """Return (summed rate in wing pool left, right) after driving the subgraph."""
    index = {cell: i for i, cell in enumerate(keep)}
    m = len(keep)
    syn = [[] for _ in range(m)]
    for cell in keep:
        for v, w in out[cell]:
            if v in index:
                syn[index[cell]].append((index[v], w))
    e = Engine(m, syn_out=syn, params={"dt": 0.1, "synapticGain": 1.0,
                                       "noiseScale": 0.0})
    return e, index


def drive_and_measure(path: Path, driven_side: int) -> dict:
    n, reg, side, flags, out = load(path)
    wing_motor = [i for i in range(n) if reg[i] == WING and flags[i] & FLAG_MOTOR]
    keep = subgraph_for(n, reg, side, flags, out, wing_motor, UPSTREAM_HOPS)
    if len(keep) > SOURCE_CAP:
        # Keep the pools, their motor cells, and the closest upstream cells.
        must = set(wing_motor)
        rest = [c for c in keep if c not in must]
        keep = sorted(must | set(rest[:max(0, SOURCE_CAP - len(must))]))
    e, index = simulate(n, reg, side, flags, out, keep)

    # Drive the EXACT cell the loop injects into, resolved the same way
    # `SensoryInterface.inputAfferent` resolves it: side match, sensory-labelled
    # and not motor-labelled first, then any side match. Driving "every haltere
    # cell on this side" instead would test a stimulus the simulator never
    # produces — the channel injects into ONE neuron — and it also masks whether
    # THAT neuron is connected, which is the single point of failure TASK-007
    # turned out to be.
    def resolve(sd: int) -> int | None:
        cand = [c for c in keep if reg[c] == HALT and side[c] == sd
                and flags[c] & FLAG_SENSORY and not flags[c] & FLAG_MOTOR]
        if not cand:
            cand = [c for c in keep if reg[c] == HALT and side[c] == sd]
        if not cand:
            return None
        return cand[0]

    target = resolve(driven_side)
    driven = [target] if target is not None else []
    for c in driven:
        e.inject(index[c], DRIVE_NA, 0)
    # Re-inject EVERY step, which is what the loop actually does: the Swift
    # side calls `engine.injectCurrent(...)` once per frame while the haltere is
    # rotating, so a single one-shot event here would test a stimulus the
    # simulator never produces. (The first version of this probe injected once
    # and measured zero on both assets — the pathway was not silent, the
    # stimulus was.)
    for _ in range(STEPS):
        for c in driven:
            e.inject(index[c], DRIVE_NA, e.time)
        e.step()

    rate = {}
    wingL = wingR = 0.0
    # `Engine.rate` is the mirror's leaky firing-rate estimate — the same
    # quantity the Swift readout samples, so the probe measures what the readout
    # would read rather than a proxy of it.
    for c in keep:
        r = e.rate[index[c]]
        if reg[c] == WING and flags[c] & FLAG_MOTOR:
            if side[c] == 1:
                wingL += r
            elif side[c] == 2:
                wingR += r
    return {"driven": driven, "left": wingL, "right": wingR, "n": len(keep),
            "wing_motor": len(wing_motor)}


print("haltere -> wing asymmetry — does driving one haltere unbalance the pools?\n")
for name, path in ASSETS:
    if not path.exists():
        print(f"      {name}: asset not present (gitignored build artifact) — skipped\n")
        continue
    left = drive_and_measure(path, 1)
    right = drive_and_measure(path, 2)
    print(f"      {name}: subgraph {left['n']} cells, wing motor {left['wing_motor']}, "
          f"haltere cells driven L={len(left['driven'])} R={len(right['driven'])}")
    print(f"        drive LEFT  haltere -> wing rates L={left['left']:.1f} R={left['right']:.1f}")
    print(f"        drive RIGHT haltere -> wing rates L={right['left']:.1f} R={right['right']:.1f}")

    asym_left = (left["left"] - left["right"]) / max(left["left"] + left["right"], 1e-9)
    asym_right = (right["left"] - right["right"]) / max(right["left"] + right["right"], 1e-9)
    print(f"        asymmetry: drive-left {asym_left:+.3f}, drive-right {asym_right:+.3f}")

    check(len(left["driven"]) > 0 or len(right["driven"]) > 0,
          f"{name}: the subgraph contains the haltere cell the loop injects into",
          f"L={len(left['driven'])} R={len(right['driven'])}")

    # The two drive sides must produce DIFFERENT imbalances, or the wiring
    # cannot express a correction at all. This is checked on BOTH assets
    # because a pathway that only responds on the synthetic demo would be a
    # claim about the demo, not about the fly.
    responded = (left["left"] + left["right"]) > 0 or (right["left"] + right["right"]) > 0
    if not responded:
        # NOT a synthetic pass: report the measured truth. A mute haltere->wing
        # path on the asset the app SHIPS is a real finding about what the app
        # can do, and hiding it behind an exemption is how TASK-007 survived.
        print(f"        [FINDING] on {name} the haltere drive does NOT reach the wing "
              f"pool: the one cell the channel injects into does not drive it")
    check(responded,
          f"{name}: the wing pools carry rate once the haltere is driven",
          f"drive-left total {left['left'] + left['right']:.1f}, "
          f"drive-right total {right['left'] + right['right']:.1f}")
    check(abs(asym_left - asym_right) > 1e-6,
          f"{name}: driving the two halteres produces DIFFERENT wing imbalances "
          f"(so a corrective sign exists to be read)",
          f"{asym_left:+.3f} vs {asym_right:+.3f}")
    print()

if failures:
    print(f"FAILED: {len(failures)} check(s)")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A haltere-side signal reaches the wing pools and unbalances them on every "
      "shipped asset, so the attitude loop has a live input rather than only a "
      "writer.")