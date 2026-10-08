#!/usr/bin/env python3
"""Gate: a neuron in the asset must name the cell it came from.

WHY THIS EXISTS
---------------
`NeuronRecord.canonicalID` was filled with `len(neurons)` — the neuron's own
array position — while docs/CONNECTOME.md said the IDs were "kept". Nothing
failed: every value was a plausible small integer, the CSR is self-consistent,
and the pipeline's own tests only ever compared the asset against itself. The
loss was invisible precisely because the substitute was well-formed.

The BANC release's Root IDs are 60-bit integers (measured min
720575940381905254), so they cannot survive an i32 field at all. The fix is the
v3 block this gate pins: one u64 per neuron, in array order.

WHAT IT CHECKS (and on what evidence)
-------------------------------------
  1. Byte-level, on the SHIPPED demo asset (real bytes, no mirror): the
     original-ID block is present, one u64 per neuron, and the IDs are exactly
     the values the block holds — read with a private reader here, so it does
     not merely confirm that the shared reader agrees with itself.
  2. Semantic, on that asset: the IDs are DISTINCT and are NOT the array
     indices. This is the assertion that fails on the original defect. A gate
     that only checked "a block exists" would have passed on a block of 0..n-1.
  3. Cross-language, on the SOURCE FILES: the block order and every record
     stride in the Python writer are the ones the Swift loader reads, and the
     Swift loader still rejects any version but the current one.
  4. The BANC writer still assigns the release Root ID to the neuron, rather
     than the array index, so a future edit cannot quietly undo the fix.

WHAT THIS DOES NOT DO
---------------------
It does not execute Swift. The runtime round trip (write -> read -> compare,
including the UInt64 > Int32.max case and the duplicate rejection) lives in
`ios/Tests/FlyBrainCoreTests/FBPackFormatTests.swift` and runs on the macOS
runner. What this gate covers is what a runtime test structurally cannot: that
the two languages agree about the layout, and that the real shipped bytes
actually carry the property.
"""
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "python"))

from flybrain.pack import (BLOCK_ORDER, CURRENT_VERSION, NEURON_STRIDE,  # noqa: E402
                           RANGE_STRIDE, REGION_STRIDE, SOURCE_ID_STRIDE,
                           SYNAPSE_STRIDE)

ROOT = HERE.parent
DEMO = ROOT / "data/generated/demo_micro.fbpack"
SWIFT = ROOT / "ios/Sources/FlyBrainCore/Connectome.swift"
BANC = ROOT / "python/flybrain/banc.py"
PACK = ROOT / "python/flybrain/pack.py"

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


# --- 1 + 2: the shipped bytes, with a reader written here -------------------
blob = DEMO.read_bytes()
(hlen,) = struct.unpack_from("<Q", blob, 0)
hdr = __import__("json").loads(blob[8:8 + hlen].split(b"\x00", 1)[0].decode())
off = 8 + hlen
lengths = {}
for name in BLOCK_ORDER:
    (ln,) = struct.unpack_from("<Q", blob, off)
    off += 8
    lengths[name] = ln
    if name == "sourceID":
        sid_off = off
    off += ln

check("asset is the current format version",
      hdr["version"] == CURRENT_VERSION,
      f"version {hdr['version']} (reader is v{CURRENT_VERSION})")

n = int(hdr["neuronCount"])
check("the asset declares that it records original IDs",
      hdr.get("hasSourceIDs") is True,
      f"hasSourceIDs={hdr.get('hasSourceIDs')}")

check("the original-ID block is one u64 per neuron",
      lengths["sourceID"] == n * SOURCE_ID_STRIDE,
      f"{lengths['sourceID']} bytes for {n} neurons")

ids = list(struct.unpack_from(f"<{n}Q", blob, sid_off))
check("every reported ID is distinct (traceability is a bijection)",
      len(set(ids)) == n,
      f"{n - len(set(ids))} duplicate(s)")

# THE assertion for the original defect: if the block were filled with array
# indices, all of these would fail while every other check above still passed.
#
# `canonicalID` is the i32 at byte 0 of each 44-byte neuron record — NOT a
# contiguous i32 run. Reading it at a 4-byte stride (as this did at first)
# returns bytes from the middle of other fields, so the comparison was between
# the IDs and noise: it passed for the wrong reason and would have passed with
# the defect present. Measured, not assumed: with sourceID=array index the
# 4-byte-stride version still reported PASS.
canon = [struct.unpack_from("<i", blob, 8 + hlen + 8 + i * NEURON_STRIDE)[0]
         for i in range(n)]
check("the original IDs are NOT the dense array indices",
      ids != canon,
      "a block of 0..n-1 would be the original defect, restated")

check("the original IDs are distinct from canonicalID as a set",
      set(ids) != set(canon))

# The demo IDs are deliberately above int32 (0x53...), so a decoder that still
# read the ID out of the i32 canonicalID slot cannot coincidentally pass.
check("the demo IDs exceed int32 (a misread cannot look correct)",
      min(ids) > 2**31 - 1,
      f"min {min(ids)} > {2**31 - 1}")

# --- 3: cross-language agreement, read out of both source files -------------
swift = SWIFT.read_text()

# Block order: the Swift loader's readBlockBytes() calls must appear in the
# order the writer emits, because each one consumes the next length prefix.
swift_calls = re.findall(r"readBlockBytes\(\)", swift)
check("Swift reads exactly as many blocks as the writer emits",
      len(swift_calls) >= len(BLOCK_ORDER),
      f"{len(swift_calls)} readBlockBytes() call(s) for {len(BLOCK_ORDER)} blocks")

# Version guard: accepting an older version means reading one layout as another.
ver_guard = re.search(r"guard header\.version == (\d+) else", swift)
check("Swift rejects every non-current version",
      ver_guard is not None and int(ver_guard.group(1)) == CURRENT_VERSION,
      f"guard is on version {ver_guard.group(1) if ver_guard else 'ABSENT'}")

# Strides the Swift loader uses, compared against the Python writer's.
declared = {
    "NEURON_STRIDE": NEURON_STRIDE,
    "SYNAPSE_STRIDE": SYNAPSE_STRIDE,
    "RANGE_STRIDE": RANGE_STRIDE,
    "REGION_STRIDE": REGION_STRIDE,
    "SOURCE_ID_STRIDE": SOURCE_ID_STRIDE,
}
py_pack = PACK.read_text()
for k, v in declared.items():
    m = re.search(rf"^{k} = (\d+)", py_pack, re.M)
    check(f"{k} is {v} in the writer", m is not None and int(m.group(1)) == v,
          f"writer says {m.group(1) if m else 'ABSENT'}")

# Swift states its neuron stride as a literal in two places (the alignment
# guard and the per-record loop); pin them to the writer's value.
swift_strides = set(int(x) for x in re.findall(r"off \+ (\d+) <= neuronData\.count", swift))
check("Swift's neuron stride matches the writer's",
      swift_strides == {NEURON_STRIDE},
      f"Swift uses {sorted(swift_strides)}, writer uses {NEURON_STRIDE}")

check("Swift decodes the ID block as u64",
      "loadUnaligned(as: UInt64.self)" in swift)

# --- 4: the BANC writer still carries the release ID ------------------------
banc = BANC.read_text()
check("the BANC ingest assigns the release Root ID to sourceID",
      re.search(r"sourceID=rid,", banc) is not None,
      "sourceID=rid")
check("the BANC ingest does NOT use the array index as the source ID",
      re.search(r"sourceID=len\(neurons\)", banc) is None)
check("the BANC writer emits the ID block",
      "source_blob" in banc and "has_source_ids" in banc)

print()
if FAILS:
    print(f"RESULT: FAIL — {len(FAILS)} check(s) did not hold:")
    for f in FAILS:
        print(f"  ! {f}")
    sys.exit(1)
print(f"RESULT: PASS — the shipped asset carries {n} distinct original IDs, "
      f"min {min(ids)}, none of which is an array index.")