#!/bin/sh
# mutation_verify_actuator_pools.sh — prove probe_actuator_motor_pools.py fails.
#
# The probe pins the measured state of `readMotorDrive`'s two switches and of
# the per-region motor-cell counts. A pin that cannot fail is a comment, so
# each mutation below breaks one half of it and must produce a nonzero exit:
#
#   1. repoint a routing case (wingNeuropil -> sez) — the group is still read,
#      so only code that PARSES the routing switch can notice
#   2. consult a region the pinned table does not know
#   3. drop a routing case so the readout consults fewer regions
#
# The clean tree must pass, otherwise the mutations prove nothing.
#
# Files are restored from a TEMP BACKUP, NOT `git checkout`: `SimulationCore.swift`
# is a tracked file that may legitimately carry uncommitted edits (TASK-006's
# water loop lived there while this script deleted it), and `git checkout`
# silently discards them. This script did exactly that once; the backup makes
# it safe to run before the work is committed.

cd "$(dirname "$0")/.." || exit 1
P=python3
SRC=ios/Sources/FlyBrainCore/SimulationCore.swift
BAK=$(mktemp -d) || exit 1
cp "$SRC" "$BAK/SimulationCore.swift" || exit 1
trap 'cp "$BAK/SimulationCore.swift" "$SRC"; rm -rf "$BAK"' EXIT

fails=0
reset_src() { cp "$BAK/SimulationCore.swift" "$SRC"; }
expect_pass() {
    if ! $P tools/probe_actuator_motor_pools.py >/dev/null 2>&1; then
        echo "[MISSED] clean tree did not pass — mutations prove nothing"
        fails=$((fails + 1))
    fi
}
expect_fail() {
    if $P tools/probe_actuator_motor_pools.py >/dev/null 2>&1; then
        echo "[MISSED] mutation not detected: $1"
        fails=$((fails + 1))
    else
        echo "[detected] $1"
    fi
}

echo "== baseline =="
reset_src
expect_pass

echo "== mutation 1: repoint the wing routing case =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
t = t.replace("case .wingNeuropil:\n                wing += rate",
              "case .wingNeuropil:\n                sez += rate", 1)
p.write_text(t)
EOF
expect_fail "wingNeuropil routed to the sez accumulator"
reset_src

echo "== mutation 2: consult a region the pinned table does not know =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
t = t.replace("            case .wingNeuropil:\n                wing += rate",
              "            case .wingNeuropil, .haltereNeuropil:\n                wing += rate", 1)
p.write_text(t)
EOF
expect_fail "readout consults haltereNeuropil, absent from the pinned table"
reset_src

echo "== mutation 3: drop a routing case entirely =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
t = t.replace("            case .subesophagealZone:\n                sez += rate\n", "", 1)
p.write_text(t)
EOF
expect_fail "subesophagealZone case removed from the readout"
reset_src

echo "== clean tree again =="
expect_pass

if [ "$fails" -ne 0 ]; then
    echo "FAILED: $fails mutation(s) went undetected"
    exit 1
fi
echo "ALL MUTATIONS DETECTED — the actuator probe has teeth."