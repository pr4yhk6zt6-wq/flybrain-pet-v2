#!/usr/bin/env python3
"""Shared reader for the packed `.fbpack` binary format.

Six tools used to carry their own copy of
`for name in ("neuron", "synapse", "range", "region")`. Every one of them had
to be edited in lockstep when the format changed, and the copies that were
forgotten did not fail — they silently read a *prefix* of the file and then
asserted the wrong things about it (a wrong stride does not always raise; it
just mis-indexes). This module makes the block list a single fact.

The format (v3), little-endian:

    [u64 hdrLen][header JSON, padded to 16]
    [u64 n][NeuronRecord  x n]      stride 44
    [u64 n][SynapseRecord x n]      stride 20
    [u64 n][OutEdgeRange  x n]      stride 8
    [u64 n][RegionBounds  x n]      stride 28
    [u64 n][u64 sourceID  x n]      original dataset IDs, 8 bytes each

The last block is **optional by length**: a zero-length block means "this asset
does not record original IDs", which is a different statement from "the ID is
zero". A v1/v2 file has no such block at all and simply runs to EOF after the
region block; `parse()` reports that as an empty block rather than an error so
that tools can still inspect an older asset while reporting its version.

Usage:
    from tools.fbpack import parse
    hdr, blocks = parse("data/generated/demo_micro.fbpack")
    blocks["neuron"], blocks["sourceID"]
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from flybrain.pack import (BLOCK_ORDER, CURRENT_VERSION,  # noqa: E402,F401
                           NEURON_STRIDE, RANGE_STRIDE, REGION_STRIDE,
                           SOURCE_ID_STRIDE, SYNAPSE_STRIDE)
from flybrain.pack import parse_fbpack  # noqa: E402,F401

# `CURRENT_VERSION` and `parse_fbpack` are re-exported for the tools that
# import them from here (tools/tests/validate_fbpack.py takes the version from
# this module), which is why `__all__` names them. A linter cannot see a
# consumer in another file, so both are marked F401 deliberately rather than
# deleted.
__all__ = ["parse", "read_header", "neuron_at", "source_id_at", "CURRENT_VERSION",
           "expected_block_lengths", "VERSION_WITH_SOURCE_IDS",
           "FBPackError", "parse_fbpack"]

# Division of labour, so the format lives in one place:
#   flybrain.pack        — the WRITER, and the authority for the byte layout.
#                          The strides above are imported from it, not restated.
#   parse_fbpack (there) — STRICT reader: exactly five blocks, no trailing
#                          bytes. This is what "the app can open it" means.
#   parse (here)         — TOLERANT inspection reader. It accepts a pre-v3 file
#                          (which simply ends after the region block) so tools
#                          can still look at an older asset and SAY it is older,
#                          instead of refusing to open the evidence.

# Format version that introduced the original-ID block, so a tool can explain
# why an asset has none rather than just returning nothing.
VERSION_WITH_SOURCE_IDS = 3


class FBPackError(ValueError):
    pass


def read_header(blob: bytes) -> tuple[dict, int]:
    """(header dict, total header bytes including the u64 length and padding)."""
    if len(blob) < 8:
        raise FBPackError("truncated: no header length")
    (hlen,) = struct.unpack_from("<Q", blob, 0)
    if 8 + hlen > len(blob):
        raise FBPackError(f"truncated header: declares {hlen} bytes")
    raw = blob[8:8 + hlen].split(b"\x00", 1)[0]
    try:
        hdr = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FBPackError(f"header is not JSON: {exc}") from exc
    return hdr, 8 + hlen


def parse(path, *, allow_missing_source_ids: bool = True):
    """(header, blocks) where each block is a memoryview.

    `blocks["sourceID"]` is an empty view for a pre-v3 file (or a v3 file that
    deliberately records no IDs). Any OTHER missing block is a corrupt file.
    """
    blob = Path(path).read_bytes()
    hdr, off = read_header(blob)
    blocks: dict[str, memoryview] = {}
    for name in BLOCK_ORDER:
        if off == len(blob):
            if name == "sourceID" and allow_missing_source_ids:
                blocks[name] = memoryview(b"")
                continue
            raise FBPackError(f"truncated: no {name} block")
        if off + 8 > len(blob):
            raise FBPackError(f"truncated: {name} block length")
        (blen,) = struct.unpack_from("<Q", blob, off)
        off += 8
        if off + blen > len(blob):
            raise FBPackError(f"truncated: {name} block declares {blen} bytes")
        blocks[name] = memoryview(blob)[off:off + blen]
        off += blen
    if off != len(blob):
        raise FBPackError(f"{len(blob) - off} trailing bytes after the last block")
    return hdr, blocks


def neuron_at(block: memoryview, i: int) -> dict:
    """Decoded NeuronRecord `i` as a dict (v2/v3 field offsets)."""
    o = i * NEURON_STRIDE
    cid, ds, region, side, nt, prov, flags, ctype = struct.unpack_from(
        "<iBBBBBBH", block, o)
    morph, in_s, in_c, out_s, out_c = struct.unpack_from("<iiiii", block, o + 12)
    x, y, z = struct.unpack_from("<3f", block, o + 32)
    return dict(canonicalID=cid, dataset=ds, region=region, side=side, nt=nt,
                provenance=prov, flags=flags, type=ctype, morph=morph,
                incomingStart=in_s, incomingCount=in_c,
                outgoingStart=out_s, outgoingCount=out_c, x=x, y=y, z=z)


def source_id_at(block: memoryview, i: int) -> int:
    return struct.unpack_from("<Q", block, i * SOURCE_ID_STRIDE)[0]


def expected_block_lengths(hdr: dict) -> dict[str, int]:
    """What each block must measure, given the header's declared counts.

    The sourceID block is the one block whose length depends on a header FLAG
    rather than only on a count: it is neuronCount × 8 when the asset records
    IDs, and zero when it does not. Treating 0 as the expected length for a
    traceable asset (or the reverse) would let a truncated file look valid.
    """
    n = int(hdr["neuronCount"])
    ids = n * SOURCE_ID_STRIDE if hdr.get("hasSourceIDs") else 0
    return {
        "neuron": n * NEURON_STRIDE,
        "synapse": int(hdr["synapseCount"]) * SYNAPSE_STRIDE,
        "range": n * RANGE_STRIDE,
        "region": int(hdr["regionCount"]) * REGION_STRIDE,
        "sourceID": ids,
    }