#!/usr/bin/env python3
"""Census every neuropil tag in the real BANC release.

Answers, from the data instead of from memory: which tags exist, how many
neurons carry each as their primary (first-listed) tag, and where those neurons
actually sit along the anterior-posterior axis (mean y in voxels, 4 nm/voxel in
x/y). That last column is what decides whether a tag belongs to the brain
(mean y < ~131,000) or the VNC, and whether an unmapped tag is worth mapping.

Usage: python3 tools/census_neuropil_tags.py [--data data/raw/flywire/banc]
"""
from __future__ import annotations

import gzip
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain.neuropil import map_neuropil, strip_side  # noqa: E402


def main() -> int:
    data = ROOT / "data/raw/flywire/banc"
    if len(sys.argv) > 2 and sys.argv[1] == "--data":
        data = Path(sys.argv[2])
    with gzip.open(data / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    primary = Counter()          # first-listed tag -> neurons
    mapped_primary = Counter()   # first-listed tag -> mapped?
    y_sum: dict[str, float] = defaultdict(float)
    y_n: dict[str, int] = defaultdict(int)
    any_tag = Counter()
    worst: dict[str, int] = defaultdict(int)

    for rid, a in attrs.items():
        tags: list[str] = []
        for key in ("input_neuropils", "output_neuropils"):
            tags.extend(a.get(key) or [])
        if not tags:
            primary["<none>"] += 1
            continue
        for t in tags:
            any_tag[t] += 1
        first = tags[0]
        primary[first] += 1
        pos = a.get("position") or []
        if pos and isinstance(pos[0], str):
            try:
                y = float(pos[0].split(",")[1])
                y_sum[first] += y
                y_n[first] += 1
            except (IndexError, ValueError):
                pass
        if map_neuropil(first, a.get("side", "")) is None:
            worst[first] += 1

    print(f"neurons with attributes : {len(attrs)}")
    print(f"distinct tags (anywhere) : {len(any_tag)}")
    print(f"distinct first tags      : {len(primary)}")
    print()
    print(f"{'tag':<22}{'neurons':>9}{'mean y':>10}   mapped")
    for tag, n in primary.most_common():
        my = (y_sum[tag] / y_n[tag]) if y_n[tag] else float("nan")
        m = map_neuropil(tag, "")
        print(f"{tag:<22}{n:>9}{my:>10.0f}   "
              f"{'CHAIN' if m is None else m}")
    print()
    total_unmapped_first = sum(worst.values())
    print(f"neurons whose FIRST tag is unmapped: {total_unmapped_first}")
    print("top unmapped first tags:")
    for tag, n in sorted(worst.items(), key=lambda kv: -kv[1])[:25]:
        my = (y_sum[tag] / y_n[tag]) if y_n[tag] else float("nan")
        print(f"    {tag:<22}{n:>8}  mean y {my:>9.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())