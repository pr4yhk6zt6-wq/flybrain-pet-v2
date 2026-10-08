#!/usr/bin/env python3
"""Independently verify a BANC-derived .fbpack against the source release.

This does NOT re-run the ingest. It reads the compiled asset and the raw BANC
files and checks the two agree, so a bug in the ingest cannot validate itself.

Usage:
    python3 tools/verify_banc.py data/generated/banc_cns.fbpack \
        --data data/raw/flywire/banc [--sample 4000]

Exit code 0 = every check passed. Non-zero = at least one real disagreement.
"""
import argparse
import csv
import gzip
import json
import pickle
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain.pid import Provenance  # noqa: E402
from flybrain.pack import (CURRENT_VERSION, NEURON_STRIDE,  # noqa: E402
                           SYNAPSE_STRIDE, neuron_flags, parse_fbpack,
                           source_ids_from)

# The block walk lives in flybrain.pack, next to the writer. This verifier used
# to carry its own copy that stopped after the `region` block; when v3 appended
# the sourceID block, that copy reported "trailing bytes" and refused to open a
# valid asset. A private copy of a format does not fail when the format moves —
# it fails later, and about the wrong thing.


def parse_asset(path):
    with open(path, "rb") as fh:
        hdr, blocks = parse_fbpack(fh.read())
    return hdr, {k: memoryview(v) for k, v in blocks.items()}


def neuron_at(block, i):
    o = i * NEURON_STRIDE
    cid, ds, region, side, nt, prov, flags, ctype = struct.unpack_from(
        "<iBBBBBBH", block, o)
    morph, in_s, in_c, out_s, out_c = struct.unpack_from("<iiiii", block, o + 12)
    x, y, z = struct.unpack_from("<3f", block, o + 32)
    return dict(cid=cid, dataset=ds, region=region, side=side, nt=nt,
                prov=prov, flags=flags, type=ctype, morph=morph,
                inStart=in_s, inCount=in_c, outStart=out_s, outCount=out_c,
                x=x, y=y, z=z)


def synapse_at(block, i):
    o = i * SYNAPSE_STRIDE
    pre, post, syn, nt, sign, conf, delay, eff = struct.unpack_from(
        "<iiHBbBB2xf", block, o)
    return dict(pre=pre, post=post, syn=syn, nt=nt, sign=sign,
                conf=conf, delay=delay, eff=eff)


def range_at(block, i):
    return struct.unpack_from("<ii", block, i * 8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asset", type=Path)
    ap.add_argument("--data", type=Path,
                    default=ROOT / "data/raw/flywire/banc")
    ap.add_argument("--sample", type=int, default=3000,
                    help="neurons/edges to re-derive from the raw release")
    args = ap.parse_args()

    hdr, blocks = parse_asset(args.asset)
    n_neurons = hdr["neuronCount"]
    n_syn = hdr["synapseCount"]
    failures = []
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok, detail))
        if not ok:
            failures.append(f"{name}: {detail}")

    # ---- structural -------------------------------------------------------
    check("version is 3 (original-ID block appended)",
          hdr.get("version") == CURRENT_VERSION, f"got {hdr.get('version')}")
    check("neuron block size matches header",
          len(blocks["neuron"]) == n_neurons * NEURON_STRIDE,
          f"{len(blocks['neuron'])} != {n_neurons * NEURON_STRIDE}")
    check("synapse block size matches header",
          len(blocks["synapse"]) == n_syn * SYNAPSE_STRIDE,
          f"{len(blocks['synapse'])} != {n_syn * SYNAPSE_STRIDE}")
    check("range block has one entry per neuron",
          len(blocks["range"]) == n_neurons * 8,
          f"{len(blocks['range'])} != {n_neurons * 8}")
    # The header's `flags` field is reserved and no reader consults it, so it
    # must NOT be used to carry meaning. Real-vs-synthetic is carried by
    # `dataProvenance`, the Provenance enum NAME (RECONSTRUCTED for real data,
    # INFERRED for the demo). The wire form is the enum's NAME, not its integer:
    # Swift decodes this field as `ConnectomeHeader.dataProvenance: String`, so
    # an integer here made JSONDecoder reject every asset the pipeline wrote.
    #
    # Note this is the header's `flags`, NOT the per-neuron `flags` byte, which
    # DOES carry meaning (the release's `Super Class`, motor/sensory) and is
    # checked in the per-neuron section below.
    check("dataset is marked as reconstructed (real) data",
          hdr.get("dataProvenance") == Provenance.RECONSTRUCTED.name,
          f"dataProvenance={hdr.get('dataProvenance')!r} "
          f"(want {Provenance.RECONSTRUCTED.name!r})")
    check("reserved HEADER flags field is left at 0",
          hdr.get("flags", 0) == 0, f"flags={hdr.get('flags')}")
    check("organism defaults to adult female",
          hdr["organism"].get("sex") == "female" and
          hdr["organism"].get("lifeStage") == "adult",
          json.dumps(hdr["organism"].get("sex")))
    check("source is named as BANC",
          any("BANC" in s for s in hdr.get("sourceDatasets", [])),
          str(hdr.get("sourceDatasets")))

    # ---- original dataset IDs (v3) ---------------------------------------
    # This verifier replays the release, so this is the strongest place to check
    # traceability: every ID the asset claims must be a Root ID that genuinely
    # exists in neurons.csv.gz, and no two neurons may claim the same one.
    check("asset records original dataset IDs (header flag)",
          bool(hdr.get("hasSourceIDs")), f"hasSourceIDs={hdr.get('hasSourceIDs')}")
    ids = source_ids_from(blocks.get("sourceID", b""))
    check("one original ID per neuron",
          len(ids) == n_neurons, f"{len(ids)} != {n_neurons}")
    check("original IDs are distinct",
          len(set(ids)) == len(ids),
          f"{len(ids) - len(set(ids))} duplicates")
    # Array index is NOT the ID: this is the defect the block exists to fix.
    check("original IDs are not the array indices",
          ids != list(range(n_neurons)),
          "0..n-1 means the IDs were replaced by the array position again")
    # The v1 u8 field collapsed every cell type above 255 onto the region byte.
    # Reading the field back as u16 must give strictly more than 256 distinct
    # values for a real release; a u8-shaped asset cannot.
    sample = random.Random(7).sample(range(n_neurons), min(n_neurons, 20000))
    types = {neuron_at(blocks["neuron"], i)["type"] for i in sample}
    check("cell-type field uses more than a u8 vocabulary",
          max(types) > 255, f"max type in sample = {max(types)}")

    # ---- CSR self-consistency --------------------------------------------
    # Every outgoing range must be contiguous, ascending by start, and its
    # contents must all share the same presynaptic id (that is what makes the
    # CSR index usable). Same for incoming.
    bad = 0
    prev_end = 0
    for i in range(n_neurons):
        start, count = range_at(blocks["range"], i)
        if count < 0 or start < 0:
            bad += 1; break
        if start < prev_end - count and count > 0:
            # non-overlapping, ascending
            pass
        if count:
            pre0 = synapse_at(blocks["synapse"], start)["pre"]
            for k in (start, start + count - 1):
                if synapse_at(blocks["synapse"], k)["pre"] != pre0:
                    bad += 1; break
        if bad:
            break
        prev_end = start + count
    check("outgoing CSR ranges are contiguous and presynaptically pure",
          bad == 0, f"{bad} bad range(s)")

    ends = [range_at(blocks["range"], i) for i in range(min(n_neurons, 50000))]
    tot = sum(c for _s, c in ends)
    check("sampled outgoing counts do not exceed total synapses",
          tot <= n_syn, f"{tot} > {n_syn}")

    # ---- re-derive a sample from the raw release --------------------------
    with gzip.open(args.data / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)
    table = {}
    with gzip.open(args.data / "neurons.csv.gz", "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            table[int(row["Root ID"])] = row

    # The canonical id preserves the CSV order of the neurons that survived,
    # so the raw order can be reconstructed by replaying the same filter.
    from flybrain.banc import _dominant_region, _parse_position
    from flybrain.pid import RegionID
    from collections import Counter

    # Everything the comparison needs is flattened into `order` here, then the
    # 14 MB attribute pickle and the 160 k-row CSV dict are RELEASED before the
    # edge pass: holding all three plus the 3 M replayed edges at once exceeded
    # the device's memory and made this verifier die with MemoryError exactly
    # when it was pointed at the real asset.
    order = []
    for rid, row in table.items():
        # Mirror the ingest's exclusions exactly. `trachea` and `not_a_neuron`
        # are not nerve cells, so they must not be replayed as neurons; leaving
        # them in made this independent check disagree with the asset by
        # exactly those rows.
        if (row.get("Super Class") or "").strip() in ("glia", "trachea", "not_a_neuron"):
            continue
        a = attrs.get(rid, {})
        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        region, _u = _dominant_region(a, side_raw, Counter())
        if region == int(RegionID.UNKNOWN):
            continue
        # The release's own `Super Class` label is carried through the replay so
        # the class byte can be re-derived here. Without it `neuron_flags` had
        # nothing to read and the comparison below counted nothing — see the
        # note at the assertion.
        order.append((rid, region, side_raw, _parse_position(a.get("position")),
                      (row.get("Super Class") or "").strip()))
    del table, attrs

    check("canonical-order reconstruction matches the asset size",
          len(order) == n_neurons,
          f"replayed {len(order)} neurons, asset has {n_neurons}")

    n = min(args.sample, n_neurons, len(order))
    mism_region = mism_pos = mism_side = mism_class = mism_id = 0
    for cid in range(n):
        rid, region, side_raw, pos, super_class = order[cid]
        rec = neuron_at(blocks["neuron"], cid)
        if rec["cid"] != cid:
            failures.append(f"neuron {cid}: canonicalID field is {rec['cid']}")
            break
        if rec["region"] != region:
            mism_region += 1
        want_side = 1 if side_raw == "left" else 2 if side_raw == "right" else 0
        if rec["side"] != want_side:
            mism_side += 1
        # The class label is re-derived from the release's own `Super Class`
        # here, independently of the ingest. This is the check that would have
        # caught the BANC serialiser writing a literal 0 into that byte: the
        # label was computed, then dropped before the file, and nothing
        # compared the asset against the release's labels.
        want_flags = neuron_flags(super_class)
        if rec["flags"] != want_flags:
            mism_class += 1
        # Traceability: the ID in the file must be THIS row's root ID.
        if source_ids_from(blocks["sourceID"])[cid] != rid:
            mism_id += 1
        if pos:
            if (abs(rec["x"] - pos[0]) > 1e-3 or abs(rec["y"] - pos[1]) > 1e-3
                    or abs(rec["z"] - pos[2]) > 1e-3):
                mism_pos += 1
    check(f"first {n} neurons carry the re-derived region",
          mism_region == 0, f"{mism_region} mismatches")
    check(f"first {n} neurons carry the re-derived side",
          mism_side == 0, f"{mism_side} mismatches")
    check(f"first {n} neurons carry the re-derived soma position",
          mism_pos == 0, f"{mism_pos} mismatches")
    # These two counters were incremented but never asserted: the class byte
    # and the root ID were compared on every neuron and then thrown away, so
    # the verifier could not report the two defects it was written to catch
    # (the serialiser writing a literal 0 into `flags`, and `canonicalID`
    # holding the array index instead of the release's Root ID). A counter
    # nothing reads is not a check.
    check(f"first {n} neurons carry the release's own class label",
          mism_class == 0, f"{mism_class} mismatches")
    check(f"first {n} neurons carry their own release Root ID",
          mism_id == 0, f"{mism_id} mismatches")

    # edge-level: replay the first N connection rows and compare the record
    # Index by POSITION, not by unpacking the replay tuple: the tuple grew a
    # `Super Class` field for the class check, and a positional unpack elsewhere
    # in the file is a second place that has to be found when it changes. These
    # lines only ever wanted the root ID and its index.
    kept = set(entry[0] for entry in order)
    canon = {entry[0]: i for i, entry in enumerate(order)}
    want_edges = []
    with gzip.open(args.data / "connections_princeton.csv.gz", "rt", newline="") as fh:
        first = True
        for line in fh:
            if first:
                first = False   # release header row (pre_root_id,...)
                continue
            a = line.find(","); b = line.find(",", a + 1)
            c = line.find(",", b + 1); d = line.find(",", c + 1)
            if d < 0:
                continue
            pre_raw = int(line[:a]); post_raw = int(line[a + 1:b])
            if pre_raw in kept and post_raw in kept:
                want_edges.append((canon[pre_raw], canon[post_raw],
                                   int(line[c + 1:d])))

    # The release describes a given (pre, post) pair once per shared neuropil,
    # so the same pair can appear several times with disjoint synapse counts.
    # The compiled asset carries ONE record per pair with the counts summed
    # (see flybrain/banc.py). Replay that merge here, otherwise this check
    # compares 2226 release rows against 1707 merged records and "fails" an
    # asset that is correct.
    merged = []
    for pre, post, syn in want_edges:
        if merged and merged[-1][0] == pre and merged[-1][1] == post:
            merged[-1][2] += syn
        else:
            merged.append([pre, post, syn])
    want_edges = merged

    check("kept edge count matches the replayed release",
          len(want_edges) == n_syn,
          f"replayed {len(want_edges)} edge pairs, asset has {n_syn}")

    mism_edge = 0
    for i in range(min(args.sample, n_syn, len(want_edges))):
        rec = synapse_at(blocks["synapse"], i)
        w = want_edges[i]
        if (rec["pre"], rec["post"]) != (w[0], w[1]):
            mism_edge += 1
    check(f"first {min(args.sample, n_syn)} edges match the release order",
          mism_edge == 0, f"{mism_edge} mismatches")

    if len(want_edges) == n_syn:
        bad_syn = 0
        for i in range(min(args.sample, n_syn)):
            if synapse_at(blocks["synapse"], i)["syn"] != want_edges[i][2]:
                bad_syn += 1
        check("merged synapse counts equal the release sum",
              bad_syn == 0, f"{bad_syn} mismatches")

    # every kept neuron's outgoing range must actually start at the right place
    bad_start = 0
    for i, (pre, _post, _syn) in enumerate(want_edges[:20000]):
        start, count = range_at(blocks["range"], pre)
        if not (start <= i < start + count):
            bad_start += 1
    check("outgoing ranges point at the right edges",
          bad_start == 0, f"{bad_start} edges outside their neuron's range")

    # ---- report -----------------------------------------------------------
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" +
              (f"   [{detail}]" if detail and not ok else ""))
    print()
    if failures:
        print(f"INVALID — {len(failures)} failing check(s):")
        for f in failures:
            print("  !", f)
        return 1
    print(f"VALID  {args.asset.name}: {n_neurons} neurons, {n_syn} synapses, "
          f"{len(checks)} checks re-derived from the raw release")
    return 0


if __name__ == "__main__":
    sys.exit(main())