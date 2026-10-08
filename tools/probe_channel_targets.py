#!/usr/bin/env python3
"""Every sensory input channel must land on a cell the motor readout does NOT
sum. If it does not, the current enters the readout directly, the connectome is
bypassed, and the "loop" is a short circuit.

The tarsal channel was fixed that way (see `mirror_regional_fixture.py`). This
is the same check for the channels that were NOT fixed, because the defect is
not in one channel: it is in the selection rule every channel shares.

Reads the selection out of `SensoryInterface.inputNeuron` — the single place an
input neuron is chosen — and reports which cell each channel would get.

Exit code is a gate: 1 when any channel lands on a summed cell, so a branch
nobody can exercise cannot pass by printing a number.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "python"))

import mirror_regional_fixture as M          # noqa: E402

SWIFT = os.path.join(HERE, "..", "ios", "Sources", "FlyBrainCore",
                     "SensoryInterface.swift")

# Channels the loop actually drives, and where each one looks. `side` follows
# the call sites in SimulationCore.
CHANNELS = [
    ("tarsal load    -> legNeuromere", M.LEG, 1),
    ("wing strain    -> wingNeuropil", M.WING, 0),
    ("odour          -> antennalLobe", M.AL, 0),
    ("rotation rate  -> haltereNeuropil", 18, 0),
]

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        FAILS.append(msg)


def swift_guards_motor() -> bool:
    """Does the shared selection rule exclude motor-labelled cells?

    The rule lives in `selectInputNeuron`, which every entry point
    (`inputNeuron`, `inputAfferent`, `touchAfferent`) delegates to. Checking
    only `inputNeuron`'s body would pass on a stub that delegates to an
    unguarded helper, which is exactly what happened when the rule was
    factored out.
    """
    src = open(SWIFT).read()
    m = re.search(r"private func selectInputNeuron\(.*?\n    \}", src, re.S)
    return bool(m) and "isMotorNeuron" in m.group(0)


def main() -> int:
    print("Sensory input channel targets (can a sensory current reach a motor cell?)")
    print()

    rule_ok = swift_guards_motor()
    check(rule_ok,
          "inputNeuron excludes motor-labelled cells when other candidates exist"
          + ("" if rule_ok else " — it does not, so every channel below can "
             "land on a cell the readout sums"))

    # Both labelled fixture shapes: the regional (cut) one and the closed-loop
    # one, because the readout's summed set differs between them.
    for topo in ("regional", "closed"):
        regions, index_of, side_of, flags, out = M.build(topo, True)
        summed, by_class = M.readout_sums(index_of, flags)
        print(f"\n  {topo} fixture ({len(regions)} neurons, "
              f"{len(summed)} summed by the readout, labels={by_class})")
        print("    channel                  region           pre-fix   post-fix")
        for label, region, side in CHANNELS:
            ids = index_of.get(region, [])
            if not ids:
                print(f"    {label:24s} {str(region):16s} region absent on this fixture")
                continue
            before = M.input_neuron_pre_fix(index_of, side_of, side, region)
            picked = M.input_neuron(index_of, side_of, flags, side, region)
            bmark = "SUMMED" if before in summed else "ok"
            amark = "SUMMED" if picked in summed else "ok"
            print(f"    {label:24s} {str(region):16s} {before:4d} {bmark:7s} "
                  f"{picked:4d} {amark}")
            if picked in summed:
                check(False,
                      f"{topo}: {label.split(' ->')[0].strip()} must not land on a "
                      f"neuron the readout sums (got {picked})")
            else:
                check(True,
                      f"{topo}: {label.split(' ->')[0].strip()} lands outside the "
                      f"readout (get {picked})")

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)} channel(s) inject straight into the readout.")
        print("  A sensory current that lands on a summed cell bypasses the")
        print("  connectome: the fly moves because the arm is injecting, not")
        print("  because a path exists. That is the defect this gate exists for.")
        return 1
    print("PASSED: no sensory channel injects into the motor readout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())