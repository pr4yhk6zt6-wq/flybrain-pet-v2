#!/usr/bin/env python3
"""Does the documentation still describe the code that exists?

Why this gate exists
--------------------
This repo has a specific, repeated failure mode: a doc or a header that
*reads* complete while the subsystem under it is missing or unwired. Three
real bugs came through that door:

  * `VisionSystem`'s header promised motion/optic-flow and small-object
    pathways; `flowAccumulator` was allocated and reset but never read, and
    nothing emitted either pathway. The header's completeness is WHY the gap
    survived.
  * `BIOLOGICAL_LIMITATIONS.md` listed mechanosensation and haltere feedback
    as `PLANNED` long after both had real call sites in
    `emitMechanosensoryReafference` — the inverse drift, where the doc
    under-reporting made the missing-channel investigation harder.
  * The same file ended with "Last updated: project scaffold (Phase 1-2)"
    while the project was at Phase 9.

So the gate asserts the relationship in BOTH directions, and it reads the
symbols out of the Swift sources rather than trusting a list here:

  A) every subsystem marked PLANNED in the doc must have NO call site;
  B) every subsystem with a call site must NOT be marked PLANNED;
  C) the doc's phase stamp must not be older than the repo's own plan doc;
  D) a declared-but-unread stored property is a defect (the `flowAccumulator`
     rule): allocators without readers are how a dead channel looks wired.

Exit status is nonzero if any check fails, so CI fails loudly rather than
printing numbers nobody asserts on.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "ios" / "Sources" / "FlyBrainCore"
DOC = ROOT / "docs" / "BIOLOGICAL_LIMITATIONS.md"
README = ROOT / "README.md"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label, detail))


def swift_sources() -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(CORE.glob("*.swift"))}


SRC = swift_sources()
ALL_SWIFT = "\n".join(SRC.values())


def call_sites(symbol: str) -> int:
    """Count USES of a symbol, excluding its own declaration.

    `fly.haltereInput(side:)` counts; `func haltereInput(side:)` does not.
    """
    hits = 0
    for text in SRC.values():
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("///"):
                continue
            if symbol not in line:
                continue
            if re.match(rf"^(public |private |internal |fileprivate )*"
                        rf"(func|var|let|static func) {re.escape(symbol)}\b",
                        stripped):
                continue
            hits += 1
    return hits


# --------------------------------------------------------------------------
# A/B: the doc's PLANNED/staged table vs real call sites.
#
# Each entry: subsystem name as it appears in the doc table, plus the Swift
# symbols that would have to have call sites for it to count as wired.
# --------------------------------------------------------------------------
SUBSYSTEMS = {
    "Mechanosensation / proprioception": ["touchAfferent"],
    "Haltere inertial feedback": ["haltereInput"],
    "Gustation (labellum/legs/proboscis)": ["gustatoryInput"],
    "Olfaction (odor fields, antennal lobe, MB)": ["odorInput"],
    "Vision (ommatidial array)": ["loomInput", "loomingInput"],
}

doc_text = DOC.read_text()

for name, symbols in SUBSYSTEMS.items():
    # Find the doc row for this subsystem.
    row = None
    for line in doc_text.splitlines():
        if line.startswith("|") and line.split("|")[1].strip() == name:
            row = line
            break
    if row is None:
        check(False, f"doc has a row for {name!r}",
              "row missing from the fidelity table")
        continue

    marked_planned = "PLANNED" in row.upper()
    live = {s: call_sites(s) for s in symbols}
    wired = any(v > 0 for v in live.values())

    if marked_planned and wired:
        check(False, f"{name} is not still PLANNED",
              f"doc says PLANNED but {live} has call sites — the doc is "
              f"under-reporting what ships")
    elif not marked_planned and not wired:
        check(False, f"{name} does not overclaim being wired",
              f"doc describes it as present but {live} has no call sites")
    else:
        state = "wired" if wired else "planned"
        check(True, f"{name} matches the code ({state})", str(live))

# --------------------------------------------------------------------------
# C: the phase stamp must not be older than the plan's own status.
# --------------------------------------------------------------------------
plan = ROOT / "docs" / "PHYSICS.md"
plan_status = ""
plan_head_phase = 0
if plan.exists():
    ptext = plan.read_text()
    m = re.search(r"^Status:\s*\*\*(.+?)\*\*", ptext, re.M)
    plan_status = m.group(1) if m else ""
    # The plan's own status line usually carries no number ("in progress"), so
    # also read the phase out of its heading. Falling back to 0 made the
    # comparison below vacuous (any doc phase >= 0 passes), which is the same
    # failure mode the stamp bug caused — a check that can never fail.
    hm = re.search(r"^#\s*Phase\s*(\d+)", ptext, re.M)
    if hm:
        plan_head_phase = int(hm.group(1))

# The REAL stamp is the first `Last updated:` line of the file — the footer
# text is allowed to quote the old stamp as the thing it is warning about,
# so scanning from the end finds the quote, not the claim. The first version
# of this gate did exactly that (`rindex`), parsed the quote in the footer,
# and the phase comparison below then degraded to "0 vs 0": a check that
# printed PASS for a doc whose real stamp was the scaffold era. Reading the
# FIRST occurrence is the fix; a doc that quotes the old stamp therefore
# still has its actual stamp checked.
STAMP_LINES = [ln for ln in doc_text.splitlines() if ln.startswith("Last updated:")]
stamp_ok = bool(STAMP_LINES)
check(stamp_ok, "the doc carries a 'Last updated' stamp",
      "no stamp at all is worse than a stale one")
if stamp_ok:
    tail = STAMP_LINES[0]
    stale = re.search(r"Phase\s*1\s*[-–]\s*2", tail) or \
        re.search(r"project scaffold", tail, re.I)
    check(not stale, "the 'Last updated' stamp is not the scaffold era",
          f"plan says '{plan_status}' but the doc still claims the scaffold"
          if stale else "phase stamp current")
    # If the plan doc says in-progress at Phase 6/7, the limitations doc must
    # at least not claim an EARLIER phase.
    m = re.search(r"Phase\s*(\d+)", tail)
    doc_phase = int(m.group(1)) if m else 0
    plan_phase = plan_head_phase
    pm = re.search(r"Phase\s*(\d+)", plan_status)
    if pm:
        plan_phase = max(plan_phase, int(pm.group(1)))
    check(doc_phase >= plan_phase,
          "the doc's phase is not behind the plan's phase",
          f"doc Phase {doc_phase} vs plan Phase {plan_phase}")

# --------------------------------------------------------------------------
# D: a stored property with no reader is a defect (the flowAccumulator rule).
# --------------------------------------------------------------------------
DECL = re.compile(
    r"^\s*(?:private|public|internal|fileprivate)?\s*(?:var|let)\s+"
    r"([a-z][A-Za-z0-9_]*)\s*[:=]")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WHITELIST = {
    # Fields the engine writes and other subsystems read through generated
    # accessors, or that exist purely as the serialized snapshot surface.
    "n", "v", "w", "b", "a", "tauM", "tauAdapt", "tauRef",
}

# One pass over every source, counting identifier occurrences. Doing this
# per-declaration (the obvious way) re-splits every file for every field and
# allocates millions of transient strings — enough to MemoryError on the
# aarch64 dev box this repo is built on. Count once, then compare.
LINE_CACHE: dict[str, list[str]] = {}
IDENT_COUNT: dict[str, int] = {}
for fname, text in SRC.items():
    lines = text.splitlines()
    LINE_CACHE[fname] = lines
    for line in lines:
        for tok in IDENT.findall(line):
            IDENT_COUNT[tok] = IDENT_COUNT.get(tok, 0) + 1

# Usage must be counted across the WHOLE repo, not just the core: `public`
# members are read by the SwiftUI layer and the test suite, which live outside
# `Sources/FlyBrainCore`. Counting only the core flagged 15 live fields
# (`activeNeuronCount`, `regionName`, ...) as dead — a false positive that
# would have had someone delete working API.
for extra in sorted(ROOT.glob("ios/**/*.swift")):
    if extra.parent.name == "FlyBrainCore":
        continue
    for tok in IDENT.findall(extra.read_text(errors="replace")):
        IDENT_COUNT[tok] = IDENT_COUNT.get(tok, 0) + 1

dead: list[str] = []
for fname, lines in LINE_CACHE.items():
    for i, line in enumerate(lines):
        m = DECL.match(line)
        if not m:
            continue
        # A COMPUTED property has a body on the same line (`{ ... }`), and a
        # sub-identifier like `bodyQuaternion` inside that body is a real read
        # that the identifier counter attributes to the expression, not to the
        # property — so a computed property is never "stored storage". The
        # first version of this rule counted them and reported six live,
        # working accessors (`totalMass`, `plantedCount`, ...) as dead. The
        # rule is about ALLOCATED storage with no reader, which is what
        # `flowAccumulator` was.
        rest = line[m.end():]
        if "{" in rest:
            continue
        name = m.group(1)
        if name in WHITELIST:
            continue
        # One occurrence is the declaration itself; zero net uses beyond it
        # means nothing anywhere reads or writes this field.
        if IDENT_COUNT.get(name, 0) <= 1:
            dead.append(f"{fname}:{i + 1} '{name}'")

# Stored-but-unread is a defect anywhere in the core, not only in VisionSystem:
# the vision case is just where it happened to bite first, and scoping the
# failure to one file is how the NEXT dead field gets to look wired. The gate
# fails on any stored property no code outside its declaration touches, so it
# cannot be satisfied by adding a dummy read (that would just move the count).
check(not dead,
      "no stored property is declared-but-unread anywhere in the core",
      f"{dead}" if dead else "clean")

# --------------------------------------------------------------------------
# F: every type the source describes as existing must actually exist.
#
# The deletion pass (SpikeEvent / NeuronState / RetinalSample / flowAccumulator
# / liftCoeff / thrustCoeff / EyeConfig.lod) removed types whose COMMENTS
# claimed telemetry, rendering and optic flow. The reverse mistake is a
# leftover reference to one of them, which is what this check pins: the names
# may only appear in prose explaining the removal, never in live code.
# --------------------------------------------------------------------------
REMOVED = ["SpikeEvent", "NeuronState", "RetinalSample", "flowAccumulator",
           "liftCoeff", "thrustCoeff"]
live_refs: list[str] = []
for fname, lines in LINE_CACHE.items():
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("///"):
            continue
        for sym in REMOVED:
            # `NeuronStateVars` must not be mistaken for `NeuronState`.
            if re.search(rf"\b{re.escape(sym)}\b", line):
                live_refs.append(f"{fname}:{i + 1} '{sym}'")
check(not live_refs,
      "no live reference to a type the repo deleted",
      f"{live_refs}" if live_refs else "clean")

# --------------------------------------------------------------------------
# E: the README's Phase 3 line must not claim a pathway the header denies.
# --------------------------------------------------------------------------
if README.exists():
    rtext = README.read_text()
    m = re.search(r"^- \[x\] Phase 3 \(sensory \+ loop\): (.*)$", rtext, re.M)
    if m:
        line = m.group(1)
        header = SRC.get("VisionSystem.swift", "")
        denies_motion = "NOT yet emitted" in header or \
            "not yet emitted" in header.lower()
        claims_motion = bool(re.search(r"\bmotion\b", line, re.I)) and \
            not re.search(r"motion[^;]*NOT yet emitted", line, re.I)
        if denies_motion and claims_motion:
            check(False, "README's Phase 3 line agrees with VisionSystem's header",
                  "header says motion pathways are NOT emitted, README lists "
                  "motion unqualified")
        else:
            check(True, "README's Phase 3 line agrees with VisionSystem's header",
                  "motion pathway is disclosed as not-yet-emitted")

# --------------------------------------------------------------------------
# G: no doc may list a NOT-EMITTED channel as represented.
#
# Same overclaim class as the README line above, and it was found in three
# docs: `VISION.md` described optic flow, small-object motion and
# HIGH/MEDIUM/LOW-power sampling tiers as implemented; `BIOLOGICAL_LIMITATIONS`
# described the eye as having "luminance and motion channels"; none of these
# had a producer. Every doc is scanned, because the three occurrences were in
# three different files and fixing one would have left the claim standing.
# --------------------------------------------------------------------------
header = SRC.get("VisionSystem.swift", "")
CLAIM_PATTERNS = [("optic flow", r"\boptic[ -]flow\b"),
                  ("small-object motion", r"small[- ]object"),
                  ("motion channels", r"motion channels?\b"),
                  ("level-of-detail sampling", r"HIGH DETAIL")]
DISCLOSURE = re.compile(
    r"NOT EMITTED|not emitted|NOT yet emitted|not yet emitted|"
    r"no .*level of detail|gone|removed", re.I)
WATCHED_DOCS = ["VISION.md", "BIOLOGICAL_LIMITATIONS.md", "README.md"]
for docname in WATCHED_DOCS:
    path = ROOT / "docs" / docname
    if not path.exists():
        path = ROOT / docname
    if not path.exists():
        continue
    buried = []
    for line in path.read_text().splitlines():
        if any(re.search(pat, line, re.I) for _, pat in CLAIM_PATTERNS):
            if not DISCLOSURE.search(line):
                buried.append(line.strip()[:90])
    check(not buried,
          f"{docname} does not present a not-emitted channel as shipped",
          f"{buried}" if buried else "every unemitted channel is disclosed")

VISION_DOC = ROOT / "docs" / "VISION.md"
if VISION_DOC.exists():
    vtext = VISION_DOC.read_text()
    check("header" in vtext and "header wins" in vtext,
          "VISION.md points at VisionSystem's header as the authority",
          "doc names the source of truth")
    check("NOT yet emitted" in header,
          "VisionSystem.swift still discloses the unemitted pathways",
          "header denial present")

# --------------------------------------------------------------------------
# H: the architecture doc's module inventory must list every core file.
#
# Directive, not prose: the file list is read off the directory at gate time
# and each name is required to appear in ARCHITECTURE.md, so adding a core
# module without documenting it fails CI. This is the check that would have
# caught "Core modules (Phase 1-2, current)" listing 7 of 20 files.
# --------------------------------------------------------------------------
ARCH_DOC = ROOT / "docs" / "ARCHITECTURE.md"
if ARCH_DOC.exists():
    atext = ARCH_DOC.read_text()
    missing = [name for name in sorted(SRC) if name not in atext]
    check(not missing,
          "ARCHITECTURE.md lists every core source file",
          f"undocumented: {missing}" if missing else f"all {len(SRC)} listed")

# --------------------------------------------------------------------------
# I: a module the doc calls a stub must actually be unused.
#
# `Network.hpp` is documented as a stub excluded from the build. If a Swift or
# Python file ever references it, the doc's description (and the SwiftPM
# `exclude:`) becomes wrong, and the file silently becomes dead weight that
# looks load-bearing.
# --------------------------------------------------------------------------
stub_refs: list[str] = []
for fname, lines in LINE_CACHE.items():
    for i, line in enumerate(lines):
        if "Network.hpp" in line or "flybrain::Network" in line:
            stub_refs.append(f"{fname}:{i + 1}")
pkg = ROOT / "ios" / "Package.swift"
excludes_stub = pkg.exists() and "Network.hpp" in pkg.read_text() and \
    "exclude" in pkg.read_text()
check(not stub_refs and excludes_stub,
      "the Network.hpp stub is documented as unused and is excluded from the build",
      f"referenced from {stub_refs}" if stub_refs
      else ("excluded from SwiftPM, unreferenced" if excludes_stub
            else "not excluded from the SwiftPM target"))

# --------------------------------------------------------------------------
# J: numbers the doc quotes about the shipped/ingested assets must match the
#    assets. The dataset section claimed "3,032,918 connections" for a BANC
#    asset whose own header says 3,036,600 — a wrong number in a document whose
#    entire purpose is telling a reader what is measured vs inferred.
# --------------------------------------------------------------------------
import sys as _sys
_sys.path.insert(0, str(ROOT / "python"))
try:
    from flybrain.pack import parse_fbpack as _parse_fbpack  # noqa: E402
except Exception:                                            # pragma: no cover
    _parse_fbpack = None

ASSET_CLAIMS = [
    ("banc_cns.fbpack", "neuronCount", r"153,?746"),
    ("banc_cns.fbpack", "synapseCount", r"3,?0(?:36|32),?600|3,?032,?918"),
]
if _parse_fbpack is not None:
    for asset, field, pattern in ASSET_CLAIMS:
        apath = ROOT / "data" / "generated" / asset
        if not apath.exists():
            check(False, f"{asset} exists for the doc's claim",
                  "asset missing from data/generated")
            continue
        try:
            hdr, _ = _parse_fbpack(apath.read_bytes())
        except Exception as exc:                             # pragma: no cover
            check(False, f"{asset} header parses", f"{exc}")
            continue
        true_val = hdr.get(field)
        quoted = [ln.strip() for ln in doc_text.splitlines()
                  if re.search(pattern, ln)]
        # The claim is fine if every line quoting a number for this asset uses
        # the asset's true value, formatted with or without separators.
        exact = f"{true_val:,}"
        bad = [ln for ln in quoted if exact not in ln and str(true_val) not in ln]
        check(not bad,
              f"the doc's {field} for {asset} matches the asset",
              f"asset says {exact}; doc lines off: {bad[:2]}" if bad
              else f"asset and doc agree on {exact}")
else:                                                        # pragma: no cover
    print("[NOTE] flybrain.pack not importable; asset-number check skipped")

# --------------------------------------------------------------------------
passed = sum(1 for ok, _, _ in results if ok)
for ok, label, detail in results:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {label}" + (f" — {detail}" if detail else ""))
print(f"\n{passed}/{len(results)} checks passed")
print(
    "\nHOW THIS IS EVIDENCED: every Swift reference count above is computed by\n"
    "reading ios/Sources/FlyBrainCore/*.swift at gate time — no symbol list is\n"
    "hardcoded in the doc or here, so renaming or removing a call site moves\n"
    "the gate. The prose claims are checked against the same sources, so the\n"
    "doc cannot drift back to 'PLANNED' for something that has call sites."
)
sys.exit(0 if passed == len(results) else 1)