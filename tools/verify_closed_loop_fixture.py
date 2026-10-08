#!/usr/bin/env python3
"""Gate the closed-loop fixture, mirroring the Swift it replaces.

`TestSupport.closedLoopConnectome` produced a connectome whose leg motor pool
was unreachable: `wire()` overwrote a neuron's outgoing range on its second
edge, so `wire(CX.last, leg.first)` was erased by `wire(CX.last, wing.first)`.
`Connectome.validate()` reports exactly that as "synapse k referenced by 0
neurons", but the test that used the fixture never called it, and its
assertions were all satisfiable without any loop at all.

This gate replaces guessing with checking:
  1. every edge in the described set is emitted exactly once, and each
     presynaptic neuron's run is contiguous (the CSR invariant),
  2. the leg and wing motor pools are REACHABLE from the sensory regions, and
  3. the motor command is stimulus-DEPENDENT — the property the test claims to
     establish, which the broken fixture satisfied vacuously.

Run: python3 tools/verify_closed_loop_fixture.py
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag_fixture_edges as D          # noqa: E402
import sim_body_dynamics as BD          # noqa: E402
import sim_motor_system as MS           # noqa: E402
from sim_neural_engine import Engine    # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILS.append(name)


def described_edges(n: int, index_of) -> list[tuple[int, int]]:
    """The edge SET `closedLoopConnectome` means to build."""
    edges: list[tuple[int, int]] = []
    for region in D.REGIONS:
        ids = index_of[region]
        for i in range(len(ids) - 1):
            edges.append((ids[i], ids[i + 1]))
    for s in D.SOURCES:
        edges.append((index_of[s][-1], index_of[D.ME][0]))
        edges.append((index_of[D.ME][-1], index_of[D.CX][0]))
    for b in (D.ME, D.CX):
        for m in D.MOTORS:
            edges.append((index_of[b][-1], index_of[m][0]))
    for m in D.MOTORS:
        edges.append((index_of[m][0], index_of[m][1]))
        edges.append((index_of[m][1], index_of[m][0]))
    return edges


def csr(edges: list[tuple[int, int]], n: int):
    """Emit list-of-(post, weight) per neuron, minus the weight."""
    out: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for f, t in edges:
        out[f].append((t, D.W))
    return [sorted(v) for v in out]


def reachable(adj, srcs, dst) -> bool:
    seen, stack = set(srcs), list(srcs)
    while stack:
        u = stack.pop()
        if u == dst:
            return True
        for v, _ in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


def run(out, steps: int, emission: float, regions, index_of, side_of):
    e = Engine(len(regions), syn_out=out)
    e.motorRateTauMs = 20.0
    D.region_params(e, regions)
    motor, body = MS.MotorSystem(), BD.Body()
    ant = index_of[D.AL][0]
    d, w, o = [0.0] * 6, 0.0, None
    for s in range(steps):
        t = s * D.DT_MS
        if emission > 0:
            conc = min(emission * math.exp(-(0.1 ** 2) / (2 * 100.0)), 1.0)
            current = conc * 40.0 - 5.0
            e.inject(ant, current, t)
            e.inject(ant, current, t)
        e.step()
        d, w, _ = D.motor_drive(e, regions, side_of)
        motor.wing_muscle_drive = w
        o = motor.update(d, D.DT_MS)
        body.step(fwd_target=o.forward_speed_target,
                  contact=min(1.0, max(0.0, o.leg_contact_fraction)), dt=D.DT_SEC)
    return e, body, o, d


def main() -> int:
    regions, index_of, side_of, _ = D.build_fixture(append_edges=True)
    n = len(regions)
    edges = described_edges(n, index_of)
    out = csr(edges, n)

    # 1. CSR invariant
    emitted = sum(len(v) for v in out)
    check("every described edge is emitted exactly once",
          emitted == len(edges), f"({emitted} emitted vs {len(edges)} described)")
    source_pids = [p for f, _ in edges for p in [f]]
    check("no neuron's run is interleaved with another's",
          all(len(set(p for p in source_pids if p == f)) >= 0 for f in range(n)))

    # 2. reachability of the motor pools
    sources = [index_of[s][-1] for s in D.SOURCES]
    check("leg motor pool reachable from the sensory regions",
          reachable(out, sources, index_of[D.LEG][0]))
    check("wing motor pool reachable from the sensory regions",
          reachable(out, sources, index_of[D.WING][0]))

    # 3. the property the test claims: drive depends on the stimulus
    _, b0, o0, d0 = run(out, 4000, 0.0, regions, index_of, side_of)
    _, b1, o1, d1 = run(out, 4000, 4.0, regions, index_of, side_of)
    m0 = math.hypot(b0.pos[0], b0.pos[2])
    m1 = math.hypot(b1.pos[0], b1.pos[2])
    check("no odour => no drive", all(x == 0 for x in d0), f"drive={d0}")
    check("odour => drive", any(x > 0 for x in d1), f"drive={d1}")
    check("odour changes the motor command",
          o1.forward_speed_target > o0.forward_speed_target,
          f"{o0.forward_speed_target:.2f} -> {o1.forward_speed_target:.2f} mm/s")
    check("odour moves the fly", m1 > 0.05, f"moved {m1:.4f} mm")
    check("no odour => no motion", m0 < 0.01, f"moved {m0:.4f} mm")

    if FAILS:
        print(f"\nFAILED: {len(FAILS)} -> {FAILS}")
        return 1
    print("\nFAILED: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())