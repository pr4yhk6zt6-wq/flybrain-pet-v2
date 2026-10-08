#!/usr/bin/env python3
"""Rewrite ONLY the JSON header block of an existing .fbpack.

Why this exists: the block layout of a real ingest (20.7M-synapse BANC asset,
~30 minutes + the whole release download) does not change when a header FIELD
changes. Re-running the ingest to fix one metadata string is wasted work and a
chance to produce a different asset. This rewrites the header in place and
copies every data block byte-for-byte, then re-verifies that all four block
lengths still match the header counts.

Usage:
    python3 tools/patch_fbpack_header.py <asset.fbpack>            # show
    python3 tools/patch_fbpack_header.py <asset.fbpack> --apply    # write
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

CHUNK = 1 << 22          # 4 MiB copy window; the asset can be ~70 MB
NEURON_STRIDE = 44
SYNAPSE_STRIDE = 20
RANGE_STRIDE = 8
REGION_STRIDE = 28
SOURCE_ID_STRIDE = 8     # v3


def read_header(blob: bytes) -> tuple[dict, int]:
    (hlen,) = struct.unpack_from("<Q", blob, 0)
    raw = blob[8:8 + hlen].rstrip(b"\x00")
    return json.loads(raw.decode("utf-8")), hlen


def read_block_lens(blob: bytes, hlen: int) -> dict[str, tuple[int, int]]:
    """{name: (length, offset-of-payload)} for the blocks after the header.

    The sourceID block is walked as well, even though this tool does not change
    it: a header rewrite stops when the four data blocks have been copied, so
    leaving the fifth out of the list would DROP it from the rewritten file.
    """
    out = {}
    off = 8 + hlen
    for name in ("neuron", "synapse", "range", "region", "sourceID"):
        (ln,) = struct.unpack_from("<Q", blob, off)
        out[name] = (ln, off + 8)
        off += 8 + ln
    return out


def rewrite(path: Path, *, apply: bool) -> int:
    blob = path.read_bytes()
    hdr, hlen = read_header(blob)
    blocks = read_block_lens(blob, hlen)

    # ---- what we intend to change -----------------------------------------
    before = hdr.get("dataProvenance")
    # Swift decodes this as String; the old file carried the IntEnum raw value.
    fixed = "RECONSTRUCTED" if before in (1, "1", "RECONSTRUCTED") else str(before)
    print(f"{path.name}: dataProvenance {before!r} -> {fixed!r}")
    # This tool rewrites the header ONLY; it cannot add or drop a data block. So
    # it must not be used to re-label a file as a different format version —
    # claiming v3 while the file still ends at the region block would make the
    # loader expect an ID block that is not there.
    print(f"  version: {hdr.get('version')} "
          f"(this tool does not change the layout, only header fields)")

    # ---- sanity: block sizes must agree with the header counts -------------
    checks = [
        ("neuron", blocks["neuron"][0], hdr["neuronCount"] * NEURON_STRIDE),
        ("synapse", blocks["synapse"][0], hdr["synapseCount"] * SYNAPSE_STRIDE),
        ("range", blocks["range"][0], hdr["neuronCount"] * RANGE_STRIDE),
        ("region", blocks["region"][0], hdr["regionCount"] * REGION_STRIDE),
        # The expected ID-block length depends on the header FLAG, not on a
        # count: neuronCount × 8 when the asset records IDs, 0 when it does
        # not. Checking it here means a file whose flag and payload disagree is
        # refused instead of rewritten with the contradiction intact.
        ("sourceID", blocks["sourceID"][0],
         hdr["neuronCount"] * SOURCE_ID_STRIDE if hdr.get("hasSourceIDs") else 0),
    ]
    bad = False
    for name, got, want in checks:
        ok = got == want
        bad |= not ok
        print(f"  {name:<8} block {got:>12} == header count {want:>12}  "
              f"{'ok' if ok else 'MISMATCH'}")
    if bad:
        print("refusing to rewrite: the header does not describe this file",
              file=sys.stderr)
        return 2

    if not apply:
        print("\n(dry run — pass --apply to rewrite the header)")
        return 0

    # ---- rebuild only the header, copy data blocks verbatim ---------------
    hdr["dataProvenance"] = fixed
    new_json = json.dumps(hdr, separators=(",", ":")).encode("utf-8")
    new_json += b"\x00" * ((-len(new_json)) % 16)
    new_hdr_bytes = struct.pack("<Q", len(new_json)) + new_json

    tmp = path.with_suffix(path.suffix + ".tmp")
    data_start = 8 + hlen
    with tmp.open("wb") as out:
        out.write(new_hdr_bytes)
        # Stream the rest so a 70 MB asset never needs a second full copy.
        with path.open("rb") as src:
            src.seek(data_start)
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
    tmp.replace(path)
    print(f"\nrewrote {path} ({path.stat().st_size} bytes)")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    return rewrite(Path(args[0]), apply="--apply" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))