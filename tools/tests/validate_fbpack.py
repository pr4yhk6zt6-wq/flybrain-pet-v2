#!/usr/bin/env python3
"""Validate a compiled .fbpack asset (spec #90).

Usage:
    python3 tools/tests/validate_fbpack.py data/generated/demo_micro.fbpack
Exit code 0 = valid, 1 = invalid.
"""
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

STRIDE = {
    "neuron": 44,
    "synapse": 20,
    "range": 8,
    "region": 32,
}


def parse(path: Path):
    blob = path.read_bytes()
    off = 0
    hlen = struct.unpack_from("<Q", blob, off)[0]; off += 8
    hdr = json.loads(blob[off:off + hlen].rstrip(b"\x00")); off += hlen
    blocks = {}
    for name in ("neuron", "synapse", "range", "region"):
        blen = struct.unpack_from("<Q", blob, off)[0]; off += 8
        blocks[name] = blob[off:off + blen]; off += blen
    return hdr, blocks


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/generated/demo_micro.fbpack")
    if not path.exists():
        print(f"missing asset: {path}"); return 1
    hdr, blocks = parse(path)
    problems = []
    if hdr.get("magic") != 0x46425031:
        problems.append("bad magic")
    if hdr.get("version") != 1:
        problems.append(f"unsupported version {hdr.get('version')}")
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
    org = hdr.get("organism", {})
    if org.get("sex") != "female":
        problems.append("organism must default to adult female (spec #1)")
    if problems:
        print("INVALID:")
        for p in problems:
            print("  !", p)
        return 1
    print(f"VALID {path.name}: {n_neurons} neurons, {n_syn} synapses, "
          f"{n_rng} ranges, {n_reg} regions")
    print(f"  organism: {org.get('species')} ({org.get('sex')}, {org.get('lifeStage')})")
    print(f"  provenance: {hdr.get('dataProvenance')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())