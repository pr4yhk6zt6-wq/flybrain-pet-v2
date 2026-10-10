#!/bin/sh
# Mutation test for tools/verify_doc_claims.py
#
# A gate that cannot fail is not a gate. This script perturbs the repo in the
# exact ways the gate claims to detect, runs the gate, and requires a nonzero
# exit each time; then it restores every file and requires the gate to pass.
#
# Every mutation is applied with python (not sed) so the edits are exact and
# the restore is byte-identical.
set -e
cd "$(dirname "$0")/.."

DOC=docs/BIOLOGICAL_LIMITATIONS.md
VIS=ios/Sources/FlyBrainCore/VisionSystem.swift
README_MD=README.md
ENG=ios/Sources/FlyBrainCore/NeuralEngine.swift

fail=0
expect_fail() {
    label="$1"
    if python3 tools/verify_doc_claims.py >/tmp/gate.out 2>&1; then
        echo "[MUTATION NOT DETECTED] $label"
        grep -E '^\[(PASS|FAIL)\]' /tmp/gate.out | sed 's/^/    /'
        fail=1
    else
        echo "[detected] $label -> $(grep -c '^\[FAIL\]' /tmp/gate.out) FAIL line(s)"
        grep '^\[FAIL\]' /tmp/gate.out | sed 's/^/    /'
    fi
}

expect_pass() {
    if python3 tools/verify_doc_claims.py >/tmp/gate.out 2>&1; then
        echo "[restored] clean tree passes"
    else
        echo "[BROKEN RESTORE] gate fails on the clean tree"
        grep '^\[FAIL\]' /tmp/gate.out | sed 's/^/    /'
        fail=1
    fi
}

P=python3

# 1. Doc claims the scaffold era again  -> stamp check must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("Last updated: Phase 6/7 in progress",
              "Last updated: project scaffold (Phase 1-2)", 1)
p.write_text(t)
EOF
expect_fail "doc stamp reverted to the scaffold era"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("Last updated: project scaffold (Phase 1-2)",
              "Last updated: Phase 6/7 in progress", 1)
p.write_text(t)
EOF

# 2. A subsystem the doc marks PLANNED has call sites -> A/B must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("| Mechanosensation / proprioception | LOW |",
              "| Mechanosensation / proprioception | PLANNED |", 1)
p.write_text(t)
EOF
expect_fail "mechanosensation marked PLANNED while wired"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("| Mechanosensation / proprioception | PLANNED |",
              "| Mechanosensation / proprioception | LOW |", 1)
p.write_text(t)
EOF

# 3. A dead allocation is re-introduced -> dead-field rule must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("    private var adaptedLuminance: [Float] = []",
              "    private var adaptedLuminance: [Float] = []\n"
              "    private var flowAccumulator: [(Float, Float)] = []", 1)
p.write_text(t)
EOF
expect_fail "flowAccumulator re-added with no reader"

$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("\n    private var flowAccumulator: [(Float, Float)] = []", "", 1)
p.write_text(t)
EOF

# 4. A deleted type is referenced by live code -> removed-type check must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/NeuralEngine.swift")
t = p.read_text()
t = t.replace("// MARK: - The engine",
              "func makeSpikeEventProbe() -> SpikeEvent? { nil }\n\n"
              "// MARK: - The engine", 1)
p.write_text(t)
EOF
expect_fail "live reference to deleted SpikeEvent"

$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/NeuralEngine.swift")
t = p.read_text()
t = t.replace("func makeSpikeEventProbe() -> SpikeEvent? { nil }\n\n", "", 1)
p.write_text(t)
EOF

# 5. A NEW dead allocation with a name the gate has never heard of -> the
#    RULE (not the name pin) must fail. Mutation 3 above is caught by the
#    removed-type list; this one cannot be.
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("    private var adaptedLuminance: [Float] = []",
              "    private var adaptedLuminance: [Float] = []\n"
              "    private var opticFlowScratch: [Float] = []", 1)
p.write_text(t)
EOF
expect_fail "novel dead field opticFlowScratch (rule D, not the name pin)"

$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("\n    private var opticFlowScratch: [Float] = []", "", 1)
p.write_text(t)
EOF

# 6. README claims motion without the disclosure -> E must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("README.md")
t = p.read_text()
t = t.replace("(ommatidial array, ON/OFF/looming; motion + small-object NOT yet emitted)",
              "(ommatidial array, ON/OFF/looming, motion detection)", 1)
p.write_text(t)
EOF
expect_fail "README claims motion while VisionSystem header denies it"

$P - <<'EOF'
from pathlib import Path
p = Path("README.md")
t = p.read_text()
t = t.replace("(ommatidial array, ON/OFF/looming, motion detection)",
              "(ommatidial array, ON/OFF/looming; motion + small-object NOT yet emitted)", 1)
p.write_text(t)
EOF

# 7. VISION.md lists an unemitted channel as represented -> G must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/VISION.md")
t = p.read_text()
t = t.replace("| optic flow (translational, rotational) | **NOT EMITTED** |",
              "| optic flow (translational, rotational) | EMITTED |", 1)
p.write_text(t)
EOF
expect_fail "VISION.md presents optic flow as shipped"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/VISION.md")
t = p.read_text()
t = t.replace("| optic flow (translational, rotational) | EMITTED |",
              "| optic flow (translational, rotational) | **NOT EMITTED** |", 1)
p.write_text(t)
EOF

# 8. VisionSystem's header stops disclosing the unemitted pathways -> G must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("  NOT yet emitted here, though the `VisualPathway` enum and `mapToInput`",
              "  Some channels, though the `VisualPathway` enum and `mapToInput`", 1)
p.write_text(t)
EOF
expect_fail "VisionSystem header drops its unemitted-pathway disclosure"

$P - <<'EOF'
from pathlib import Path
p = Path("ios/Sources/FlyBrainCore/VisionSystem.swift")
t = p.read_text()
t = t.replace("  Some channels, though the `VisualPathway` enum and `mapToInput`",
              "  NOT yet emitted here, though the `VisualPathway` enum and `mapToInput`", 1)
p.write_text(t)
EOF

# 9. A core module is undocumented in ARCHITECTURE.md -> H must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/ARCHITECTURE.md")
t = p.read_text()
t = t.replace("├── VectorMath.swift", "├── (removed line)", 1)
p.write_text(t)
EOF
expect_fail "ARCHITECTURE.md drops a core module from its inventory"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/ARCHITECTURE.md")
t = p.read_text()
t = t.replace("├── (removed line)", "├── VectorMath.swift", 1)
p.write_text(t)
EOF

# 10. The doc's own fidelity row re-claims motion channels -> G must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("Optic-flow / small-object motion channels are **NOT emitted**",
              "with luminance and motion channels", 1)
p.write_text(t)
EOF
expect_fail "limitations table re-claims motion channels"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("with luminance and motion channels",
              "Optic-flow / small-object motion channels are **NOT emitted**", 1)
p.write_text(t)
EOF

# 11. The doc quotes a stale connection count for the BANC asset -> J must fail.
$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("3,036,600 connections", "3,032,918 connections", 1)
p.write_text(t)
EOF
expect_fail "doc quotes a stale BANC connection count"

$P - <<'EOF'
from pathlib import Path
p = Path("docs/BIOLOGICAL_LIMITATIONS.md")
t = p.read_text()
t = t.replace("3,032,918 connections", "3,036,600 connections", 1)
p.write_text(t)
EOF

expect_pass
echo
if [ "$fail" -eq 0 ]; then
    echo "ALL MUTATIONS DETECTED — the gate has teeth."
else
    echo "SOME MUTATIONS SURVIVED — the gate is not proving what it claims."
fi
exit "$fail"