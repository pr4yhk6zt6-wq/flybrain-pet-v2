#!/bin/sh
# mutation_verify_water_pathway.sh — prove probe_water_pathway.py can fail.
#
# A gate that cannot fail is not a gate. This script perturbs the repo in the
# exact ways TASK-006 was made of, runs the gate, and requires a nonzero exit
# each time; then it restores every file and requires the gate to pass.
#
#  1. remove the water call site (thirst's consumer vanishes — TASK-006 exactly)
#  2. gate the TARSAL water sample on an open proboscis (the loop becomes
#     circular: you must already be drinking to sense the water that makes you
#     drink — TASK-001's trap, in the water channel)
#  3. remove the `drink()` call (hydration can only fall again)
#  4. give water a taste valence (the two needs collapse into one)
#  5. zero the thirst gain floor (water stops being a sense and becomes a
#     thirst alarm)
#
# Files are restored from a TEMP BACKUP, not `git checkout`: these are tracked
# files that may carry uncommitted edits, and `git checkout` would discard them.

cd "$(dirname "$0")/.." || exit 1
P=python3
CORE=ios/Sources/FlyBrainCore/SimulationCore.swift
STATE=ios/Sources/FlyBrainCore/InternalState.swift
WORLD=ios/Sources/FlyBrainCore/World.swift
BAK=$(mktemp -d) || exit 1
for f in "$CORE" "$STATE" "$WORLD"; do
    mkdir -p "$BAK/$(dirname "$f")"
    cp "$f" "$BAK/$f" || exit 1
done
trap 'for f in "$CORE" "$STATE" "$WORLD"; do cp "$BAK/$f" "$f"; done; rm -rf "$BAK"' EXIT

fail=0
reset_all() {
    for f in "$CORE" "$STATE" "$WORLD"; do cp "$BAK/$f" "$f"; done
}
expect_pass() {
    if ! $P tools/probe_water_pathway.py >/tmp/water_gate.out 2>&1; then
        echo "[BROKEN] clean tree does not pass — mutations prove nothing"
        grep '^\[FAIL\]' /tmp/water_gate.out | head -5 | sed 's/^/    /'
        fail=1
    else
        echo "[restored] clean tree passes"
    fi
}
expect_fail() {
    if $P tools/probe_water_pathway.py >/tmp/water_gate.out 2>&1; then
        echo "[MUTATION NOT DETECTED] $1"
        fail=1
    else
        echo "[detected] $1 -> $(grep -c '^\[FAIL\]' /tmp/water_gate.out) FAIL line(s)"
    fi
}

echo "== baseline =="
reset_all
expect_pass

echo
echo "== mutation 1: remove the water call site (TASK-006 shape) =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace("sensoryInputs += sensory.waterInput(contact: tarsalWater)",
              "_ = tarsalWater", 1)
t = t.replace("sensoryInputs += sensory.waterInput(contact: labellarWater)",
              "_ = labellarWater", 1)
assert t != before, "mutation 1 produced no change"
p.write_text(t)
EOF
expect_fail "the water channel is never called — thirst has no consumer"
reset_all

echo
echo "== mutation 2: gate the tarsal sample on the proboscis (circular) =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace(
    "private func waterAtTarsus(_ w: WorldProvider) -> Float {\n"
    "        guard dynamics.isGrounded else { return 0 }",
    "private func waterAtTarsus(_ w: WorldProvider) -> Float {\n"
    "        guard dynamics.isGrounded, FlyBody.proboscisReaches(body.proboscis.angle) "
    "else { return 0 }", 1)
assert t != before, "mutation 2 produced no change"
p.write_text(t)
EOF
expect_fail "the tarsal water sense requires an open proboscis (circular)"
reset_all

echo
echo "== mutation 3: remove the drink() call =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace("let gained = internalState.drink(amount: taken * Self.hydrationPerWaterUnit)",
              "let gained: Float = 0\n                _ = taken", 1)
assert t != before, "mutation 3 produced no change"
p.write_text(t)
EOF
expect_fail "hydration is never written — the drink consumer is gone"
reset_all

echo
echo "== mutation 4: give water a taste valence =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/World.swift")
t = p.read_text()
before = t
t = t.replace("        case .water, .pheromone: return 0",
              "        case .water: valence = 1\n        case .pheromone: return 0", 1)
assert t != before, "mutation 4 produced no change"
p.write_text(t)
EOF
expect_fail "water is treated as food (the two needs collapse into one)"
reset_all

echo
echo "== mutation 5: zero the thirst gain floor =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/InternalState.swift")
t = p.read_text()
before = t
t = t.replace("thirstGainFloor: Float = 1.0", "thirstGainFloor: Float = 0.0", 1)
assert t != before, "mutation 5 produced no change"
p.write_text(t)
EOF
expect_fail "the thirst gain floor is 0 — water becomes a thirst alarm, not a sense"
reset_all

echo
expect_pass
echo
if [ "$fail" -ne 0 ]; then
    echo "FAILED: a mutation went undetected (or the restore broke the tree)."
else
    echo "ALL MUTATIONS DETECTED — the water-pathway probe has teeth."
fi
exit "$fail"