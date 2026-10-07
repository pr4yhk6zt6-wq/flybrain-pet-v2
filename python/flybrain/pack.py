# flybrain/pack.py
"""Binary packer — writes .fbpack iOS assets.

Wire format (little-endian, fixed offsets; mirrors Swift Connectome.swift):

    [u64 hdrLen][header JSON (padded to 16)]
    [u64 neuronBytes][NeuronRecord × neuronCount]      stride 44
    [u64 synapseBytes][SynapseRecord × synapseCount]   stride 20
    [u64 rangeBytes][OutEdgeRange × neuronCount]       stride 8
    [u64 regionBytes][RegionBounds × regionCount]      stride 28

NeuronRecord (44): i32 canonicalID; u8 datasetID,region,side,transmitter,
    provenance; u8 flags; u16 type; i32 morphologyIndex,incomingStart,
    incomingCount,outgoingStart,outgoingCount; f32 x,y,z
    (byte offsets: id 0, datasetID 4, region 5, side 6, transmitter 7,
     provenance 8, flags 9, type 10, morphology 12, in 16/20, out 24/28,
     x 32, y 36, z 40 — pinned by python/tests/test_banc.py)

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


def _neuron_bytes(n: NeuronRecord) -> bytes:
    return struct.pack(
        "<iBBBBBBHiiiii3f",
        n.canonicalID, n.datasetID, n.region, n.side,
        n.transmitter, n.provenance, 0, n.type,
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

    hdr = json.dumps(header.to_json_dict(), separators=(",", ":")).encode("utf-8")
    hdr_pad = (-len(hdr)) % 16
    hdr += b"\x00" * hdr_pad

    neuron_blob = b"".join(_neuron_bytes(n) for n in neurons)
    synapse_blob = b"".join(_synapse_bytes(s) for s in synapses)
    range_blob = b"".join(_range_bytes(r) for r in ranges)
    region_blob = b"".join(_region_bytes(r) for r in regions)

    out = b""
    out += struct.pack("<Q", len(hdr)) + hdr
    out += struct.pack("<Q", len(neuron_blob)) + neuron_blob
    out += struct.pack("<Q", len(synapse_blob)) + synapse_blob
    out += struct.pack("<Q", len(range_blob)) + range_blob
    out += struct.pack("<Q", len(region_blob)) + region_blob
    return out


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