#!/usr/bin/env python3
"""Keep the Swift test fixture byte-identical to the pipeline's asset.

Why a copy exists at all: the Swift tests run under SwiftPM (`swift test`),
which only collects resources from inside the target directory. The pipeline
writes `data/generated/demo_micro.fbpack`, outside it. Copying it in — and
failing CI when the two differ — makes the cross-language format contract an
executed test instead of a reading exercise.

Discipline note: the asset is NOT byte-reproducible (the header carries a
generation date and the network is seeded from time), so this must compare the
existing files rather than regenerate on the fly; regenerating would churn the
fixture on every run and could hide a real format drift.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/generated/demo_micro.fbpack"
FIXTURE = ROOT / "ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"

    if not SOURCE.exists():
        print(f"missing pipeline asset: {SOURCE}", file=sys.stderr)
        return 1

    if mode == "--sync":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE, FIXTURE)
        print(f"synced {FIXTURE.relative_to(ROOT)} from {SOURCE.relative_to(ROOT)}")
        return 0

    if not FIXTURE.exists():
        print(f"FAIL: test fixture {FIXTURE.relative_to(ROOT)} is missing; "
              f"run tools/sync_test_asset.py --sync", file=sys.stderr)
        return 1

    a, b = SOURCE.read_bytes(), FIXTURE.read_bytes()
    if a != b:
        print(
            "FAIL: the Swift test fixture has drifted from the pipeline asset.\n"
            f"  {SOURCE.relative_to(ROOT)}  ({len(a)} bytes)\n"
            f"  {FIXTURE.relative_to(ROOT)}  ({len(b)} bytes)\n"
            "Run tools/sync_test_asset.py --sync and commit the result; a stale "
            "fixture makes the cross-language format gate pass against an old format.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: Swift test fixture matches the pipeline asset ({len(a)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())