#!/usr/bin/env python3
"""What provenance does a NEURON record actually carry in the shipped asset?

Motivation: spec #120 requires that "measured vs inferred data are
distinguished", and `NeuronRecord` carries ONE provenance byte. The BANC
report JSON lists FOUR different provenance levels for four different
attributes of the same neuron:

    topology            RECONSTRUCTED
    neuropil_assignment INFERRED   (atlas tag -> one simulator region)
    soma_position       RECONSTRUCTED
    transmitter         PREDICTED

If the byte is a single constant, then a UI that prints "provenance: X" for a
neuron is stating something about ONE attribute and implying it about four.
This tool MEASURES which it is, on the real bytes, instead of guessing from
the docstring — the same discipline as tools/measure_banc_assumptions.py.

Reads the asset directly (the JSON header plus the neuron block) so the answer
cannot come from a stale report file.

Run: python3 tools/census_neuron_provenance.py [asset]
"""
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fbpack import neuron_at, parse  # noqa: E402

ROOT = HERE.parent
DEFAULT = ROOT / "data/generated/banc_cns.fbpack"
DEMO = ROOT / "data/generated/demo_micro.fbpack"

PROVENANCE = ["MEASURED", "RECONSTRUCTED", "INFERRED",
              "PREDICTED", "APPROXIMATED", "UNKNOWN"]


def read_asset(path):
    """Header + blocks via the SHARED reader.

    This tool used to carry its own block walk with the names
    ("neurons","synapses","ranges","regions") — which are not the names on the
    wire, and which stopped reading after the region block. When v3 appended a
    fifth block, the private copy would have silently ignored it and gone on to
    report confidently about a prefix of the file.
    """
    return parse(path)


def main():
    argv = sys.argv[1:]
    paths = [Path(a) for a in argv] or [DEFAULT, DEMO]
    for path in paths:
        if not path.exists():
            print(f"--- {path.name}: NOT PRESENT (not tracked in git, 68 MB) ---\n")
            continue
        header, blocks = read_asset(path)
        neurons = blocks["neuron"]
        n = int(header["neuronCount"])
        print(f"=== {path.name} ===")
        print(f"header dataProvenance : {header.get('dataProvenance')}")
        print(f"neurons               : {n}")
        print(f"declared neuronCount  : {header.get('neuronCount')}")

        prov = Counter()
        flags = Counter()
        sides = Counter()
        regions = Counter()
        type_max = 0
        type_min = None
        pos_nonzero = 0
        for i in range(n):
            v = neuron_at(neurons, i)
            typ = v["type"]
            pv = v["provenance"]
            prov[pv] += 1
            flags[v["flags"]] += 1
            sides[v["side"]] += 1
            regions[v["region"]] += 1
            type_max = max(type_max, typ)
            type_min = typ if type_min is None else min(type_min, typ)
            if v["x"] or v["y"] or v["z"]:
                pos_nonzero += 1

        distinct_prov = len(prov)
        print(f"\ndistinct provenance values across {n} neurons: {distinct_prov}")
        for pv, c in sorted(prov.items()):
            name = PROVENANCE[pv] if pv < len(PROVENANCE) else f"<{pv}>"
            print(f"    {pv} {name:<14} {c:>8}  {100.0 * c / n:5.1f}%")
        print(f"\ncell-class flags      : "
              f"{ {k: v for k, v in sorted(flags.items())} }")
        print(f"side values           : "
              f"{ {k: v for k, v in sorted(sides.items())} }")
        print(f"distinct regions      : {len(regions)}")
        print(f"type vocabulary used  : {type_min}..{type_max} "
              f"({type_max + 1} slots)")
        print(f"neurons with a nonzero position: {pos_nonzero} "
              f"({100.0 * pos_nonzero / n:.1f}%)")
        if distinct_prov == 1:
            only = next(iter(prov))
            name = PROVENANCE[only] if only < len(PROVENANCE) else only
            print(f"\nRESULT: the neuron provenance byte is a SINGLE CONSTANT "
                  f"({name}).")
            print("        One byte cannot describe four attributes that the "
                  "pipeline")
            print("        itself labels differently (topology RECONSTRUCTED, "
                  "region")
            print("        INFERRED, transmitter PREDICTED). Any UI that prints "
                  "it")
            print("        as 'the' provenance of a neuron overstates what was "
                  "measured.")
        else:
            print(f"\nRESULT: provenance varies per neuron ({distinct_prov} "
                  f"values).")
            print("        Still one byte for four attributes; check whether it "
                  "tracks")
            print("        the attribute with the WEAKEST provenance.")
        print()


if __name__ == "__main__":
    main()