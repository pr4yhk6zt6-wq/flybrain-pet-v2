#!/usr/bin/env python3
"""Is the BANC connection release sorted by (pre, post)?

flybrain/banc.py merges duplicate (pre, post) pairs by summing counts, and it
does that with a single adjacent-pair scan so memory stays flat in the edge
count (the device cap is ~1.5 GB). That is only correct if every duplicate pair
is ADJACENT, i.e. if the release is sorted by (pre, post) and not merely by pre.
This script answers that question on the raw file without holding it in memory,
so the assumption is measured rather than assumed.

Usage:
    python3 tools/check_release_order.py [connections_princeton.csv.gz]

Exit code 0 = sorted by (pre, post); 1 = at least one inversion (merge would
miss duplicates and the ingest must group explicitly instead).
"""
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "data/raw/flywire/banc/connections_princeton.csv.gz")
    if not path.exists():
        print(f"missing release file: {path}")
        return 2

    rows = 0
    pre_inversions = 0
    post_inversions = 0
    dup_adjacent = 0
    dup_nonadjacent = 0
    last_pre = -1
    last_post = -1
    first_bad = None
    seen_at = {}
    with gzip.open(path, "rt", newline="") as fh:
        for line in fh:
            a = line.find(",")
            b = line.find(",", a + 1)
            c = line.find(",", b + 1)
            d = line.find(",", c + 1)
            if d < 0:
                continue                       # header / truncated
            try:
                pre = int(line[:a])
                post = int(line[a + 1:b])
            except ValueError:
                continue
            rows += 1
            if pre < last_pre:
                pre_inversions += 1
                if first_bad is None:
                    first_bad = ("pre", rows, pre, last_pre)
            elif pre == last_pre:
                if post < last_post:
                    post_inversions += 1
                    if first_bad is None:
                        first_bad = ("post", rows, post, last_post)
                if post == last_post:
                    dup_adjacent += 1
                else:
                    # a repeat of an earlier post inside the same pre group is a
                    # non-adjacent duplicate: the adjacent-merge would miss it
                    key = (pre, post)
                    if key in seen_at:
                        dup_nonadjacent += 1
            if pre != last_pre:
                seen_at.clear()
            seen_at[(pre, post)] = rows
            last_pre, last_post = pre, post

    print(f"rows                : {rows}")
    print(f"pre  inversions     : {pre_inversions}")
    print(f"post inversions     : {post_inversions}")
    print(f"adjacent duplicate repeats   : {dup_adjacent}")
    print(f"NON-adjacent repeat sightings: {dup_nonadjacent}")
    if first_bad:
        print(f"first inversion     : {first_bad}")
    ok = pre_inversions == 0 and dup_nonadjacent == 0
    print("SORTED by (pre, post)" if ok else "NOT sorted by (pre, post)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())