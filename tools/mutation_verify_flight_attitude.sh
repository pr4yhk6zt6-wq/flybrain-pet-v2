#!/bin/sh
# mutation_verify_flight_attitude.sh — prove the flight-attitude gates can fail.
#
# Two gates cover TASK-008 and each guards a different half:
#   tools/mirror_flight_attitude.py       the producer exists and the sign is right
#   tools/probe_haltere_wing_asymmetry.py a haltere signal actually reaches the pools
#
# A gate that cannot fail is not a gate. Each mutation below breaks one half and
# must produce a nonzero exit from the gate named; then every file is restored
# and the clean tree must pass.
#
#  1. remove the writer (back to the pre-TASK-008 state: declared, never written)
#  2. flip the sign (a louder left wing would roll the fly the WRONG way — worse
#     than having no loop, because it amplifies the perturbation)
#  3. stop splitting the readout by side (the asymmetry becomes a difference of
#     one scalar with itself, so it is always 0)
#  4. make the demo's haltere->VNC path inhibitory again (the reflex can never
#     fire on the asset the app ships)
#
# Files are restored from a TEMP BACKUP, not `git checkout`: these are tracked
# files that may carry uncommitted edits, and `git checkout` silently deletes
# them (this happened for real during TASK-006).

cd "$(dirname "$0")/.." || exit 1
P=python3
CORE=ios/Sources/FlyBrainCore/SimulationCore.swift
PID=python/flybrain/pid.py
DEMO=data/generated/demo_micro.fbpack
DEMO_TEST=ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack
BAK=$(mktemp -d) || exit 1
for f in "$CORE" "$PID" "$DEMO" "$DEMO_TEST"; do
    mkdir -p "$BAK/$(dirname "$f")"
    cp "$f" "$BAK/$f" || exit 1
done
trap 'for f in "$CORE" "$PID" "$DEMO" "$DEMO_TEST"; do cp "$BAK/$f" "$f"; done; rm -rf "$BAK"' EXIT

fail=0
reset_all() {
    for f in "$CORE" "$PID" "$DEMO" "$DEMO_TEST"; do cp "$BAK/$f" "$f"; done
}
expect_fail() {
    gate="$1"; label="$2"
    if $P "$gate" >/tmp/flight_gate.out 2>&1; then
        echo "[MUTATION NOT DETECTED] $label ($gate)"
        grep '^\[FAIL\]' /tmp/flight_gate.out | head -4 | sed 's/^/    /'
        fail=1
    else
        echo "[detected] $label -> $(grep -c '^\[FAIL\]' /tmp/flight_gate.out) FAIL line(s)"
    fi
}
expect_pass() {
    if ! $P tools/mirror_flight_attitude.py >/tmp/flight_gate.out 2>&1; then
        echo "[BROKEN] clean tree does not pass mirror_flight_attitude — mutations prove nothing"
        fail=1
    elif ! $P tools/probe_haltere_wing_asymmetry.py >/tmp/flight_gate2.out 2>&1; then
        echo "[BROKEN] clean tree does not pass probe_haltere_wing_asymmetry"
        fail=1
    else
        echo "[restored] clean tree passes both gates"
    fi
}

echo "== baseline =="
reset_all
expect_pass

echo
echo "== mutation 1: remove the writer (the pre-TASK-008 state) =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace("motor.leftRightAsymmetry = wingAsymmetry(left: wingLeft, right: wingRight)",
              "// motor.leftRightAsymmetry = wingAsymmetry(left: wingLeft, right: wingRight)", 1)
assert t != before, "mutation 1 produced no change"
p.write_text(t)
EOF
expect_fail tools/mirror_flight_attitude.py "the asymmetry has no writer"
reset_all

echo
echo "== mutation 2: flip the sign =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace("return min(max((left - right) / total, -1), 1)",
              "return min(max((right - left) / total, -1), 1)", 1)
assert t != before, "mutation 2 produced no change"
p.write_text(t)
EOF
expect_fail tools/mirror_flight_attitude.py "the asymmetry sign is inverted"
reset_all

echo
echo "== mutation 3: stop splitting the readout by side =="
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/SimulationCore.swift")
t = p.read_text()
before = t
t = t.replace("""                if n.side == 1 { wingLeft += rate } else if n.side == 2 { wingRight += rate }""",
              """                wingLeft += rate; wingRight += rate""", 1)
assert t != before, "mutation 3 produced no change"
p.write_text(t)
EOF
expect_fail tools/mirror_flight_attitude.py "the wing readout no longer separates the sides"
reset_all

echo
echo "== mutation 4: make the demo haltere path inhibitory again =="
$P - <<'EOF'
from pathlib import Path
import subprocess, sys
p = Path("python/flybrain/pid.py")
t = p.read_text()
before = t
t = t.replace("(RegionID.HALTERE_NEUROPIL, RegionID.VENTRAL_NERVE_CORD, +1, TransmitterType.CHOLINERGIC, (2, 4)),",
              "(RegionID.HALTERE_NEUROPIL, RegionID.VENTRAL_NERVE_CORD, -1, TransmitterType.GABAERGIC, (2, 4)),", 1)
assert t != before, "mutation 4 produced no change"
p.write_text(t)
subprocess.run([sys.executable, "tools/gen_synth.py", "data/generated/demo_micro.fbpack"],
               check=True, capture_output=True)
Path("ios/Tests/FlyBrainCoreTests/Resources/demo_micro.fbpack").write_bytes(
    Path("data/generated/demo_micro.fbpack").read_bytes())
EOF
expect_fail tools/probe_haltere_wing_asymmetry.py "the demo haltere reflex inhibits its target"
reset_all

echo
expect_pass
echo
if [ "$fail" -ne 0 ]; then
    echo "FAILED: a mutation went undetected (or the restore broke the tree)."
else
    echo "ALL MUTATIONS DETECTED — the flight-attitude gates have teeth."
fi
exit "$fail"