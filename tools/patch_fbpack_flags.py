#!/usr/bin/env python3
"""Set the cell-class byte (wire offset 9) on an existing .fbpack asset.

Why this exists rather than a re-ingest: the byte was already in the record
stride and was written as 0 by every previous packer, so filling it is a
one-byte-per-neuron edit. A full BANC ingest costs ~30 minutes; this is a
streaming pass over two files. `tools/patch_fbpack_header.py` exists for the
same reason (JSON header instead of a 68 MB rewrite).

The classes come from the release's own `Super Class` column — MEASURED, not
inferred. The neurons must be visited in exactly the order the ingest emitted
them, so the filter is imported from the ingest module rather than restated:
a restated filter that drifts would silently write each flag to the wrong
neuron, and a wrong flag is worse than no flag (it would make the motor
readout confidently sum the wrong cells).

Usage:
    python3 tools/patch_fbpack_flags.py \
        --data data/raw/flywire/banc --asset data/generated/banc_cns.fbpack
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain.pack import FLAG_MOTOR, FLAG_SENSORY, neuron_flags  # noqa: E402
from flybrain.pid import RegionID  # noqa: E402

NEURON_STRIDE = 44
FLAGS_OFFSET = 9


def _header(f) -> tuple[dict, int]:
    hl = struct.unpack("<Q", f.read(8))[0]
    raw = f.read(hl)
    return json.loads(raw.decode("utf-8").rstrip("\x00")), (hl + 15) // 16 * 16


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data/raw/flywire/banc"))
    ap.add_argument("--asset", default=str(ROOT / "data/generated/banc_cns.fbpack"))
    ap.add_argument("--out", default=None, help="default: patch in place")
    args = ap.parse_args()

    data = Path(args.data)
    asset = Path(args.asset)
    out = Path(args.out) if args.out else asset

    # The ingest's own filter, so the row order cannot drift from the asset.
    # Imported, not restated: a restated filter that drifts writes each flag to
    # the WRONG neuron, and a confidently wrong motor label is worse than none.
    sys.path.insert(0, str(ROOT / "tools"))
    from flybrain.banc import (_dominant_region,  # noqa: E402
                               _load_neuron_attributes,
                               _load_neuron_table)

    table = _load_neuron_table(data / "neurons.csv.gz")
    attrs = _load_neuron_attributes(data / "neuron_attributes.pickle.gz")
    unmapped_synapses: dict[str, int] = {}
    expected_flags = []
    for rid, row in table.items():
        a = attrs.get(rid, {})
        super_class = (row.get("Super Class") or "").strip()
        if super_class in ("glia", "trachea", "not_a_neuron"):
            continue
        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        # The ingest drops neurons whose atlas tags map to no region (without
        # keep_unmapped_neurons), so they are not in the asset either.
        region, _ = _dominant_region(a, side_raw, unmapped_synapses)
        if region == int(RegionID.UNKNOWN):
            continue
        expected_flags.append(neuron_flags(super_class))

    with asset.open("rb") as f:
        hdr, pad = _header(f)
        n_bytes = struct.unpack("<Q", f.read(8))[0]
        n_neurons = n_bytes // NEURON_STRIDE
        assert n_bytes % NEURON_STRIDE == 0, "neuron block is not stride-aligned"

        if n_neurons != len(expected_flags):
            print(f"FAIL: asset has {n_neurons} neurons but the filtered release "
                  f"yields {len(expected_flags)} — the filter and the asset "
                  f"disagree, so no flag can be attributed safely.")
            return 1

        # Stream-copy everything before the neuron block.
        tmp = Path(tempfile.mkstemp(dir=str(out.parent), suffix=".tmp")[1])
        with tmp.open("wb") as o:
            f.seek(0)
            o.write(f.read(8 + pad + 8))          # magic+hdr, pad, neuronBytes
            block = bytearray(f.read(n_bytes))
            n_motor = n_sensory = 0
            for i, fl in enumerate(expected_flags):
                block[i * NEURON_STRIDE + FLAGS_OFFSET] = fl
                if fl & FLAG_MOTOR:
                    n_motor += 1
                elif fl & FLAG_SENSORY:
                    n_sensory += 1
            o.write(block)
            shutil.copyfileobj(f, o)              # remaining blocks untouched

    shutil.move(str(tmp), str(out))
    print(f"patched {out}")
    print(f"  neurons          : {n_neurons}")
    print(f"  motor flags      : {n_motor}")
    print(f"  sensory flags    : {n_sensory}")
    print(f"  unclassified     : {n_neurons - n_motor - n_sensory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())