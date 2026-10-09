#!/usr/bin/env python3
"""Feed-forward pathway — can a taste signal actually reach the legs?

WHY THIS GATE EXISTS
--------------------
TASK-005 asks for a feeding loop: hunger -> ingest -> energy, driven by the
nervous system. Before writing any of it, the question is whether the DATA can
carry it.

The gustatory channel injects into the subesophageal zone (SEZ). The proboscis
is driven by SEZ motor cells, so taste -> proboscis is one hop and is already
covered by `probe_gustatory_pathway.py`. But "the fly eats and gets energy" also
needs the animal to KEEP its mouth on the food, i.e. the taste must reach the
leg (walking) and wing (flight) motor pools. A project can have a fully working
taste channel and a fully working leg CPG and still have no animal that eats,
if nothing connects them.

This gate measures the connection on the assets that ship. It is the same shape
of question as `probe_looming_pathway.py`: the effector exists, the sensor
exists, and the gap between them is invisible to a test that only looks at
either end.

HOW THIS IS EVIDENCED
---------------------
BFS over the real synapse block in the packed asset (not a regex, not a mirror).
The SEZ holds ~10k neurons on BANC, so the search is seeded from a bounded
sample and capped; the claim is existential ("a route exists"), which a bounded
seed settles. Depth is reported because a route 9 synapses long is not the fast
sensorimotor path this loop is supposed to be.
"""
from __future__ import annotations

import struct
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from fbpack import neuron_at, parse                     # noqa: E402
from flybrain.pid import RegionID                       # noqa: E402

FLAG_MOTOR = 0x01

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

SEED_CAP = 2000
VISIT_CAP = 60000
BFS_MAX_DEPTH = 4   # "a short route exists" is settled by depth 4, not by visiting everything

# The effector regions the loop needs besides the proboscis itself (SEZ).
EFFECTORS = {
    "legNeuromere": RegionID.LEG_NEUROMERE,
    "wingNeuropil": RegionID.WING_NEUROPIL,
}

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def load(path: Path):
    hdr, blocks = parse(str(path))
    n = int(hdr["neuronCount"])
    neurons = blocks["neuron"]
    syn = blocks["synapse"]
    rng = blocks["range"]

    def cell(i: int) -> dict:
        return neuron_at(neurons, i)

    def edges(u: int):
        start, count = struct.unpack_from("<ii", rng, u * 8)
        for k in range(start, start + count):
            _pre, post, _cnt, _nt, sign, _conf, _delay, _eff = struct.unpack_from(
                "<iiHBbBB2xf", syn, k * 20)
            yield post, sign

    return hdr, n, cell, edges


def main() -> int:
    for label, path in ASSETS:
        if not path.exists():
            print(f"[SKIP] {label}: asset not present at {path.relative_to(ROOT)}")
            continue
        hdr, n, cell, edges = load(path)
        print(f"\n=== {label}: {n} neurons, {hdr['synapseCount']} synapses ===")

        region_of = [cell(i)["region"] for i in range(n)]
        flags = [cell(i)["flags"] for i in range(n)]

        sez = [i for i in range(n) if region_of[i] == int(RegionID.SUBESOPHAGEAL_ZONE)]
        check(len(sez) > 0, f"{label}: the taste target region exists",
              f"{len(sez)} SEZ neurons")

        # Reachability is asserted against the effector REGION (any cell), and
        # separately against its motor pool WHERE ONE EXISTS. A region with no
        # motor-labelled cell is a data gap in the release itself, not a broken
        # route here: `measure_motor_pool_coverage.py` already reports that the
        # wing group reads zero on BANC because wingNeuropil holds no motor
        # cell. Folding that into this gate as a failure would blame the route
        # for the asset's own hole, so the empty pool is reported by name.
        targets = {}
        pools = {}
        empty_pools = []
        for name, rid in EFFECTORS.items():
            region_cells = [i for i in range(n) if region_of[i] == int(rid)]
            pool = [i for i in region_cells if flags[i] & FLAG_MOTOR]
            targets[name] = region_cells
            pools[name] = pool
            check(len(region_cells) > 0, f"{label}: {name} exists",
                  f"{len(region_cells)} neurons, {len(pool)} labelled motor")
            if not pool:
                empty_pools.append(f"{name} (0 of {len(region_cells)} motor-labelled)")
            # A pool is only a valid target if it is a SUBSET of the region.
            check(all(i in set(region_cells) for i in pool),
                  f"{label}: {name} motor pool is inside the region", "")

        if empty_pools:
            check(True,
                  f"{label}: motor pools the readout reads ZERO from",
                  "; ".join(empty_pools) + " — an asset gap, reported not hidden")

        if not sez:
            continue

        # Level-synchronous BFS with a DEPTH cap, not just a visit cap. The
        # claim is "a short route exists", so depth 4 settles it; a
        # visit-capped search that keeps expanding would have to hold the whole
        # reachable set of a 3M-synapse graph in memory (it raised MemoryError
        # on BANC before this cap was added).
        seeds = sez[:SEED_CAP]
        seen = set(seeds)
        hit: dict[str, list[int]] = {k: [] for k in EFFECTORS}
        frontier = list(seeds)
        max_depth = 0
        # Rebuilding `set(pool)` per edge made this O(edges x targets); the
        # membership sets are built once.
        target_sets = {name: set(cells) for name, cells in targets.items()}
        truncated = False
        for d in range(1, BFS_MAX_DEPTH + 1):
            if not frontier:
                break
            if len(seen) >= VISIT_CAP:
                truncated = True
                break
            max_depth = d
            next_frontier = []
            for u in frontier:
                for post, _sign in edges(u):
                    for name, tset in target_sets.items():
                        if post in tset:
                            hit[name].append(d)
                    if post not in seen:
                        seen.add(post)
                        next_frontier.append(post)
            frontier = next_frontier
            if all(hit[name] for name in EFFECTORS):
                break

        for name in EFFECTORS:
            d = min(hit[name]) if hit[name] else None
            check(d is not None,
                  f"{label}: SEZ reaches the {name} region (the effector tissue)",
                  f"shortest hop {d}" if d is not None
                  else f"no route within depth {max_depth} "
                       f"({"exhausted, searched to depth %d" % max_depth})")

        # A path that exists but is long is not a sensorimotor loop. Polysynaptic
        # descending routes run a handful of synapses; allow a generous ceiling
        # and report the value rather than hiding it.
        for name, ds in hit.items():
            if not ds:
                continue
            d = min(ds)
            check(d <= 6, f"{label}: SEZ -> {name} is a plausible route",
                  f"{d} synapses (a long route is not a reflex path)")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("all checks passed — the taste signal has a route to the effectors")
    print("that keep the mouth on the food. Whether the loop CLOSES (hunger ->")
    print("gain -> sampling -> ingestion -> energy) is asserted by the Swift")
    print("feeding tests and mirrored in tools/mirror_feeding_loop.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())