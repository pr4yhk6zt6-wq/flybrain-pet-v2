# flybrain/pack.py
"""Binary packer — writes .fbpack iOS assets.

Wire format (little-endian, fixed offsets; mirrors Swift Connectome.swift):

    [u64 hdrLen][header JSON (padded to 16)]
    [u64 neuronBytes][NeuronRecord × neuronCount]      stride 44
    [u64 synapseBytes][SynapseRecord × synapseCount]   stride 20
    [u64 rangeBytes][OutEdgeRange × neuronCount]       stride 8
    [u64 regionBytes][RegionBounds × regionCount]      stride 28
    [u64 sourceIDBytes][u64 × neuronCount]             stride 8    (v3)

NeuronRecord (44): i32 canonicalID; u8 datasetID,region,side,transmitter,
    provenance; u8 flags; u16 type; i32 morphologyIndex,incomingStart,
    incomingCount,outgoingStart,outgoingCount; f32 x,y,z
    (byte offsets: id 0, datasetID 4, region 5, side 6, transmitter 7,
     provenance 8, flags 9, type 10, morphology 12, in 16/20, out 24/28,
     x 32, y 36, z 40 — pinned by python/tests/test_banc.py)

`canonicalID` is the DENSE SIMULATOR INDEX (the array position, and the
numbering SynapseRecord endpoints use), NOT the source dataset's own ID. The
original ID did not fit in the 44-byte record — the BANC release's Root IDs are
60-bit integers (measured min 720575940381905254) — so v3 appends a separate
u64-per-neuron block rather than widening the field and moving every offset.
See docs/TRACEABILITY.md.

`flags` was reserved and dropped on read; it now carries the MEASURED
cell class (FLAG_MOTOR / FLAG_SENSORY from the release's `Super Class`),
because `region` alone cannot tell a motor neuron from the sensory afferent
sitting in the same neuromere.

The cell-type field is u16, not u8: the real BANC release carries 11,566
distinct cell types, which a u8 vocabulary would silently alias into 256
buckets. The two pad bytes that used to follow provenance now hold the high
half of the wider field plus a reserved flags byte, so the stride and every
later offset are unchanged. Header version is 2; a version-1 asset is rejected
rather than misread.
SynapseRecord (20): i32 pre, post; u16 synapseCount; u8 transmitter; i8 sign;
    u8 confidence, delaySteps; pad2; f32 estimatedEfficacy
OutEdgeRange (8): i32 start, count
RegionBounds (28): u8 region; pad3; f32 minX..maxZ
"""

from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path

from .pid import ConnectomeHeader, NeuronRecord, OutEdgeRange, RegionBounds, SynapseRecord

# Record strides, in the one module that writes them. Tools and tests import
# these instead of restating the numbers: a copied constant does not fail when
# the format moves, it mis-indexes and then asserts about the wrong bytes.
NEURON_STRIDE = 44
SYNAPSE_STRIDE = 20
RANGE_STRIDE = 8
REGION_STRIDE = 28
SOURCE_ID_STRIDE = 8        # v3: one u64 per neuron, original dataset IDs

BLOCK_ORDER = ("neuron", "synapse", "range", "region", "sourceID")

CURRENT_VERSION = 3


# Cell-class bits on the wire (NeuronRecord.flags, byte 9). These are MEASURED
# from the release's `Super Class` column, unlike `region`, which is a reduction
# of an atlas tag. The simulator's motor readout needs them because a neuropil
# like the leg neuromere contains motor neurons, the sensory afferents that
# report into it, and local interneurons all at once (measured: only 1.9% of
# the 9,954 neurons the ingest assigns to legNeuromere are motor).
FLAG_MOTOR = 1 << 0
FLAG_SENSORY = 1 << 1

_SENSORY_SUPER_CLASSES = {"sensory", "sensory_ascending", "sensory_descending"}


def neuron_flags(super_class: str) -> int:
    """Cell-class bits from the release's own `Super Class` label."""
    sc = (super_class or "").strip().lower()
    flags = 0
    if sc == "motor":
        flags |= FLAG_MOTOR
    if sc in _SENSORY_SUPER_CLASSES:
        flags |= FLAG_SENSORY
    return flags


def _neuron_bytes(n: NeuronRecord) -> bytes:
    return struct.pack(
        "<iBBBBBBHiiiii3f",
        n.canonicalID, n.datasetID, n.region, n.side,
        n.transmitter, n.provenance, getattr(n, "flags", 0) or 0, n.type,
        n.morphologyIndex, n.incomingStart, n.incomingCount,
        n.outgoingStart, n.outgoingCount,
        n.x, n.y, n.z,
    )


def _synapse_bytes(s: SynapseRecord) -> bytes:
    return struct.pack(
        "<iiHBbBB2xf",
        s.preNeuron, s.postNeuron, s.synapseCount, s.transmitter,
        s.sign, s.confidence, s.delaySteps,
        s.estimatedEfficacy,
    )


def _range_bytes(r: OutEdgeRange) -> bytes:
    return struct.pack("<ii", r.start, r.count)


def _region_bytes(r: RegionBounds) -> bytes:
    return struct.pack("<B3x6f", r.region, r.minX, r.minY, r.minZ, r.maxX, r.maxY, r.maxZ)


def _source_id_bytes(neurons: list[NeuronRecord]) -> tuple[bytes, bool]:
    """The v3 original-ID block: one u64 per neuron, in array order.

    It is written ONLY when every neuron has a source ID, and the header's
    `hasSourceIDs` records that. A partially-identified asset writes a
    zero-length block instead of padding the unknown slots with 0, because 0 is
    a legal ID and "unknown" must not be spelled the same way as "cell 0" —
    a reader that confuses them would confidently resolve the wrong neuron.
    The count of unidentified neurons is the caller's to report; what matters
    here is that the file never states an ID it does not have.
    """
    if any(n.sourceID == NeuronRecord.NO_SOURCE_ID or n.sourceID < 0
           for n in neurons):
        return b"", False
    return b"".join(struct.pack("<Q", n.sourceID) for n in neurons), True


def pack(data: dict, *, generated_by: str) -> bytes:
    """Serialize a dataset dict (as produced by build_synthetic_demo) to bytes."""
    header: ConnectomeHeader = data["header"]
    neurons: list[NeuronRecord] = data["neurons"]
    synapses: list[SynapseRecord] = data["synapses"]
    ranges: list[OutEdgeRange] = data["outgoingRanges"]
    regions: list[RegionBounds] = data.get("regionBounds", [])

    header.neuronCount = len(neurons)
    header.synapseCount = len(synapses)
    header.morphologyCount = 0
    header.regionCount = len(regions)
    header.generatedBy = generated_by
    header.generationDate = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    neuron_blob = b"".join(_neuron_bytes(n) for n in neurons)
    synapse_blob = b"".join(_synapse_bytes(s) for s in synapses)
    range_blob = b"".join(_range_bytes(r) for r in ranges)
    region_blob = b"".join(_region_bytes(r) for r in regions)
    source_blob, has_source_ids = _source_id_bytes(neurons)
    # The header must describe the block that was actually written, and the
    # block is only written when every neuron has an ID. Setting the flag from
    # the same helper that produced the bytes keeps the two from disagreeing.
    header.hasSourceIDs = has_source_ids
    # The header is serialised AFTER the flag is final: writing it before would
    # ship a header that says false while a block follows it.
    hdr = json.dumps(header.to_json_dict(), separators=(",", ":")).encode("utf-8")
    hdr += b"\x00" * ((-len(hdr)) % 16)

    out = b""
    out += struct.pack("<Q", len(hdr)) + hdr
    out += struct.pack("<Q", len(neuron_blob)) + neuron_blob
    out += struct.pack("<Q", len(synapse_blob)) + synapse_blob
    out += struct.pack("<Q", len(range_blob)) + range_blob
    out += struct.pack("<Q", len(region_blob)) + region_blob
    out += struct.pack("<Q", len(source_blob)) + source_blob
    return out


def parse_fbpack(blob: bytes):
    """Decode a .fbpack into (header, blocks). Mirrors the Swift loader.

    Kept next to the writer so the two cannot drift: this is the reader used to
    check round trips in tests, and a reader that disagrees with the writer is
    exactly the failure the cross-language tests exist to catch.
    """
    (hlen,) = struct.unpack_from("<Q", blob, 0)
    raw = blob[8:8 + hlen].split(b"\x00", 1)[0]
    header = json.loads(raw.decode("utf-8"))
    blocks = {}
    off = 8 + hlen
    for name in BLOCK_ORDER:
        (blen,) = struct.unpack_from("<Q", blob, off)
        off += 8
        blocks[name] = blob[off:off + blen]
        off += blen
    if off != len(blob):
        raise ValueError(f"{len(blob) - off} trailing bytes after {len(blocks)} blocks")
    return header, blocks


def source_ids_from(block: bytes) -> list[int]:
    """The v3 original-ID block as a list of ints."""
    return list(struct.unpack(f"<{len(block) // 8}Q", block))


def write_fbpack(data: dict, path: str | Path, *, generated_by: str = "flybrain/pack.py") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = pack(data, generated_by=generated_by)
    path.write_bytes(blob)
    return path


if __name__ == "__main__":
    from .pid import build_synthetic_demo

    demo = build_synthetic_demo()
    out = sys.argv[1] if len(sys.argv) > 1 else "data/generated/demo_micro.fbpack"
    p = write_fbpack(demo, out)
    print(f"wrote {p} ({p.stat().st_size} bytes, {demo['header'].neuronCount} neurons, "
          f"{demo['header'].synapseCount} synapses)")