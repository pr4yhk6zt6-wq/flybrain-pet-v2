#!/bin/sh
# mutation_verify_motor_pools.sh — prove verify_motor_pools_from_report.py fails.
#
# The gate reads the TRACKED ingest report and checks that every region the
# motor readout consults has motor cells. A pin that cannot fail is a comment,
# so each mutation below breaks one half of it and must produce a nonzero exit:
#
#   1. empty a pool in the report (wingNeuropil -> 0) — the TASK-007 shape
#   2. delete a pool row entirely, so a consulted region is unnamed
#   3. repoint the readout at a region the report does not carry
#      (haltereNeuropil) — only a gate that reads the SWIFT can notice
#   4. strip the `region_source` provenance field
#
# The clean tree must pass, otherwise the mutations prove nothing.
#
# Files are restored from a TEMP BACKUP, not `git checkout`: the report is a
# tracked file that may legitimately carry uncommitted edits (a fresh ingest),
# and `git checkout` would silently discard them. Restoring from the backup
# also means this script is safe to run before the work is committed.

cd "$(dirname "$0")/.." || exit 1
P=python3
REPORT=data/generated/banc_cns.report.json
SRC=ios/Sources/FlyBrainCore/SimulationCore.swift
BAK=$(mktemp -d) || exit 1
cp "$REPORT" "$BAK/report.json" || exit 1
cp "$SRC" "$BAK/SimulationCore.swift" || exit 1
trap 'cp "$BAK/report.json" "$REPORT"; cp "$BAK/SimulationCore.swift" "$SRC"; rm -rf "$BAK"' EXIT

fails=0
reset_all() { cp "$BAK/report.json" "$REPORT"; cp "$BAK/SimulationCore.swift" "$SRC"; }
expect_pass() {
    if ! $P tools/verify_motor_pools_from_report.py >/dev/null 2>&1; then
        echo "[MISSED] clean tree did not pass — mutations prove nothing"
        fails=$((fails + 1))
    fi
}
expect_fail() {
    if $P tools/verify_motor_pools_from_report.py >/dev/null 2>&1; then
        echo "[MISSED] mutation not detected: $1"
        fails=$((fails + 1))
    else
        echo "[detected] $1"
    fi
}
mutate_report() {
    # $1 = python body operating on the dict `d`
    $P - "$1" <<'EOF'
import json, sys
from pathlib import Path
p = Path("data/generated/banc_cns.report.json")
d = json.loads(p.read_text())
exec(sys.argv[1])
p.write_text(json.dumps(d, indent=2) + "\n")
EOF
}

echo "== baseline =="
reset_all
expect_pass

echo "== mutation 1: empty the wing pool (TASK-007 shape) =="
mutate_report 'd["motor_by_region"]["WING_NEUROPIL"] = 0'
expect_fail "wingNeuropil reads 0 motor cells"
reset_all

echo "== mutation 2: delete a consulted region's row =="
mutate_report 'del d["motor_by_region"]["SUBESOPHAGEAL_ZONE"]'
expect_fail "subesophagealZone absent from the report"
reset_all

echo "== mutation 3: repoint the readout at an unreported region =="
# `medulla` is a real RegionID but appears in NO motor pool, so the gate's
# "every region the readout reads is named in the report" check must fire.
# (An earlier version of this mutation used `haltereNeuropil`, which DOES
# carry 33 motor cells — the gate correctly passed and the mutation proved
# nothing, which is exactly what the baseline step is there to expose.)
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
t = t.replace("            case .wingNeuropil:\n                wing += rate",
              "            case .wingNeuropil, .medulla:\n                wing += rate", 1)
p.write_text(t)
EOF
expect_fail "readout consults medulla, absent from the report"
reset_all

echo "== mutation 4: strip the region_source provenance =="
mutate_report 'del d["region_source"]'
expect_fail "the report no longer records which region source was used"
reset_all

echo "== mutation 5: filter admits a region the routing never handles =="
# The exact blind spot this gate grew a second switch-parse for: a region
# survives the FILTER (so its rate lands in motorDriveTotal) but has no
# routing case, so no actuator ever sees it. A gate reading only the first
# switch passes here; this one must not.
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
t = t.replace("            case .legNeuromere, .wingNeuropil, .subesophagealZone:\n"
              "                if !classifyByRegion && !n.isMotorNeuron { continue }",
              "            case .legNeuromere, .wingNeuropil, .subesophagealZone, .haltereNeuropil:\n"
              "                if !classifyByRegion && !n.isMotorNeuron { continue }", 1)
p.write_text(t)
EOF
expect_fail "filter admits haltereNeuropil but the routing drops it"
reset_all

echo "== clean tree again =="
expect_pass

if [ "$fails" -ne 0 ]; then
    echo "FAILED: $fails mutation(s) went undetected"
    exit 1
fi
echo "ALL MUTATIONS DETECTED — the motor-pool report gate has teeth."