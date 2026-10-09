#!/usr/bin/env python3
"""Measure the looming pathway on the assets this repo actually ships.

The claim under test is from PLAN.md phase 3: vision with "looming" is
implemented, and docs/VALIDATION.md scenario 04 expects "sudden looming ->
escape". Both need two things to exist in the data:

  1. a producer that turns something in the world into a looming signal, and
  2. a route from the looming target region (LOBULA_PLATE) to motor cells.

(2) is measurable on the shipped assets today, so this tool measures it rather
than assuming it. `tools/probe_shipped_asset_channels.py` already checks that
every channel in its CHANNELS table can ADDRESS a neuron; a channel can be
addressable and still lead nowhere, which is the case this tool covers.

Run: python3 tools/probe_looming_pathway.py
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

FLAG_MOTOR, FLAG_SENSORY = 0x01, 0x02

ASSETS = [("demo", ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"),
          ("banc", ROOT / "data/generated/banc_cns.fbpack")]

# The plate holds 17,803 neurons on BANC; a full multi-source BFS from all of
# them is 153k neurons deep and did not finish in 15 minutes here. The claim
# being checked is existential — "a route exists" — so seeding a bounded sample
# is sufficient, and the cap makes the cost independent of asset size.
SEED_CAP = 4000
VISIT_CAP = 60000

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
            pre, post, _cnt, _nt, sign, _conf, _delay, _eff = struct.unpack_from(
                "<iiHBbBB2xf", syn, k * 20)
            yield pre, post, sign

    return hdr, n, cell, edges


def main() -> int:
    for label, path in ASSETS:
        if not path.exists():
            print(f"[SKIP] {label}: asset not present at {path.relative_to(ROOT)}")
            continue
        hdr, n, cell, edges = load(path)
        print(f"\n=== {label}: {n} neurons, {hdr['synapseCount']} synapses ===")

        flags = [cell(i)["flags"] for i in range(n)]
        motor = [i for i in range(n) if flags[i] & FLAG_MOTOR]
        region_of = [cell(i)["region"] for i in range(n)]
        plate = [i for i in range(n) if region_of[i] == int(RegionID.LOBULA_PLATE)]

        check(len(plate) > 0, f"{label}: lobula plate exists and is non-empty",
              f"{len(plate)} neurons (the looming target region)")
        check(len(motor) > 0, f"{label}: motor cells exist",
              f"{len(motor)} flagged motor")

        if not plate or not motor:
            continue

        # BFS over the real synapse block from the plate. Seeded from a bounded
        # sample and stopped at VISIT_CAP so the runtime does not scale with the
        # asset — a probe that runs for 15 minutes is a probe nobody runs.
        seeds = plate[:SEED_CAP]
        seen = set(seeds)
        depth = {i: 0 for i in seeds}
        q = deque(seeds)
        reached_motor = set()
        truncated = False
        while q:
            if len(seen) >= VISIT_CAP:
                truncated = True
                break
            u = q.popleft()
            for _pre, post, _sign in edges(u):
                if post in motor:
                    reached_motor.add(post)
                if post not in seen:
                    seen.add(post)
                    depth[post] = depth[u] + 1
                    q.append(post)

        check(len(seen) - len(seeds) > 0,
              f"{label}: the plate has outgoing connections at all",
              f"{len(seen) - len(seeds)} downstream neurons "
              f"(seeded from {len(seeds)} of {len(plate)} plate cells"
              f"{'; search truncated' if truncated else ''})")
        check(len(reached_motor) > 0,
              f"{label}: plate reaches motor cells",
              f"{len(reached_motor)} of {len(motor)} motor cells, "
              f"max depth {max(depth.values())}")

        # Where a signal would have to land: the plate's own efferents.
        if reached_motor:
            d = min(depth[i] for i in reached_motor)
            check(d <= 4, f"{label}: the plate-to-motor path is short",
                  f"shortest motor hop at depth {d} (a descending/giant-fibre "
                  f"style route would be 1-3 synapses)")

        # Sensory afferents INSIDE the plate: the channel selects by class when
        # the asset has classes, so whether the plate holds any is what decides
        # whether loomingInput lands on a labelled cell or a positional guess.
        sens_in_plate = sum(1 for i in plate if flags[i] & FLAG_SENSORY)
        check(True, f"{label}: class-labelled cells inside the plate",
              f"{sens_in_plate} of {len(plate)} "
              f"({'class-addressed' if sens_in_plate else 'positional fallback: no sensory label here'})")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("all checks passed — the looming target region is wired to motor cells")
    print("on every asset present. This measures the DATA; whether a producer")
    print("emits looming at all is asserted in VisionSystemTests (Swift).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())