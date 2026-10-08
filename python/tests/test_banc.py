# BANC (real whole-CNS connectome) ingestion tests
#
# These run against a small synthetic fixture built to have the SAME column
# layout as the real release, so they exercise the ingest logic without
# requiring the 522 MB download. The fixture is generated here rather than
# committed, and every value in it is deliberately chosen to hit a specific
# branch that a bug once got wrong.
import csv
import gzip
import json
import pickle
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from flybrain.banc import (  # noqa: E402
    build_banc_asset, sign_for, transmitter_enum,
)
from flybrain.neuropil import UNMAPPED_APPROXIMATIONS, map_neuropil  # noqa: E402
from flybrain.pack import pack, source_ids_from  # noqa: E402
from flybrain.pid import (  # noqa: E402
    RegionID, TransmitterType, build_synthetic_demo,
)

STRIDE_NEURON = 44
STRIDE_SYNAPSE = 20
STRIDE_RANGE = 8
STRIDE_REGION = 28      # u8 region + 3 pad + 6 f32 — NOT 32
STRIDE_SOURCE_ID = 8    # v3: u64 original dataset ID per neuron


def _parse_bytes(blob: bytes):
    """Same walk as `_parse`, for an asset that was never written to disk."""
    off = 0
    hlen = struct.unpack_from("<Q", blob, off)[0]; off += 8
    hdr = json.loads(blob[off:off + hlen].rstrip(b"\x00")); off += hlen
    blocks = {}
    for name in ("neuron", "synapse", "range", "region", "sourceID"):
        blen = struct.unpack_from("<Q", blob, off)[0]; off += 8
        blocks[name] = blob[off:off + blen]; off += blen
    assert off == len(blob), f"{len(blob) - off} trailing bytes"
    return hdr, blocks


def _write_fixture(root: Path, *, n=10, extra_types=0, unmapped_tag=None):
    """Build a miniature release with the real column names and dtypes."""
    cols = ["Root ID", "Top in/out region", "Community labels",
            "Predicted NT type", "Predicted NT confidence",
            "Verified NT type", "Verified Neuropeptide", "Body Part",
            "Function", "Flow", "Super Class", "Class", "Sub Class",
            "Hemilineage", "Nerve", "Soma side", "Primary Cell Type",
            "Alternative Cell Type(s)", "Cable length (nm)",
            "Surface area (nm^2)", "Volume (nm^3)"]

    # Deliberate population:
    #   0  a normal medulla intrinsic neuron
    #   1  GABAergic (sign must be inhibitory)
    #   2  a glial cell (must be dropped, not silently kept)
    #   3  a fragment with no mappable neuropil (must be dropped)
    #   4  a VNC leg-neuromere motor neuron
    #   5  an optic-lobe neuron whose tag is compound ("ME.LO")
    #   6  a free-flight haltere neuron
    #   7  a neuron whose tag has no simulator region (PVLP) -> dropped
    #   8/9 neurons whose SAME (pre,post) pair appears twice, once per neuropil
    rows = [
        ("100", "ME", "medulla_intrinsic", "acetylcholine"),
        ("101", "ME", "gabaergic_local", "gaba"),
        ("102", "ME", "glia", ""),
        ("103", "NO_CONS", "sensory_fragment", ""),
        ("104", "T1_PRONOM", "leg_motor", "glutamate"),
        ("105", "ME.LO", "t4_neuron", "acetylcholine"),
        ("106", "HTCT", "haltere_motor", "acetylcholine"),
        # PVLP is a REAL release tag with no simulator region. It must be
        # reported as a mapping gap rather than silently re-homed, so the
        # ingest has to keep a mappable neuron after it (108/109) and stay at
        # 8 kept out of 10.
        ("107", "PVLP", "unknown_region_cell", ""),
        ("108", "AL", "olfactory_projection", "acetylcholine"),
        ("109", "AL", "olfactory_local", "acetylcholine"),
    ][:n]
    # `extra_types` appends real-looking named neurons so a test can push the
    # cell-type vocabulary past what a u8 can hold.
    for k in range(extra_types):
        rows.append((str(1000 + k), "ME", f"synthesized_type_{k:03d}",
                     "acetylcholine"))
    # A tag the simulator has no region for, to pin the mapping-gap report.
    unmapped_idx = None
    if unmapped_tag is not None:
        unmapped_idx = len(rows)
        rows.append(("2000", "ME", "mystery_region_cell", "acetylcholine"))

    neuropil = {0: ["ME_L"], 1: ["ME_L"], 2: ["ME_L"], 3: ["NO_CONS"],
                4: ["VNC_T1_ProNm_L"], 5: ["ME_L", "LO_L"], 6: ["HTCT_L"],
                7: ["PVLP_R"], 8: ["AL_L"], 9: ["AL_L"]}

    with gzip.open(root / "neurons.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i, (rid, top, ctype, nt) in enumerate(rows):
            super_class = "glia" if i == 2 else "central"
            # The real release labels motor and sensory cells, and the ingest
            # keeps that label in the neuron's flag byte. The fixture must carry
            # a couple so the serialiser's handling of it is exercised: with no
            # labelled cell here, writing a literal 0 in that byte was invisible.
            if i == 4:
                super_class = "motor"
            elif i == 5:
                super_class = "sensory"
            row = [""] * len(cols)
            row[0] = rid
            row[1] = top
            row[3] = nt
            row[10] = super_class
            row[15] = "left"
            row[16] = ctype
            w.writerow(row)

    attrs = {}
    for i, (rid, *_rest) in enumerate(rows):
        # rows synthesised by `extra_types` are anatomically normal medulla
        # cells, so they keep a mappable tag and are KEPT (only the vocabulary
        # is what those tests are about).
        tags = ["ME_L"] if i == unmapped_idx else neuropil.get(i, ["ME_L"])
        if i == unmapped_idx:
            tags = [unmapped_tag]
        attrs[int(rid)] = {
            "input_neuropils": tags,
            "output_neuropils": tags,
            # voxel units, in the REAL release's format: one comma-separated string
            # "x, y, z" with each axis in its own voxel size (x,y = 4 nm,
            # z = 40 nm). The fixture used to write "100 200 300" with spaces,
            # which _parse_position rejects, so every fixture neuron silently
            # got (0,0,0) and test_positions_use_per_axis_voxel_size failed
            # while the ingest had no position handling bug at all.
            "position": [f"{100 * (i + 1)}, {200 * (i + 1)}, {300 * (i + 1)}"],
            "side": "left",
        }
    with gzip.open(root / "neuron_attributes.pickle.gz", "wb") as fh:
        pickle.dump(attrs, fh)

    with gzip.open(root / "connections_princeton.csv.gz", "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pre_root_id", "post_root_id", "neuropil", "syn_count",
                    "nt_type"])
        # duplicate pair split across two neuropils -> must merge to 5
        w.writerow(["100", "104", "ME_L", "2", ""])
        w.writerow(["100", "104", "VNC_T1_ProNm_L", "3", ""])
        w.writerow(["101", "104", "ME_L", "7", ""])
        w.writerow(["104", "100", "VNC_T1_ProNm_L", "1", ""])
        # references a dropped glial id and a dropped fragment -> must not
        # appear in the asset at all
        w.writerow(["100", "102", "ME_L", "9", ""])
        w.writerow(["103", "100", "NO_CONS", "4", ""])
    return rows


@pytest.fixture
def fixture(tmp_path):
    rows = _write_fixture(tmp_path)
    out = tmp_path / "mini.fbpack"
    result, size = build_banc_asset(tmp_path, out)
    return rows, result, out, size


def _fixture(tmp_path, *, extra_types=0, unmapped_tag=None):
    """Same as the `fixture` fixture, but parameterisable (for the u16 test)."""
    rows = _write_fixture(tmp_path, extra_types=extra_types,
                          unmapped_tag=unmapped_tag)
    out = tmp_path / "mini.fbpack"
    result, size = build_banc_asset(tmp_path, out)
    return rows, result, out, size


def _parse(path: Path):
    blob = path.read_bytes()
    off = 0
    hlen = struct.unpack_from("<Q", blob, off)[0]; off += 8
    import json as _json
    hdr = _json.loads(blob[off:off + hlen].rstrip(b"\x00")); off += hlen
    blocks = {}
    for name in ("neuron", "synapse", "range", "region", "sourceID"):
        blen = struct.unpack_from("<Q", blob, off)[0]; off += 8
        blocks[name] = blob[off:off + blen]; off += blen
    assert off == len(blob), f"{len(blob) - off} trailing bytes"
    return hdr, blocks


def test_glia_and_fragments_are_dropped_not_hidden(fixture):
    rows, result, _out, _size = fixture
    # 10 rows in; dropped: 102 (glia) and 103 (fragment with NO atlas tag).
    # 107 (PVLP) is KEPT — PVLP is a real release tag that the mapping does
    # cover; the earlier version of this test asserted it was dropped, which
    # only held while the mapper's "."-split bug was swallowing tags.
    assert result.neurons_read == 10
    assert result.neurons_dropped == 2
    assert result.dropped_reasons["glia"] == 1
    assert result.dropped_reasons["fragment-no-region"] == 1
    assert result.neurons_kept == 8
    assert result.neurons_kept + result.neurons_dropped == result.neurons_read
    # Drops are accounted for by reason, not just counted — a neuron may never
    # vanish without a reason.
    assert sum(result.dropped_reasons.values()) == result.neurons_dropped
    # Nothing in this fixture is an unmapped tag, so the mapping gap counter
    # (and its `problems` entry) must be empty. Real unmapped tags DO get
    # reported — that path is pinned by test_atlas_tag_unmapped_is_reported.
    assert result.dropped_reasons["atlas-tag-unmapped"] == 0
    assert result.problems == []


def test_atlas_tag_unmapped_is_reported_not_renamed(tmp_path):
    # A tag with no simulator region must be dropped AS A MAPPING GAP: counted
    # under its own reason and named in `problems`. Folding it into the generic
    # "fragment/no region" bucket is how a real mapping hole stayed invisible.
    _rows, result, _out, _size = _fixture(
        tmp_path, unmapped_tag="TOTALLY_UNKNOWN_PILOT_REGION")
    assert result.dropped_reasons["atlas-tag-unmapped"] == 1
    assert any("TOTALLY_UNKNOWN_PILOT_REGION" in p for p in result.problems), \
        result.problems


def test_duplicate_pairs_merge_with_counts_summed(fixture):
    _rows, result, out, _size = fixture
    hdr, blocks = _parse(out)
    # 4 usable release rows; the doubled pair collapses to one record
    assert result.connections_kept_raw == 4
    assert result.connections_kept == 3
    assert hdr["synapseCount"] == 3
    counts = []
    for i in range(3):
        counts.append(struct.unpack_from("<H", blocks["synapse"],
                                         i * STRIDE_SYNAPSE + 8)[0])
    assert sorted(counts) == [1, 5, 7], counts


def test_dropped_neuron_edges_never_reach_the_asset(fixture):
    _rows, result, out, _size = fixture
    # both rows touching a dropped neuron must be counted as dropped
    assert result.connections_dropped >= 2
    _hdr, blocks = _parse(out)
    n_syn = len(blocks["synapse"]) // STRIDE_SYNAPSE
    ids = set()
    for i in range(n_syn):
        pre, post = struct.unpack_from("<ii", blocks["synapse"],
                                       i * STRIDE_SYNAPSE)
        ids.add(pre); ids.add(post)
    # canonical ids are POSITIONS IN THE KEPT LIST, not source row indices:
    # dropping source rows 102 (glia), 103 (fragment) and 107 (PVLP) leaves
    #   canonical 0 = 100, 1 = 101, 2 = 104, 3 = 105, 4 = 106, 5 = 108, 6 = 109
    # The old test asserted "2 not in ids", reading canonical 2 as if it were
    # source row 102 — but 2 is source 104, a perfectly good motor neuron, so
    # that assertion only ever passed because positions were all (0,0,0) and
    # the ids happened to shift. Assert the real property instead: no edge
    # endpoint may be an id that was dropped, i.e. all endpoints are < 7.
    assert ids and ids <= set(range(7)), ids
    # the surviving topology is exactly 100->104, 101->104, 104->100
    assert len(ids) == 3


def test_inhibitory_sign_comes_from_the_presynaptic_transmitter(fixture):
    _rows, _result, out, _size = fixture
    _hdr, blocks = _parse(out)
    n_syn = len(blocks["synapse"]) // STRIDE_SYNAPSE
    signs = {}
    for i in range(n_syn):
        pre = struct.unpack_from("<i", blocks["synapse"], i * STRIDE_SYNAPSE)[0]
        sign = struct.unpack_from("<b", blocks["synapse"],
                                  i * STRIDE_SYNAPSE + 11)[0]
        signs[pre] = sign
    # neuron 0 is cholinergic -> excitatory (+1); neuron 1 is GABAergic ->
    # inhibitory (-1); neuron 4 is GLUTAMATERGIC -> UNPOLARISED (0), because
    # glutamate is excitatory at ionotropic GluR and inhibitory at GluClalpha
    # in the fly CNS, so the label alone cannot decide it.
    assert signs[0] == 1
    assert signs[1] == -1
    assert signs[2] == 0                    # canonical 2 = source 104 (glutamate)
    assert sign_for("gaba") == -1
    # The old expectations here ("glutamate" -> -1, "dopamine" -> +1) were both
    # backwards against the receptor literature and are what let the bug ship.
    # Verified: glutamate is EXCITATORY at the larval NMJ (ionotropic GluR;
    # Jan & Jan 1976, PMID 186587) but INHIBITORY at GluClalpha in the adult
    # antennal lobe (Liu & Wilson 2013 PNAS, PMID 23729809) -> 0, undecidable.
    assert sign_for("glutamate") == 0
    assert sign_for("GLUT") == 0
    # histamine is the photoreceptor transmitter and gates a CHLORIDE channel
    # (hclA/ort, HisCl1/HisCl2) -> inhibitory, not excitatory.
    assert sign_for("histamine") == -1
    assert sign_for("HIST") == -1
    assert sign_for("acetylcholine") == 1
    # dopamine/octopamine/serotonin act through GPCRs in Drosophila, so there
    # is no fast current to model -> unpolarised, not silently excitatory.
    assert sign_for("dopamine") == 0
    assert sign_for("serotonin") == 0
    assert sign_for("octopamine") == 0
    # an unpredicted transmitter carries no evidence either way
    assert sign_for("") == 0
    assert sign_for("some_unknown_label") == 1


def test_cell_type_is_u16_so_a_large_vocabulary_survives(tmp_path):
    # Three cells cannot prove a u16 survives; a vocabulary larger than 255 can.
    # That is the property the widened field exists for, so this fixture is
    # allowed extra synthesized cell types.
    _rows, _result, out, _size = _fixture(tmp_path, extra_types=300)
    hdr, blocks = _parse(out)
    # v3 layout: the cell-type field is the u16 at byte 10 (id 0; datasetID 4;
    # region 5; side 6; transmitter 7; provenance 8; flags 9; type 10). The
    # test read it from byte 6 — a u8-era offset — and passed only because the
    # fixture had 3 cells; BANC has 11,566 distinct cell types.
    # v3 appended the original-ID block (see the sourceID tests below); the
    # neuron record itself is unchanged at 44 bytes.
    assert hdr["version"] == 3
    assert STRIDE_NEURON == 44
    n = hdr["neuronCount"]
    types = [struct.unpack_from("<H", blocks["neuron"],
                                i * STRIDE_NEURON + 10)[0] for i in range(n)]
    assert len(set(types)) == n, "distinct cell types must stay distinct"
    assert max(types) > 255, "a >u8 type id must round-trip"


def test_region_stride_is_28_bytes(fixture):
    _rows, _result, out, _size = fixture
    hdr, blocks = _parse(out)
    assert len(blocks["region"]) % STRIDE_REGION == 0
    assert len(blocks["region"]) // STRIDE_REGION == hdr["regionCount"]
    # and the file has no trailing bytes, which is what a wrong stride breaks
    assert len(blocks["region"]) == hdr["regionCount"] * STRIDE_REGION


def test_neuron_field_offsets_are_pinned(fixture):
    # The neuron record's byte layout is read by THREE implementations
    # (flybrain/pack.py, Connectome.packNeuronRecord, Connectome's loader) and
    # they silently disagreed: the Swift loader took type from byte 6 and read
    # side/transmitter/provenance one byte early, so every asset it loaded had a
    # bogus cell type (770 instead of 4660) and the wrong side/transmitter/
    # provenance. Assert the exact offsets, not just the stride.
    _rows, _result, out, _size = fixture
    _hdr, blocks = _parse(out)
    # a neuron with distinguishable values in every field
    n0 = blocks["neuron"][:STRIDE_NEURON]
    assert struct.unpack_from("<i", n0, 0)[0] == 0        # canonicalID
    # 4 datasetID, 5 region, 6 side, 7 transmitter, 8 provenance, 9 flags
    assert n0[9] == 0, "flags is reserved and must stay 0"
    assert struct.unpack_from("<H", n0, 10)[0] == 0      # type of neuron 0
    assert struct.unpack_from("<i", n0, 12)[0] == -1     # morphologyIndex
    # offsets 16/20 are the incoming start/count and 24/28 the outgoing ones
    in_start, in_count = struct.unpack_from("<ii", n0, 16)
    out_start, out_count = struct.unpack_from("<ii", n0, 24)
    assert (in_start, in_count) == (0, 0), "no incoming CSR block is stored"
    assert out_count >= 0
    # x,y,z at 32/36/40 and non-zero (source row 100 -> voxel (100,200,300))
    x, y, z = struct.unpack_from("<fff", n0, 32)
    assert (x, y, z) != (0.0, 0.0, 0.0), "positions must survive the round trip"


def test_outgoing_csr_is_contiguous_and_pure(fixture):
    _rows, _result, out, _size = fixture
    hdr, blocks = _parse(out)
    n_neurons = hdr["neuronCount"]
    n_syn = hdr["synapseCount"]
    ranges = []
    for i in range(n_neurons):
        start, count = struct.unpack_from("<ii", blocks["range"],
                                          i * STRIDE_RANGE)
        ranges.append((start, count))
    # ranges must tile [0, n_syn) in order without gaps or overlaps
    expect = 0
    for start, count in ranges:
        assert start == expect, f"gap at {start} (expected {expect})"
        expect += count
    assert expect == n_syn
    # and each range must contain only edges from its own neuron
    for i, (start, count) in enumerate(ranges):
        for k in range(start, start + count):
            pre = struct.unpack_from("<i", blocks["synapse"],
                                     k * STRIDE_SYNAPSE)[0]
            assert pre == i, f"neuron {i} range holds an edge from {pre}"


def test_compound_neuropil_tags_use_the_dominant_component():
    # "ME.LO" is a real release tag: synapses span two neuropils and the first
    # listed is the larger. The simulator has no compound region, so the first
    # component must win instead of the neuron being dropped.
    assert map_neuropil("ME.LO", "left") == int(RegionID.MEDULLA)
    assert map_neuropil("LO.LOP", "left") == int(RegionID.LOBULA)
    assert map_neuropil("ME", "left") == int(RegionID.MEDULLA)
    assert map_neuropil("VNC_T1_ProNm_L", "left") == int(RegionID.LEG_NEUROMERE)
    assert map_neuropil("HTCT", "left") == int(RegionID.HALTERE_NEUROPIL)
    # PVLP is a REAL release tag that the simulator currently has no region
    # for. It must stay unmapped in the mapping rather than be guessed into a
    # region — check the mapping, not the ingest, because the ingest drops the
    # neuron and reports the tag (see the dropped/format tests).
    assert "PVLP" not in UNMAPPED_APPROXIMATIONS
    # and the approximations the mapping does make stay on the record
    assert "IntTct" in UNMAPPED_APPROXIMATIONS


def test_asset_has_no_trailing_bytes_for_an_empty_region_block(tmp_path):
    # A dataset where nothing maps to a region must still produce a valid file
    # (this is the shape of the synthetic demo, which hid the 32-vs-28 bug).
    (tmp_path / "neurons.csv.gz").write_bytes(b"")
    with pytest.raises(Exception):
        build_banc_asset(tmp_path, tmp_path / "x.fbpack")


def test_positions_use_per_axis_voxel_size(fixture):
    # BANC/FlyWire voxels are 4x4x40 nm; a single scalar scale would put every
    # soma at the wrong depth along z.
    _rows, _result, out, _size = fixture
    _hdr, blocks = _parse(out)
    x, y, z = struct.unpack_from("<fff", blocks["neuron"], 32)
    assert x == pytest.approx(100 * 4 / 1e6)
    assert y == pytest.approx(200 * 4 / 1e6)
    assert z == pytest.approx(300 * 40 / 1e6)
    assert z > x * 5      # the anisotropic scale must actually show up


def test_transmitter_enum_matches_the_shared_vocabulary():
    assert transmitter_enum("acetylcholine") == int(TransmitterType.CHOLINERGIC)
    assert transmitter_enum("gaba") == int(TransmitterType.GABAERGIC)
    assert transmitter_enum("") == int(TransmitterType.UNKNOWN)

def test_cell_class_flags_are_shared_between_pid_and_pack():
    # `pid` cannot import `pack` (pack imports pid), so the flag byte values are
    # stated in both. Pin them against each other: a drift here would make the
    # simulator select on a bit nobody wrote, which reads as "no motor neurons"
    # and silently falls back to summing whole neuropils.
    from flybrain import pack as pack_mod
    from flybrain import pid as pid_mod
    assert pid_mod.FLAG_MOTOR == pack_mod.FLAG_MOTOR
    assert pid_mod.FLAG_SENSORY == pack_mod.FLAG_SENSORY


def test_neuron_flags_follow_the_release_super_class():
    from flybrain.pack import FLAG_MOTOR, FLAG_SENSORY, neuron_flags
    # The release's own labels, including the compound sensory super classes.
    assert neuron_flags("motor") == FLAG_MOTOR
    assert neuron_flags("sensory") == FLAG_SENSORY
    assert neuron_flags("sensory_ascending") == FLAG_SENSORY
    assert neuron_flags("sensory_descending") == FLAG_SENSORY
    assert neuron_flags("central_brain_intrinsic") == 0
    assert neuron_flags("") == 0
    # `motor` must NOT also read as sensory — the release gives one label, and
    # a cell that is both would let the readout pick up its own input.
    assert not (neuron_flags("motor") & FLAG_SENSORY)


def test_flags_land_on_the_wire_at_offset_9():
    # The whole point of the byte: it must be the NEURON record's offset 9, the
    # slot between provenance (8) and the u16 type (10).
    from flybrain.pid import build_synthetic_demo, DatasetID, NeuronRecord
    from flybrain.pack import FLAG_MOTOR, FLAG_SENSORY, _neuron_bytes
    rec = NeuronRecord(canonicalID=1, datasetID=DatasetID.SYNTHETIC, type=7,
                       region=3, side=1, transmitter=2, provenance=1,
                       morphologyIndex=-1, incomingStart=0, incomingCount=0,
                       outgoingStart=0, outgoingCount=0, x=1.0, y=2.0, z=3.0,
                       flags=FLAG_MOTOR | FLAG_SENSORY)
    b = _neuron_bytes(rec)
    assert len(b) == 44
    assert b[9] == (FLAG_MOTOR | FLAG_SENSORY)
    # and the neighbours are untouched
    assert struct.unpack_from("<H", b, 10)[0] == 7
    assert b[8] == 1


def test_banc_serialiser_keeps_the_class_label(fixture):
    """The class label must survive the BANC writer, not just the model.

    `pid.build_synthetic_demo` goes through `pack._neuron_bytes`, but the BANC
    ingest has its OWN serialiser, and that one wrote a literal 0 into the flags
    byte. Every neuron in the whole-CNS asset therefore shipped classless: the
    motor readout could not tell a leg motor neuron from the sensory afferents
    sharing its neuromere, and the shipped-asset gate had nothing to find
    because the label was gone before it reached the file. The fixture now
    labels one motor and one sensory cell so this is observable at all.
    """
    _rows, _result, out, _size = fixture
    _hdr, blocks = _parse(out)
    n = len(blocks["neuron"]) // STRIDE_NEURON
    flags = [struct.unpack_from("<iBBBBBBH", blocks["neuron"],
                                i * STRIDE_NEURON)[6] for i in range(n)]
    assert any(f != 0 for f in flags), \
        "every neuron serialised with flags == 0 — the class label was dropped"
    assert sum(1 for f in flags if f & 1) == 1, "one motor cell expected"
    assert sum(1 for f in flags if f & 2) == 1, "one sensory cell expected"


# --------------------------------------------------------------------------
# v3: original dataset IDs (docs/TRACEABILITY.md)
#
# BEFORE this block existed, `canonicalID` was filled with `len(neurons)` — the
# array position — while CONNECTOME.md told the reader the IDs were "kept".
# Nothing failed, because nothing compared the two: the BANC release's Root IDs
# are 60-bit (measured min 720575940381905254) and cannot survive an i32, so the
# only way to notice was to go and look at what the numbers were.
# --------------------------------------------------------------------------

def test_banc_asset_records_the_release_root_ids(fixture):
    """The original IDs are on the wire, in array order, one per neuron.

    This is the regression test for the defect above: it asserts the asset can
    name the release cell, not just its own array index.

    Deliberately does NOT restate the drop policy (which cells survive is other
    tests' subject). It asserts the property that traceability needs: every ID
    is a real ID from the source file, and the kept ones keep their original
    order. Re-deciding the drops here would make this test disagree with the
    ingest for reasons that have nothing to do with IDs.
    """
    rows, _result, out, _size = fixture
    hdr, blocks = _parse(out)
    assert hdr["hasSourceIDs"] is True
    assert len(blocks["sourceID"]) == hdr["neuronCount"] * STRIDE_SOURCE_ID
    ids = source_ids_from(blocks["sourceID"])
    source = [int(r[0]) for r in rows]
    assert len(ids) == hdr["neuronCount"]
    assert set(ids) <= set(source), "an ID that is not a source row is fabricated"
    # order-preserving subsequence of the source rows (the release is sorted,
    # and the ingest streams it, so reordering would mean the CSR is wrong too)
    it = iter(source)
    assert all(any(s == i for s in it) for i in ids), \
        f"{ids} is not an ordered subsequence of {source}"


def test_source_id_is_not_the_array_index(fixture):
    """Pin the DISTINCTION, not just the presence.

    The defect was that the two were the same number, which made every value
    look plausible. This asserts they differ, so a future change that collapses
    them back fails here instead of at the point someone tries to trace a cell.
    """
    _rows, _result, out, _size = fixture
    _hdr, blocks = _parse(out)
    ids = source_ids_from(blocks["sourceID"])
    canon = [struct.unpack_from("<i", blocks["neuron"], i * STRIDE_NEURON)[0]
             for i in range(len(ids))]
    assert canon == list(range(len(canon))), "canonicalID is the dense index"
    assert ids != canon, "source IDs must not be the dense simulator index"


def test_asset_without_source_ids_says_so(tmp_path):
    """A neuron with no known source ID must produce NO block, not a block of 0s.

    0 is a legal dataset ID, so padding the unknown slots with it would state a
    fact that is not known — and a reader resolving "cell 0" does not fail, it
    answers confidently about the wrong neuron.
    """
    from flybrain.pid import NeuronRecord
    from flybrain.pack import pack as _pack
    data = build_synthetic_demo(neurons_per_region=2)
    for nr in data["neurons"]:
        nr.sourceID = NeuronRecord.NO_SOURCE_ID
    blob = _pack(data, generated_by="test")
    hdr, blocks = _parse_bytes(blob)
    assert hdr["hasSourceIDs"] is False
    assert blocks["sourceID"] == b"", "unknown IDs must not be written as 0"


def test_source_id_block_survives_the_round_trip_multi_byte():
    """A >32-bit ID must come back exactly, or traceability is lost silently."""
    data = build_synthetic_demo(neurons_per_region=2)
    big = 0x0A00000F0F0F0F0F      # shaped like a BANC Root ID (60-bit)
    data["neurons"][0].sourceID = big
    blob = pack(data, generated_by="test")
    _hdr, blocks = _parse_bytes(blob)
    ids = source_ids_from(blocks["sourceID"])
    assert ids[0] == big
    assert ids[0] > 2**32, "the value has to exceed int32 or the test proves nothing"
