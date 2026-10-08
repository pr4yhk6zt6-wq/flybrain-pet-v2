#!/usr/bin/env python3
"""Every sensory channel must resolve to a REAL, non-readout cell on the assets
we actually ship — not just on the hand-written test fixtures.

`probe_channel_targets.py` checks the selection RULE against two synthetic
fixtures whose labels are set by hand. This checks the OUTCOME against the two
committed assets: `demo_micro.fbpack` (what the app bundles and runs) and, when
present, `banc_cns.fbpack` (the real release ingest).

Why it is needed: the class-exclusion rule is right, but the demo asset assigns
cell class from the REGION (`pid._class_flags`), so every one of its 24
wingNeuropil neurons carries the motor bit. The exclusion then skips all 24 and
the channel resolves to nothing at all — `wingStrainInput` returns an empty
list on every step. That is not a short circuit; it is a dead channel, and it is
just as wrong: a fix must not be able to satisfy "no channel drives the readout"
by making the channel silent.

Three failure modes are therefore asserted separately, because they are
different defects with the same symptom:

  SHORTED  the current enters the readout directly (the connectome is bypassed);
  DEAD     no cell resolves, although the region HAS a non-motor candidate;
  MOTOR-ONLY  no cell resolves because every cell in that region carries the
           motor label, so there is no afferent to resolve to. This is a
           property of the ASSET, not of the rule, and it is reported as an
           inventory item — but only after being checked to be exactly that,
           from the same bytes.

Exit code is a gate: 1 for a SHORTED channel or a resolver bug, so a branch
nobody can exercise cannot pass by printing a number.

Run: python3 tools/probe_shipped_asset_channels.py
"""
from __future__ import annotations

# import json  # orphaned when the block walk moved to tools/fbpack.py
import os
import re
# import struct  # orphaned when the block walk moved to tools/fbpack.py
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "python"))

from flybrain.pid import RegionID  # noqa: E402

sys.path.insert(0, HERE)
from fbpack import neuron_at, parse  # noqa: E402

MOTOR = 1 << 0
SENSORY = 1 << 1
STRIDE = 44

ASSETS = [
    ("data/generated/demo_micro.fbpack", "bundled demo (what the app runs)"),
    ("data/generated/banc_cns.fbpack", "real BANC release ingest"),
]

SWIFT = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore",
                     "SensoryInterface.swift")
CORE = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore",
                    "SimulationCore.swift")

# Every sensory channel the loop drives, with the region/side its call site
# asks for. `class_filtered` marks the tarsal arm, which resolves through
# `touchAfferent` rather than `inputNeuron`. Side 0 means "do not care about
# side", which is what `inputAfferent(region:)` asks for.
CHANNELS = [
    ("odour L        -> antennalLobe/1", int(RegionID.ANTENNAL_LOBE), 1, False),
    ("odour R        -> antennalLobe/2", int(RegionID.ANTENNAL_LOBE), 2, False),
    ("looming        -> lobulaPlate", int(RegionID.LOBULA_PLATE), 0, False),
    ("gustation      -> subesophagealZone", int(RegionID.SUBESOPHAGEAL_ZONE), 0, False),
    ("haltere        -> haltereNeuropil", int(RegionID.HALTERE_NEUROPIL), 0, False),
    ("wing strain    -> wingNeuropil", int(RegionID.WING_NEUROPIL), 0, False),
    ("tarsal touch   -> legNeuromere/1", int(RegionID.LEG_NEUROMERE), 1, True),
]

FAILS: list[str] = []


def load(path: str):
    hdr, blocks = parse(path)
    nb = blocks["neuron"]
    cells = []
    for i in range(int(hdr["neuronCount"])):
        v = neuron_at(nb, i)
        cells.append((v["region"], v["side"], v["flags"], v["type"]))
    return hdr, cells


def region_cells(cells, region: int) -> list[int]:
    return [i for i, c in enumerate(cells) if c[0] == region]


def by_class_for(cells, ids: list[int]) -> bool:
    """`byClass` is local to the region being searched, exactly as Swift
    computes it from `connectome.neuronIndices(in:)`."""
    return any(cells[i][2] & MOTOR for i in ids)


def readout_sums(cells) -> set[int]:
    """Neurons `readMotorDrive` would sum: motor cells inside one of the three
    neuropils it consults."""
    summed = set()
    for i, (region, _side, flags, _t) in enumerate(cells):
        if region not in (int(RegionID.LEG_NEUROMERE), int(RegionID.WING_NEUROPIL),
                          int(RegionID.SUBESOPHAGEAL_ZONE)):
            continue
        if flags & MOTOR:
            summed.add(i)
    return summed


def resolve_input_neuron(cells, region: int, side: int) -> int | None:
    """Mirror of `SensoryInterface.selectInputNeuron`, pass for pass. Keep the
    pass ORDER identical to the Swift source: it is the behaviour, and a mirror
    that drifts silently invalidates every number this gate prints."""
    ids = region_cells(cells, region)
    if not ids:
        return None
    by_class = by_class_for(cells, ids)

    # 1. the sensory-labelled cell on the requested side.
    for i in ids:
        if (cells[i][2] & SENSORY) and cells[i][1] == side:
            return i
    # 2. any sensory-labelled cell (rescues a request for a side the region has
    #    none of — every BANC cell is left/right, none is side 0).
    for i in ids:
        if cells[i][2] & SENSORY:
            return i
    # 3. a labelled cell: requested side, then side 0, then any.
    if any(cells[i][2] != 0 for i in ids):
        for wanted in (side, 0):
            for i in ids:
                f = cells[i][2]
                if f == 0 or (f & MOTOR):
                    continue
                if cells[i][1] == wanted:
                    return i
        for i in ids:
            f = cells[i][2]
            if f == 0 or (f & MOTOR):
                continue
            return i
    # 4. positional: the wanted side, then side 0.
    for wanted in (side, 0):
        for i in ids:
            if by_class and (cells[i][2] & MOTOR):
                continue
            if cells[i][1] == wanted:
                return i
    # 5. the region's first non-readout cell.
    for i in ids:
        if by_class and (cells[i][2] & MOTOR):
            continue
        return i
    return None


def resolve_touch_afferent(cells, side: int) -> int | None:
    """Mirror of `SensoryInterface.touchAfferent`. It resolves through the same
    selection rule as every other channel (leg neuromere, requested side), so it
    is not a separate rule any more; keeping it as a named function documents
    which channel is which."""
    return resolve_input_neuron(cells, int(RegionID.LEG_NEUROMERE), side)


def swift_rule_guards_motor() -> bool:
    src = open(SWIFT).read()
    m = re.search(r"private func selectInputNeuron\(.*?\n    \}", src, re.S)
    return bool(m) and "isMotorNeuron" in m.group(0)


def swift_channels_use_afferent() -> bool:
    """The channels that ask for a whole-body signal must go through
    `inputAfferent`, because `side: 0` finds nothing on an asset where every
    cell has a side."""
    src = open(SWIFT).read()
    names = ("lobulaPlate", "subesophagealZone", "haltereNeuropil", "wingNeuropil")
    return all(f"inputAfferent(region: .{n})" in src for n in names)


def swift_readout_classes_only() -> bool:
    """The readout must skip non-motor cells when the asset has classes.

    Matched loosely on the guard's operands rather than on one spelling, so
    reformatting the source does not read as a behaviour change — but the
    operands themselves are the assertion: the condition has to consult the
    class flag AND a mode flag.
    """
    src = open(CORE).read()
    m = re.search(r"if\s+([^\n]*?)\{\s*continue\s*\}", src)
    for match in re.finditer(r"if ([^\n]*)continue", src):
        cond = match.group(1)
        if "isMotorNeuron" in cond and ("classifyByRegion" in cond or "hasCellClasses" in cond):
            return True
    return False


def swift_readout_mode_from_asset() -> bool:
    """The readout's mode must be decided from the connectome, not from which
    cells fired. Reading it off the firing set makes the guard above flip on and
    off with the activity, and every frame it is off the readout sums the
    sensory afferents that share the motor region."""
    src = open(CORE).read()
    return "let classifyByRegion = !connectome.hasCellClasses" in src


def swift_leg_slots_use_type_index() -> bool:
    src = open(CORE).read()
    return "Int(n.type) % 3" in src


def leg_pool_report(cells) -> tuple[int, int, bool]:
    """The leg group splits its six slots by `Int(type) % 3`, so the six slots
    are only addressable if the leg motor pool lands on more than one residue on
    BOTH sides. That is an INVARIANT of a vocabulary index, not a fix for a
    known bug: the asset carries 11,528 distinct type values spanning 0..11,565.
    It is checked because a future change to how `type` is derived (a name
    ordinal, a truncation to u8, a collision) would collapse the slots without
    breaking anything else, and it would show up only as a fly that cannot turn.
    Returns (distinct types in the leg motor pool, distinct `% 3`, whether the
    pool covers three slots on BOTH sides)."""
    ids = region_cells(cells, int(RegionID.LEG_NEUROMERE))
    pool = [i for i in ids if cells[i][2] & MOTOR]
    if not pool:
        return 0, 0, False
    threes = {cells[i][3] % 3 for i in pool}
    sides = {(cells[i][1], cells[i][3] % 3) for i in pool}
    both = all(any(s == side and t == k for s, t in sides) for side in (1, 2) for k in (0, 1, 2))
    return len({cells[i][3] for i in pool}), len(threes), both


def main() -> int:
    print("Sensory channels on the SHIPPED assets (dead channel / shorted channel)")
    print()

    for label, ok in (("selectInputNeuron excludes motor cells "
                       "(read from SensoryInterface.swift)", swift_rule_guards_motor()),
                      ("whole-body channels go through inputAfferent "
                       "(side 0 exists on neither asset)", swift_channels_use_afferent()),
                      ("readMotorDrive selects motor cells only "
                       "(read from SimulationCore.swift)", swift_readout_classes_only()),
                     ("the readout's class mode comes from the ASSET, not the "
                      "firing set (read from SimulationCore.swift)",
                      swift_readout_mode_from_asset())):
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            FAILS.append(label)
    slots = swift_leg_slots_use_type_index()
    print(f"[{'PASS' if slots else 'INFO'}] leg slots still derived from `Int(type) % 3` "
          f"(so `type` must be a vocabulary index)")
    print()

    checked = 0
    motor_only_regions: set[tuple[str, int]] = set()
    skipped: list[str] = []
    for rel, what in ASSETS:
        path = os.path.join(HERE, "..", rel)
        if not os.path.exists(path):
            print(f"\n  {what}: {rel} not present, skipped")
            # Only the 50 KB demo asset is tracked in git; the 69 MB real ingest
            # is a local build artifact (see .gitignore). Saying so in the
            # SUMMARY as well as in the body matters: a CI log that ends with
            # "PASSED: N combinations" reads as if the real asset had been
            # checked when it had not been, which is the kind of quietly-green
            # gate this file exists to avoid.
            skipped.append(rel)
            continue
        hdr, cells = load(path)
        summed = readout_sums(cells)
        labelled = sum(1 for c in cells if c[2] != 0)
        print(f"\n  {what} — {rel}")
        print(f"    {hdr['neuronCount']} neurons, {labelled} carry a class byte, "
              f"{len(summed)} summed by the readout")
        print("      channel                             target   verdict")
        for label, region, side, cf in CHANNELS:
            ids = region_cells(cells, region)
            if not ids:
                print(f"      {label:35s} {'-':>6s}   region absent on this asset")
                continue
            checked += 1
            idx = (resolve_touch_afferent(cells, side) if cf
                   else resolve_input_neuron(cells, region, side))
            if idx is None:
                non_motor = [i for i in ids if not (cells[i][2] & MOTOR)]
                if non_motor:
                    verdict = "DEAD: a non-motor cell exists here, so the resolver is wrong"
                else:
                    verdict = ("MOTOR-ONLY: every cell in this region carries the "
                               "motor label, so there is no afferent to resolve to")
                    motor_only_regions.add((rel, region))
            elif idx in summed:
                verdict = "SHORTED: lands on a summed motor cell"
            else:
                verdict = "ok"
            tgt = "-" if idx is None else str(idx)
            print(f"      {label:35s} {tgt:>6s}   {verdict}")
            if verdict.startswith(("DEAD", "SHORTED")):
                check(False, f"{rel}: {label.split(' ->')[0].strip()} — {verdict}")
            else:
                check(True, f"{rel}: {label.split(' ->')[0].strip()} — "
                            f"{'ok' if verdict == 'ok' else 'no afferent in the asset'}")

        if "banc" in rel:
            distinct, threes, both = leg_pool_report(cells)
            print(f"\n      leg motor pool: {distinct} distinct type indices, "
                  f"`type % 3` covers {threes} slots, both sides covered: {both}")
            check(both, f"{rel}: the six leg slots are addressable on both sides "
                        f"(the type field must stay a vocabulary index, not an ordinal)")
            d, t, _ = leg_pool_report(cells)
            check(t == 3 and d > 3,
                  f"{rel}: leg pool spans more than 3 type indices (got {d})")

    print()
    if motor_only_regions:
        print("ASSET LIMITATION — verbose, not silent:")
        for rel, region in sorted(motor_only_regions):
            name = RegionID(region).name if region in {int(r) for r in RegionID} else str(region)
            print(f"  {rel}: {name} is motor-only, so the channel that reads it has")
            print(f"      no afferent and injects nothing. The channel is inert, NOT")
            print(f"      shorted. A real release labels its afferents (measured:")
            print(f"      banc_cns.fbpack legNeuromere = 4,770 sensory / 187 motor), so")
            print(f"      this is the synthetic asset's grain, not the simulator's rule.")
        print()

    if FAILS:
        print(f"FAILED: {len(FAILS)} problem(s).")
        print("  A DEAD channel is the mirror image of a SHORTED one: the first")
        print("  says the fly senses something there is nothing to sense it with,")
        print("  the second says a sensory current is being read back as the")
        print("  motor command. Quietly satisfying one by causing the other is")
        print("  not a fix, which is why both are asserted on the same bytes.")
        return 1
    print(f"PASSED: {checked} channel/asset combinations — none lands on a "
          f"summed motor cell.")
    print("  HOW THIS IS EVIDENCED: the target arithmetic runs on the ASSET BYTES")
    print("  through a Python mirror of the Swift resolver, so it shows the bytes")
    print("  support a good answer; the [PASS] lines above pin the Swift SOURCE")
    print("  (the exclusion exists, the mode comes from the asset). It does NOT")
    print("  execute Swift — a semantic change inside a pass would not be caught")
    print("  here. The Swift tests in SensoryChannelTests.swift cover that, and")
    print("  they only run in CI.")
    if skipped:
        print(f"  NOT COVERED: {', '.join(skipped)}")
        print("  (the real ingest is not tracked in git; it is verified locally "
              "with tools/verify_banc.py)")
    return 0


def check(cond: bool, msg: str) -> None:
    print(f"      [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILS.append(msg)


if __name__ == "__main__":
    raise SystemExit(main())