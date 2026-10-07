# flybrain pipeline tests (pytest)
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flybrain.pack import _region_bytes, pack
from flybrain.pid import (
    Provenance,
    RegionBounds,
    RegionID,
    TransmitterType,
    build_synthetic_demo,
)
from flybrain.validate import validate_dataset


def test_synthetic_build_is_consistent():
    data = build_synthetic_demo()
    res = validate_dataset(data)
    assert res.ok, res.summarize()
    assert data["header"].organism["sex"] == "female"
    assert data["header"].organism["species"] == "Drosophila melanogaster"


def test_region_record_layout_28_bytes():
    # RegionBounds is read by the Swift loader at a fixed 28-byte stride
    # (u8 region; 3 pad; 6 x f32 min/max XYZ). The loader advanced 32 bytes per
    # record — invisible on the synthetic demo, which has 0 regions, but fatal
    # on a real asset. Pin both the record size and the field order.
    b = _region_bytes(RegionBounds(region=7, minX=-1.5, minY=-2.5, minZ=-3.5,
                                   maxX=1.25, maxY=2.25, maxZ=3.25))
    assert len(b) == 28
    assert b[0] == 7
    assert b[1:4] == b"\x00\x00\x00", "3 pad bytes after the region id"
    got = struct.unpack_from("<6f", b, 4)
    assert got == (-1.5, -2.5, -3.5, 1.25, 2.25, 3.25)


def test_neuron_record_layout_44_bytes():
    data = build_synthetic_demo()
    blob = pack(data, generated_by="test")
    # skip header block: [u64 len][hdr] then [u64 neuronBytes]...
    off = 8 + int.from_bytes(blob[0:8], "little")
    nbytes = int.from_bytes(blob[off:off + 8], "little")
    assert nbytes % 44 == 0
    assert nbytes // 44 == len(data["neurons"])


def test_provenance_all_neurons_inferred_never_measured():
    data = build_synthetic_demo()
    for nr in data["neurons"]:
        assert nr.provenance == int(Provenance.INFERRED), \
            "synthetic dataset must never claim measured data"


def test_synapse_sign_and_transmitter_valid():
    data = build_synthetic_demo()
    for s in data["synapses"]:
        assert s.sign in (-1, 0, 1)
        assert 0 <= s.transmitter <= int(TransmitterType.HISTAMINERGIC)


def test_regions_covered():
    data = build_synthetic_demo()
    regions = {nr.region for nr in data["neurons"]}
    # at least the principal pathways must exist
    for r in (RegionID.LAMINA, RegionID.MEDULLA, RegionID.MUSHROOM_BODY,
              RegionID.VENTRAL_NERVE_CORD, RegionID.LEG_NEUROMERE,
              RegionID.SUBESOPHAGEAL_ZONE, RegionID.WING_NEUROPIL):
        assert int(r) in regions, f"missing region {r}"


def test_roundtrip_pack_preserves_counts():
    data = build_synthetic_demo(neurons_per_region=8)
    blob = pack(data, generated_by="test")
    # header
    hlen = int.from_bytes(blob[0:8], "little")
    assert hlen % 16 == 0
    off = 8 + hlen
    # neuron block
    nlen = int.from_bytes(blob[off:off + 8], "little")
    off += 8
    assert nlen // 44 == len(data["neurons"])
    off += nlen
    slen = int.from_bytes(blob[off:off + 8], "little")
    off += 8
    assert slen // 20 == len(data["synapses"])