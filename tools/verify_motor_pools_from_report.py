#!/usr/bin/env python3
"""Every region the motor readout consults must have motor cells to read.

`readMotorDrive` (SimulationCore.swift) sums the neural rate of MOTOR cells in
three regions and publishes three actuators:

    legNeuromere      -> 6-slot leg drive
    wingNeuropil      -> motor.wingMuscleDrive   (wing stroke -> lift)
    subesophagealZone -> motor.proboscisDrive    (feeding loop)

A region with no motor cell reads exactly zero, forever — the same "channel with
no producer" failure as the earlier gustation bug. TASK-007 was exactly this:
the ingest filed every flight power muscle outside `WING_NEUROPIL`, so
`wingMuscleDrive` was structurally zero on the real release while the demo asset
(which happens to place 2 motor cells there) hid it.

WHY THIS GATE EXISTS ALONGSIDE `probe_actuator_motor_pools.py`
-------------------------------------------------------------
That probe reads the ASSET bytes. The real BANC asset is a 68 MB gitignored
build artifact, so on CI it is absent and its finding cannot run — which is how
a zero-wing-pool shipped unnoticed. The ingest now records motor cells per
region in `banc_cns.report.json`, and THAT file is tracked. This gate reads the
tracked report, so the pool coverage is checkable in CI without the binary.

Both regions lists come from the Swift source at run time, so pointing the
readout at a new region (or renaming one) moves this gate.

Run: python3 tools/verify_motor_pools_from_report.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"
REPORT = ROOT / "data/generated/banc_cns.report.json"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


# --- the regions the readout consults, read from the Swift itself -----------
#
# `readMotorDrive` has TWO `switch region` blocks: the first FILTERS which
# regions contribute at all, the second ROUTES each region to an accumulator
# (`wing += rate`). A region in the filter but missing from the routing is
# summed into `motorDriveTotal` and then dropped on the floor — the same
# "declared but no producer/consumer" shape as TASK-007, and it would let a
# mutation that deletes a routing case pass unnoticed if this gate only read
# the filter. The actuator probe (probe_actuator_motor_pools.py) parses the
# routing switch; this gate must agree with it.
src = SRC.read_text()
switches = re.findall(r"switch region \{\n(.*?)\n\s{12}\}", src, re.S)
if len(switches) < 2:
    print(f"FAIL: expected two `switch region` blocks in readMotorDrive, "
          f"found {len(switches)}")
    sys.exit(1)


def cases_of(block: str) -> list[str]:
    out: list[str] = []
    for case in re.findall(r"case (\.\w+(?:, \.\w+)*):", block):
        out += [c.strip().lstrip(".") for c in case.split(",")]
    return out


filtered = cases_of(switches[0])
routed = cases_of(switches[1])


def region_name(camel: str) -> str:
    """`legNeuromere` -> `LEG_NEUROMERE`, the name the report uses."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", camel).upper()


# The filter may name a region the routing then drops. That asymmetry means a
# rate is counted into `motorDriveTotal` but never reaches an actuator, so it
# is a defect in the readout and is checked before anything else.
unrouted = [r for r in filtered if r not in routed]
extra_routed = [r for r in routed if r not in filtered]

wanted = {region_name(c): c for c in filtered}
print(f"readMotorDrive filters: {', '.join(filtered)}")
print(f"readMotorDrive routes:  {', '.join(routed)}\n")

if not REPORT.exists():
    print(f"FAIL: {REPORT.relative_to(ROOT)} is missing — the ingest has no "
          f"tracked evidence to check")
    sys.exit(1)

report = json.loads(REPORT.read_text())
motor = report.get("motor_by_region")

if motor is None:
    print("FAIL: the report has no `motor_by_region` — it predates the field, "
          "so pool coverage cannot be checked. Re-run tools/ingest_banc.py.")
    sys.exit(1)

# The report is the tracked proxy for the asset; the region names must line up
# or the check would silently pass by finding nothing to look at.
missing = [r for r in wanted if r not in motor]
check(not missing, "every region the readout reads is named in the report",
      f"absent from motor_by_region: {missing}" if missing else
      f"{len(wanted)} regions")

# A region the filter admits but the routing ignores is summed into the total
# and then dropped — no actuator ever sees it. Checked before the counts, so
# the failure names the asymmetry rather than a downstream zero.
check(not unrouted, "every filtered region is routed to an accumulator",
      f"filtered but never routed: {unrouted}" if unrouted else
      f"{len(routed)} routed regions")
check(not extra_routed, "every routed region is admitted by the filter",
      f"routed but never filtered: {extra_routed}" if extra_routed else
      "filter and routing agree")

for name, camel in sorted(wanted.items()):
    n = motor.get(name, 0)
    check(n > 0, f"{camel} has motor cells to read",
          f"{n:,} motor cells" if n else
          "0 motor cells — this actuator can never move (the TASK-007 shape)")

# The ingestion records the release region too: if the column ever disappears
# and everything silently falls back to the tag rule, the wing pool would empty
# again and only this would say why.
source = report.get("region_source", {})
check(bool(source), "the report records which region source was used",
      str(source) if source else "missing — provenance is unrecorded")

print()
if failures:
    print(f"FAILED: {len(failures)} check(s)")
    sys.exit(1)
print("Every motor pool the readout consults is populated on BANC.")