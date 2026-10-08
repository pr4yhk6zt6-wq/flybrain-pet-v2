#!/usr/bin/env python3
"""Validate a compiled .fbpack asset (spec #90).

Usage:
    python3 tools/tests/validate_fbpack.py data/generated/demo_micro.fbpack
Exit code 0 = valid, 1 = invalid.
"""
# import json  # orphaned when the block walk moved to tools/fbpack.py
# import struct  # orphaned when the block walk moved to tools/fbpack.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fbpack import (CURRENT_VERSION, FBPackError,  # noqa: E402
                   NEURON_STRIDE, REGION_STRIDE, SOURCE_ID_STRIDE,
                   SYNAPSE_STRIDE, expected_block_lengths, parse, source_id_at)

STRIDE = {
    "neuron": NEURON_STRIDE,
    "synapse": SYNAPSE_STRIDE,
    "range": 8,
    "region": REGION_STRIDE,
}


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/generated/demo_micro.fbpack")
    if not path.exists():
        print(f"missing asset: {path}"); return 1
    try:
        hdr, blocks = parse(path)
    except FBPackError as exc:
        print(f"INVALID: {exc}")
        return 1
    problems = []
    if hdr.get("magic") != 0x46425031:
        problems.append("bad magic")
    # Version 3 appended the per-neuron original-ID block. A v1 asset has a
    # different byte layout and a v2 asset has no ID block, so accepting any of
    # them would mean silently misreading one of them.
    if hdr.get("version") != CURRENT_VERSION:
        problems.append(f"unsupported version {hdr.get('version')} "
                        f"(this reader is v{CURRENT_VERSION})")
    n_neurons = len(blocks["neuron"]) // STRIDE["neuron"]
    n_syn = len(blocks["synapse"]) // STRIDE["synapse"]
    n_rng = len(blocks["range"]) // STRIDE["range"]
    n_reg = len(blocks["region"]) // STRIDE["region"]
    if n_neurons != hdr.get("neuronCount"):
        problems.append(f"neuron block {n_neurons} != header {hdr.get('neuronCount')}")
    if n_syn != hdr.get("synapseCount"):
        problems.append(f"synapse block {n_syn} != header {hdr.get('synapseCount')}")
    if n_rng != n_neurons:
        problems.append(f"range block {n_rng} != neuron {n_neurons}")
    if len(blocks["neuron"]) % STRIDE["neuron"] or len(blocks["synapse"]) % STRIDE["synapse"]:
        problems.append("unaligned block")
    # Original dataset IDs (v3). The header flag and the block length must agree
    # both ways, and the IDs must be DISTINCT: traceability is a bijection.
    want_src = expected_block_lengths(hdr)["sourceID"]
    if len(blocks["sourceID"]) != want_src:
        problems.append(
            f"sourceID block {len(blocks['sourceID'])} != header says "
            f"{want_src} (hasSourceIDs={hdr.get('hasSourceIDs')})")
    n_ids = len(blocks["sourceID"]) // SOURCE_ID_STRIDE
    ids = [source_id_at(blocks["sourceID"], i) for i in range(n_ids)]
    if ids and len(set(ids)) != len(ids):
        problems.append(f"sourceID block has {len(ids) - len(set(ids))} duplicates")
    org = hdr.get("organism", {})
    if org.get("sex") != "female":
        problems.append("organism must default to adult female (spec #1)")
    # Neuron flags (wire byte 9): the cell class the motor readout selects on.
    # Only the two known bits may appear, and a cell cannot be both a motor
    # neuron and a sensory afferent — the release's `Super Class` is one label.
    FLAG_MOTOR, FLAG_SENSORY = 1 << 0, 1 << 1
    nb = blocks["neuron"]
    n_motor = n_sensory = n_other = 0
    for i in range(n_neurons):
        f = nb[i * STRIDE["neuron"] + 9]
        if f & ~(FLAG_MOTOR | FLAG_SENSORY):
            problems.append(f"neuron {i}: unknown flag bits 0x{f:02x}")
            break
        if (f & FLAG_MOTOR) and (f & FLAG_SENSORY):
            problems.append(f"neuron {i}: motor and sensory at once")
            break
        n_motor += bool(f & FLAG_MOTOR)
        n_sensory += bool(f & FLAG_SENSORY)
        n_other += not f
    if problems:
        print("INVALID:")
        for p in problems:
            print("  !", p)
        return 1
    print(f"VALID {path.name}: {n_neurons} neurons, {n_syn} synapses, "
          f"{n_rng} ranges, {n_reg} regions")
    print(f"  organism: {org.get('species')} ({org.get('sex')}, {org.get('lifeStage')})")
    print(f"  provenance: {hdr.get('dataProvenance')}")
    print(f"  cell class: motor {n_motor}, sensory {n_sensory}, unlabelled {n_other}")
    if ids:
        print(f"  source IDs: {len(ids)} recorded, {len(set(ids))} distinct, "
              f"range {min(ids)}..{max(ids)}")
    else:
        print("  source IDs: none — this asset cannot be traced back to a release")
    return 0


if __name__ == "__main__":
    sys.exit(main())