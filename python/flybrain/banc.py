# flybrain/banc.py
"""Real BANC ingestion: the whole adult female CNS (brain + VNC) -> .fbpack.

Source (public, no authentication required):
    https://storage.googleapis.com/flywire-data/codex/data/banc/888/
        neurons.csv.gz                 (158,262 neurons, annotations)
        connections_princeton.csv.gz   (3,990,039 traced connections)
        neuron_attributes.pickle.gz    (per-neuron neuropil tags + soma position)

BANC is the dataset the project actually wants (spec #1/#4): brain AND ventral
nerve cord of an adult female, so the closed loop can go
    world -> sensors -> brain -> descending -> VNC leg neuropils -> legs
inside ONE traced animal. FAFB/FlyWire v783 is brain-only and is kept as the
supplement for lamina/ocellar detail.

What is MEASURED vs what we add
-------------------------------
MEASURED (from the release, never invented here):
    * every pre→post connection and its synapse count
    * the neuropil tag(s) each neuron's synapses fall in
    * the soma position (voxel coordinates) and soma side
    * predicted transmitter type(and its confidence, kept as metadata)

DERIVED BY US (kept explicit, never labelled MEASURED):
    * sign (excitatory/inhibitory) — from the predicted transmitter; acetylcholine
      and the amines are treated as excitatory, GABA/glutamate as inhibitory.
      This is an INFERENCE: glutamate is the fast inhibitory transmitter at the
      Drosophila neuromuscular junction and in much of the CNS, but not
      everywhere. Provenance INFERRED, confidence from the NT prediction.
    * efficacy/delay — a uniform INFERRED constant scaled by synapse count, the
      same convention the synthetic pipeline uses. Not measured.
    * type index — a stable ordinal over the dataset's cell-type vocabulary.

Not included, and why (honesty ledger)
--------------------------------------
    * muscles / proprioceptor morphology: absent from this release
    * synaptic delay and release probability: not measured in any release
    * coordinates are soma voxels, NOT synapse-resolution positions
"""

from __future__ import annotations

import csv
import gzip
import json
import pickle
import struct
import time
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import pack
from .neuropil import NT_TAG_MAP, map_neuropil, strip_side
from .pid import (ConnectomeHeader, DatasetID, NeuronRecord, OutEdgeRange,
                  Provenance, RegionBounds, RegionID, TransmitterType)

BANC_VERSION = "banc-888"
BANC_SOURCE = "https://storage.googleapis.com/flywire-data/codex/data/banc/888"

# Voxel size of the BANC/FlyWire template (nm). The FAFB v14 template is 4x4x40
# nm voxels; the same grid is used for BANC. Positions are therefore converted
# to millimetres as  x_mm = voxel * 4e-6  and  z_mm = voxel * 4e-5.
VOXEL_XY_NM = 4.0
VOXEL_Z_NM = 40.0
NM_PER_MM = 1e6


@dataclass
class BancIngestResult:
    dataset: str = BANC_VERSION
    data_dir: str = ""
    neurons_read: int = 0
    connections_read: int = 0
    neurons_kept: int = 0
    neurons_dropped: int = 0
    connections_kept: int = 0
    connections_kept_raw: int = 0
    connections_dropped: int = 0
    dropped_below_threshold: int = 0
    missing_position: int = 0
    asset_bytes: int = 0
    type_vocabulary_size: int = 0
    regions_filled: Counter = field(default_factory=Counter)
    region_unmapped_synapses: Counter = field(default_factory=Counter)
    transmitter_counts: Counter = field(default_factory=Counter)
    dropped_reasons: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)

    def summarize(self) -> str:
        return (f"BANC ingest: {self.neurons_kept}/{self.neurons_read} neurons, "
                f"{self.connections_kept}/{self.connections_read} connections, "
                f"{self.connections_dropped} dropped (of which "
                f"{self.dropped_below_threshold} below threshold), "
                f"{self.neurons_dropped} neurons dropped, "
                f"{len(self.problems)} problems")


# ---------------------------------------------------------------------------
# Transmitter -> sign / transmitter enum
# ---------------------------------------------------------------------------

# The inhibitory set is deliberate: GABA and glutamate are the two inhibitory
# transmitters used in the fly CNS (glutamate at the NMJ and in many central
# circuits). Acetylcholine and the biogenic amines are excitatory or modulatory
# and are treated as excitatory here. HISTAMINE is the photoreceptor
# transmitter and is excitatory (sign-preserving). This is INFERRED, not
# measured; per-synapse receptor identity is not in the release.
# Transmitters whose receptor family is metabotropic (GPCR) in Drosophila, so
# the engine cannot apply them as a fast excitatory/inhibitory current. Verified
# against the receptor literature: DA -> DopR/DopEcR, SER -> 5-HT1/2/7,
# OCT -> OAMB/OctbetaR, TYR -> TyrR/TAR1. The ionotropic amine-gated channels
# (serotonin-gated MOD-1, tyramine-gated LGC-55) are C. elegans and are NOT
# present in the fly. Sign 0 here means "no modelled fast current" — NOT "no
# effect": GPCR neuromodulation can depolarise or hyperpolarise, and the
# simulator loses that.
_MODULATORY = {"DA", "SER", "OCT", "TYR", "SHT", "NEUROPEPTIDE", "NPF", "PDF"}

# Fast inhibitory transmitters in Drosophila.
#   GABA — ligand-gated Cl- channel Rdl/GBRC. The canonical fast inhibitor.
#   HIST — histamine-gated Cl- channels (hclA/ort, HisCl1/HisCl2) hyperpolarise
#          lamina monopolar cells L1-L3; this is the photoreceptor transmitter
#          and it is INHIBITORY, not "sign-preserving". (Gengs 2002 JBC PMID
#          12196539; Zheng 2002 JBC PMID 11714703.)
# GLUT is deliberately NOT in this set: in the fly CNS glutamate is BOTH
# excitatory (ionotropic GluR; the larval NMJ is glutamatergic and excitatory,
# Jan&Jan 1976 PMID 186587) and inhibitory (GluClalpha; Liu & Wilson 2013 PNAS
# PMID 23729809 showed GluClalpha-RNAi-sensitive hyperpolarisation in the
# antennal lobe). Its valence is cell-type-specific, so a bare "GLUT" label
# cannot decide it and is left unpolarised below.
_INHIBITORY = {"GABA", "HIST"}

# The one transmitter whose valence is genuinely ambiguous from the label alone.
_AMBIGUOUS = {"GLUT"}


def transmitter_enum(nt_tag: str) -> int:
    name = NT_TAG_MAP.get((nt_tag or "").strip().lower())
    if name is None:
        return int(TransmitterType.UNKNOWN)
    return int(TransmitterType[name])


def sign_for(nt_tag: str) -> int:
    """+1 excitatory, -1 inhibitory, 0 unpolarised. INFERRED from NT prediction.

    An UNPREDICTED transmitter must not become "+1 excitatory". The release
    leaves the prediction empty for 8,780 neurons, and the engine applies the
    sign unconditionally, so returning +1 there would assert excitation that was
    never measured. 0 keeps those edges present but unpolarised.

    Both the abbreviations the release uses ("GLUT", "ACH") and the full names
    ("glutamate", "acetylcholine") are accepted: the BANC release's own ATPsynthase
    columns and the FlyWire "top_nt" column spell them differently, and a
    name-shaped tag used to fall through to +1.
    """
    tag = (nt_tag or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not tag:
        return 0
    # Normalise the long spellings to the abbreviations the sets use, so
    # "glutamate" and "GLUT" cannot disagree. NT_TAG_MAP is the shared
    # vocabulary (flybrain/neuropil.py) and holds both releases' spellings.
    canonical = NT_TAG_MAP.get(tag.lower())
    if canonical:
        tag = {"CHOLINERGIC": "ACH", "GABAERGIC": "GABA",
               "GLUTAMATERGIC": "GLUT", "DOPAMINERGIC": "DA",
               "SEROTONERGIC": "SER", "OCTOPAMINERGIC": "OCT",
               "TYRAMINERGIC": "TYR", "HISTAMINERGIC": "HIST"}.get(
                   canonical, tag)
    first = tag.split("_")[0]
    if tag in _AMBIGUOUS or first in _AMBIGUOUS:
        return 0                      # iGluR and GluClalpha both exist
    if tag in _MODULATORY or first in _MODULATORY:
        return 0                      # GPCR-only: no modelled fast current
    if tag in _INHIBITORY or first in _INHIBITORY:
        return -1
    if first in ("GABAERGIC", "HISTAMINERGIC", "GLYCINERGIC"):
        return -1
    if first in ("GLUTAMATERGIC", "DOPAMINERGIC", "SEROTONERGIC",
                 "OCTOPAMINERGIC", "TYRAMINERGIC"):
        return 0
    return 1


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_neuron_table(path: Path) -> dict[int, dict]:
    """Read neurons.csv.gz keyed by root id."""
    out: dict[int, dict] = {}
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                rid = int(row["Root ID"])
            except (TypeError, ValueError):
                continue
            out[rid] = row
    return out


def _load_neuron_attributes(path: Path) -> dict[int, dict]:
    """Read neuron_attributes.pickle.gz: neuropil tags + soma position.

    The pickle is already keyed by the integer root id (verified on banc-888),
    so it is returned as-is: rebuilding it as {int(k): v} would double the peak
    footprint of this 158k-entry dict of dicts for no gain, and this dict is
    alive at the same time as the CSV table.
    """
    with gzip.open(path, "rb") as fh:
        raw = pickle.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a dict keyed by root id")
    return raw


def _parse_position(v) -> tuple[float, float, float] | None:
    """The release stores position as ['x, y, z'] voxel strings."""
    if not v:
        return None
    entry = v[0] if isinstance(v, (list, tuple)) else v
    if isinstance(entry, str):
        parts = [p.strip() for p in entry.split(",")]
    else:
        parts = [str(p) for p in entry]
    if len(parts) != 3:
        return None
    try:
        x, y, z = (float(p) for p in parts)
    except ValueError:
        return None
    return (x * VOXEL_XY_NM / NM_PER_MM,
            y * VOXEL_XY_NM / NM_PER_MM,
            z * VOXEL_Z_NM / NM_PER_MM)


def _dominant_region(attrs: dict, soma_side: str,
                     unmapped: Counter) -> tuple[int, bool]:
    """Region of a neuron from its innervated neuropils.

    HONESTY NOTE (measured on banc-888, see tools/measure_tag_order.py): the
    release's tag lists are NOT ordered by descending synapse count — among
    multi-tag neurons the first-listed tag equals the measured argmax only 60.2%
    of the time (input list) / 46.5% (output list), and only 17.9% of input
    lists are themselves descending. There is no per-tag synapse count in the
    release either (the `distance`/`neuropil` columns are per-CONNECTION), so no
    component can be shown to be "the dominant" one from the data we have.

    So this is an explicit, documented CHOICE, not a measurement: take the first
    tag that maps, input list before output list. The region and the tag are
    RECONSTRUCTED; the reduction of a (often compound) tag to one simulator
    region is INFERRED and is recorded as such in the ingest report.
    """
    if not (attrs.get("input_neuropils") or attrs.get("output_neuropils")):
        return int(RegionID.UNKNOWN), True

    # Prefer the leading tag that maps; remember the best unknown one so the
    # caller can report which release tag the mapping is missing.
    best_unknown = None
    for key in ("input_neuropils", "output_neuropils"):
        for tag in attrs.get(key) or []:
            region = map_neuropil(tag, soma_side)
            if region is not None:
                return region, False
            if best_unknown is None:
                best_unknown = tag
    if best_unknown is not None:
        unmapped[best_unknown] += 1
    return int(RegionID.UNKNOWN), True


# ---------------------------------------------------------------------------
# Streaming writer
#
# A whole-BANC asset at the ≥1-synapse threshold is 20.7M synapse records
# (414 MB packed). Holding the record objects AND the packed bytes in one
# Python process exceeds the ~1.5 GB per-process memory cap on the target
# device (measured: bytearray allocation fails at 1500 MB, RLIMIT_AS is
# unlimited). So the writer appends each block to the file as it is produced
# and keeps only the small per-neuron arrays in memory. Peak RSS stays flat in
# the number of synapses, which is what makes this run at all on-device.
# ---------------------------------------------------------------------------

def _write_block_header(fh, length: int) -> None:
    """Each block is preceded by its length as a u64 (see flybrain/pack.py)."""
    fh.write(struct.pack("<Q", length))


def _write_banc_asset(out_path: Path, *, header: ConnectomeHeader,
                      neurons: list[NeuronRecord],
                      ordered, pre_nt_types: list[str],
                      outgoing: list[OutEdgeRange],
                      region_bounds: list[RegionBounds],
                      generated_by: str) -> int:
    """Stream a .fbpack to disk. Returns the final file size in bytes."""
    import struct as _struct

    header.neuronCount = len(neurons)
    header.synapseCount = len(ordered) // 3
    header.morphologyCount = 0
    header.regionCount = len(region_bounds)
    header.generatedBy = generated_by
    header.generationDate = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    hdr = json.dumps(header.to_json_dict(), separators=(",", ":")).encode("utf-8")
    hdr += b"\x00" * ((-len(hdr)) % 16)

    tmp = out_path.with_suffix(out_path.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    neuron_bytes = len(neurons) * 44
    synapse_bytes = (len(ordered) // 3) * 20
    range_bytes = len(outgoing) * 8
    region_bytes = len(region_bounds) * 28   # u8 + pad3 + 6xf32
    with open(tmp, "wb") as fh:
        fh.write(struct.pack("<Q", len(hdr))); fh.write(hdr)

        # Each block is length-prefixed so the reader can skip it without
        # parsing. The sizes are known up front (fixed stride per record), so
        # the file can be streamed without buffering a whole block.
        fh.write(struct.pack("<Q", neuron_bytes))
        buf = bytearray()
        for n in neurons:
            buf += _struct.pack(
                "<iBBBBBBHiiiii3f",
                n.canonicalID, n.datasetID, n.region, n.side,
                n.transmitter, n.provenance, 0, n.type,
                n.morphologyIndex, n.incomingStart, n.incomingCount,
                n.outgoingStart, n.outgoingCount,
                n.x, n.y, n.z)
            if len(buf) >= 1 << 22:
                fh.write(buf); buf.clear()
        fh.write(buf); buf.clear()
        assert neuron_bytes == fh.tell() - (8 + len(hdr) + 8)

        fh.write(struct.pack("<Q", synapse_bytes))
        buf = bytearray()
        for i in range(0, len(ordered), 3):
            pre = ordered[i]; post = ordered[i + 1]; syn = ordered[i + 2]
            # sign from the presynaptic neuron's predicted transmitter: the
            # release's per-connection neurotransmitter column is empty for
            # every BANC row, so this is INFERRED and recorded as such.
            nt = pre_nt_types[pre]
            eff = min(1.0, 0.25 + 0.05 * syn)
            buf += _struct.pack("<iiHBbBB2xf", pre, post, syn,
                                transmitter_enum(nt), sign_for(nt), 60, 1, eff)
            if len(buf) >= 1 << 22:
                fh.write(buf); buf.clear()
        fh.write(buf); buf.clear()

        fh.write(struct.pack("<Q", range_bytes))
        buf = bytearray()
        for r in outgoing:
            buf += _struct.pack("<ii", r.start, r.count)
            if len(buf) >= 1 << 22:
                fh.write(buf); buf.clear()
        fh.write(buf); buf.clear()

        fh.write(struct.pack("<Q", region_bytes))
        buf = bytearray()
        for r in region_bounds:
            buf += _struct.pack("<B3x6f", r.region, r.minX, r.minY, r.minZ,
                                r.maxX, r.maxY, r.maxZ)
        fh.write(buf)

    tmp.replace(out_path)
    return out_path.stat().st_size


def build_banc_asset(data_dir: Path,
                     out_path: Path,
                     *,
                     min_synapses: int = 1,
                     exclude_glia: bool = True,
                     keep_unmapped_neurons: bool = False,
                     generated_by: str = "flybrain/banc.py",
                     ) -> tuple[BancIngestResult, int]:
    """Stream the real BANC whole-CNS connectome into a .fbpack on disk.

    `min_synapses` is the connection threshold: 1 keeps every traced connection
    (the published "connection" threshold is 5). Keeping everything at 1 is the
    honest default (nothing is discarded); a higher value is an explicit
    experimental choice and is recorded in the report.

    `exclude_glia` drops glial cells, which are not neurons and would make the
    spiking network meaningless. They are counted, never silently deleted.

    Returns (result, asset_bytes). Peak RSS is flat in the synapse count: only
    the per-neuron arrays and the canonical-id map are held, and the synapse
    block is written while it is still being produced.
    """
    data_dir = Path(data_dir)
    out_path = Path(out_path)
    result = BancIngestResult(data_dir=str(data_dir))

    neuron_csv = data_dir / "neurons.csv.gz"
    conn_csv = data_dir / "connections_princeton.csv.gz"
    attr_pickle = data_dir / "neuron_attributes.pickle.gz"
    for p in (neuron_csv, conn_csv, attr_pickle):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} missing — run tools/fetch_banc.py first "
                f"(downloads are never performed by the ingest step)")

    table = _load_neuron_table(neuron_csv)
    result.neurons_read = len(table)
    attrs = _load_neuron_attributes(attr_pickle)

    # cell-type vocabulary (first pass: names only, cheap)
    type_ids: dict[str, int] = {}
    name_of_type: list[str] = []
    for row in table.values():
        name = (row.get("Primary Cell Type") or row.get("Class") or
                row.get("Super Class") or "unclassified").strip()
        if name not in type_ids:
            type_ids[name] = len(type_ids)
            name_of_type.append(name)

    # ---- neurons ---------------------------------------------------------
    canonical: dict[int, int] = {}
    neurons: list[NeuronRecord] = []
    # canonicalID -> predicted transmitter tag (used for the inferred sign)
    pre_nt_types: list[str] = []
    region_bounds_acc: dict[int, list[float]] = {}

    for rid, row in table.items():
        a = attrs.get(rid, {})
        super_class = (row.get("Super Class") or "").strip()
        if exclude_glia and super_class == "glia":
            result.neurons_dropped += 1
            result.dropped_reasons["glia"] += 1
            continue
        # The release also labels a handful of rows `trachea` and
        # `not_a_neuron`. They are not nerve cells, so they must not become
        # neurons in the simulator just because they carry a neuropil tag.
        if super_class in ("trachea", "not_a_neuron"):
            result.neurons_dropped += 1
            result.dropped_reasons[f"not-a-neuron ({super_class})"] += 1
            continue

        side_raw = (row.get("Soma side") or a.get("side") or "").strip().lower()
        side = 1 if side_raw == "left" else 2 if side_raw == "right" else 0

        is_frag = "fragment" in (row.get("Primary Cell Type") or "").lower()
        region, unmapped = _dominant_region(a, side_raw, result.region_unmapped_synapses)
        if region == int(RegionID.UNKNOWN) and not keep_unmapped_neurons:
            result.neurons_dropped += 1
            # Distinguish the two very different reasons a neuron has no
            # region: a mere fragment (the release says so in its cell type)
            # versus a whole neuron whose atlas tag the mapping does not cover.
            # The second kind is a mapping gap and must be visible as such —
            # with the fixed compound-tag handling it is now 0 on banc-888,
            # and any non-zero value means a release tag went unmapped.
            if is_frag:
                result.dropped_reasons["fragment-no-region"] += 1
            elif not (a.get("input_neuropils") or a.get("output_neuropils")):
                result.dropped_reasons["no-atlas-tag"] += 1
            else:
                result.dropped_reasons["atlas-tag-unmapped"] += 1
                # Name the ACTUAL tag, not a flag: this string is the only
                # pointer a maintainer gets for extending the mapping.
                missing = next(
                    (t for key in ("input_neuropils", "output_neuropils")
                     for t in (a.get(key) or [])
                     if map_neuropil(t, side_raw) is None), "?")
                result.problems.append(
                    f"neuron {rid}: atlas tag {missing!r} has no simulator "
                    f"region — the mapping needs that tag")
            continue

        pos = _parse_position(a.get("position"))
        if pos is None:
            pos = (0.0, 0.0, 0.0)
            result.missing_position += 1

        nt_tag = (row.get("Predicted NT type") or "").strip()
        result.transmitter_counts[nt_tag or "(none)"] += 1

        ctype = (row.get("Primary Cell Type") or row.get("Class") or
                 row.get("Super Class") or "unclassified").strip()
        # `type` is the cell-type vocabulary index (see `type_ids` above).
        # Measured on the committed asset: 11,528 distinct values spanning
        # 0..11,565, i.e. a dense vocabulary index, NOT an ordinal of a name and
        # not a truncation of one. `readMotorDrive` splits its six leg slots by
        # `Int(type) % 3` and the ingest lands the 15 leg motor types on 15
        # distinct indices covering all three residues on both sides
        # (`tools/probe_shipped_asset_channels.py`), so the slots are
        # addressable.
        #
        # `setdefault` rather than the old `type_ids.get(ctype, 0)`: the
        # vocabulary pass runs first so `get` found every name, but the `.get`
        # form silently falls back to type 0 ("unclassified") for any name that
        # pass ever misses. Recording the name instead makes a mismatch
        # impossible rather than invisible.
        type_index = type_ids.setdefault(ctype, len(type_ids))
        neurons.append(NeuronRecord(
            canonicalID=len(neurons),
            datasetID=int(DatasetID.BANC),
            type=type_index,
            region=region,
            side=side,
            transmitter=transmitter_enum(nt_tag),
            provenance=int(Provenance.RECONSTRUCTED),
            # Cell class from the release's own `Super Class` label. This is
            # MEASURED, unlike `region` (a reduction of an atlas tag), and it is
            # what lets the motor readout at runtime tell a leg motor neuron
            # from the sensory afferent sitting in the same neuromere: the
            # ingest puts 4,770 sensory cells and 187 motor cells into
            # legNeuromere, so a region-only readout would sum its own input.
            flags=pack.neuron_flags(super_class),
            morphologyIndex=-1,
            incomingStart=0, incomingCount=0,
            outgoingStart=0, outgoingCount=0,
            x=pos[0], y=pos[1], z=pos[2],
        ))
        canonical[rid] = len(neurons) - 1
        pre_nt_types.append(nt_tag)
        result.neurons_kept += 1
        result.regions_filled[RegionID(region).name] += 1
        if pos != (0.0, 0.0, 0.0):
            b = region_bounds_acc.get(region)
            if b is None:
                region_bounds_acc[region] = [pos[0], pos[1], pos[2],
                                             pos[0], pos[1], pos[2]]
            else:
                b[0] = min(b[0], pos[0]); b[1] = min(b[1], pos[1]); b[2] = min(b[2], pos[2])
                b[3] = max(b[3], pos[0]); b[4] = max(b[4], pos[1]); b[5] = max(b[5], pos[2])
        _ = unmapped

    # The CSV table is no longer needed; free it before the synapse block so the
    # transient peak stays inside the device memory cap.
    table = None
    attrs = None

    # ---- connections -----------------------------------------------------
    # Stream the connection file, keeping only edges whose BOTH endpoints
    # survived the neuron pass. The release is already ordered by presynaptic
    # root id (verified on banc-888: 3,990,039/3,990,039 rows non-decreasing),
    # and the canonical id preserves the CSV order because the neuron pass
    # inserts into `canonical` in that same order — so a single streaming pass
    # yields edges sorted by presynaptic canonical id, which is exactly what
    # the outgoing CSR needs. No global sort is performed, which keeps this
    # O(E) in memory instead of O(E log E).
    packed = array("i")   # flat triples (pre, post, syn)
    with gzip.open(conn_csv, "rt", newline="") as fh:
        for line in fh:
            # Manual split: this line is read ~4M times and csv.DictReader's
            # per-row dict is the dominant cost of the whole ingest. Column
            # order in the release is pre,post,neuropil,syn_count,nt_type.
            a = line.find(",")
            b = line.find(",", a + 1)
            c = line.find(",", b + 1)      # end of the neuropil column
            d = line.find(",", c + 1)      # end of syn_count
            if d < 0:
                continue                   # header row or truncated line
            try:
                pre_raw = int(line[:a])
                post_raw = int(line[a + 1:b])
                syn = int(line[c + 1:d])
            except ValueError:
                result.connections_dropped += 1
                continue
            result.connections_read += 1
            if syn < min_synapses:
                result.dropped_below_threshold += 1
                continue
            pre = canonical.get(pre_raw)
            post = canonical.get(post_raw)
            if pre is None or post is None:
                result.connections_dropped += 1
                continue
            packed.append(pre); packed.append(post); packed.append(syn)

    result.connections_kept_raw = len(packed) // 3

    # Merge duplicate (pre, post) pairs by summing their synapse counts. The
    # transmitter comes from the presynaptic neuron, so callers look it up by
    # `pre` when writing the wire records rather than storing it per edge.
    # Flat triples (pre, post, syn) in one int array: 12 bytes per kept edge
    # instead of a ~72-byte tuple, which is what brought the peak under the
    # device memory cap for the 3.0M-edge BANC release.
    ordered = array("i")
    i = 0
    n_packed = len(packed)
    while i < n_packed:
        pre = packed[i]; post = packed[i + 1]; syn = packed[i + 2]
        i += 3
        while i < n_packed and packed[i] == pre and packed[i + 1] == post:
            syn += packed[i + 2]
            i += 3
        ordered.append(pre); ordered.append(post); ordered.append(syn)
    packed = None
    result.connections_kept = len(ordered) // 3

    # outgoing CSR: one entry per neuron, filled by a forward scan
    outgoing: list[OutEdgeRange] = [OutEdgeRange(start=0, count=0)
                                    for _ in range(len(neurons))]
    current_pre = -1
    start_for_pre = 0
    for i in range(0, len(ordered), 3):
        pre = ordered[i]
        if pre != current_pre:
            if current_pre >= 0:
                outgoing[current_pre] = OutEdgeRange(
                    start=start_for_pre, count=(i // 3) - start_for_pre)
            current_pre = pre
            start_for_pre = i // 3
    if current_pre >= 0:
        outgoing[current_pre] = OutEdgeRange(
            start=start_for_pre, count=(len(ordered) // 3) - start_for_pre)

    # A neuron with no outgoing edges would keep start=0 by construction, which
    # breaks the CSR's tiling invariant (ranges must tile [0, nEdges) in neuron
    # order) — that is what made test_outgoing_csr fail. `start` is defined as
    # the number of edges belonging to EARLIER neurons, i.e. the prefix sum of
    # the counts, which is also exactly what the forward scan produces for the
    # neurons that do have edges (the edges are sorted by canonical id).
    running = 0
    for i, r in enumerate(outgoing):
        running += r.count
        outgoing[i] = OutEdgeRange(start=running - r.count, count=r.count)

    # incoming CSR: the wire format carries per-neuron incomingStart/Count, but
    # NO incoming index block is written — only outgoing edges are stored (see
    # _write_banc_asset and pack.py's four blocks: neuron, synapse, range,
    # region). Declaring a prefix-sum layout that is not in the file left every
    # reader indexing an array that does not exist, and broke
    # Connectome.validateCSR(), which checks the starts tile [0, total). So
    # report 0/0: "no incoming CSR is stored in this asset", which is the truth
    # the format supports. The incoming adjacency is derivable from the outgoing
    # CSR whenever it is actually needed.
    for i, n in enumerate(neurons):
        n.outgoingStart = outgoing[i].start
        n.outgoingCount = outgoing[i].count
        n.incomingStart = 0
        n.incomingCount = 0

    region_bounds = [
        RegionBounds(r, b[0], b[1], b[2], b[3], b[4], b[5])
        for r, b in sorted(region_bounds_acc.items())
    ]

    header = ConnectomeHeader(
        magic=0x46425031,
        version=2,          # v2 widened the cell-type field from u8 to u16
        flags=0,                     # reserved in the format; no reader reads it
        neuronCount=len(neurons),
        synapseCount=len(ordered) // 3,
        morphologyCount=0,
        regionCount=len(region_bounds),
        organism={
            "species": "Drosophila melanogaster",
            "sex": "female",
            "lifeStage": "adult",
            "datasetVersion": BANC_VERSION,
            "simulatorVersion": "0.1.0",
            "parameterProfile": "female-adult-default-v1",
        },
        sourceDatasets=[f"BANC (codex {BANC_VERSION}) — brain + VNC"],
        dataProvenance=Provenance.RECONSTRUCTED,
        generationDate="",
        generatedBy=generated_by,
        description=(
            "Real BANC whole-CNS connectome (brain + ventral nerve cord, adult "
            "female). Topology, neuropil assignment and soma positions are "
            "reconstructed from EM tracing. Transmitter is a prediction. Sign, "
            "efficacy and delay are INFERRED by this pipeline and are not "
            "measured values."),
    )

    asset_bytes = _write_banc_asset(
        out_path, header=header, neurons=neurons, ordered=ordered,
        pre_nt_types=pre_nt_types, outgoing=outgoing,
        region_bounds=region_bounds, generated_by=generated_by)
    result.type_vocabulary_size = len(type_ids)
    result.asset_bytes = asset_bytes
    return result, asset_bytes


def write_ingest_report(result: BancIngestResult, path: Path,
                        extra: dict | None = None) -> Path:
    """Write a machine-readable provenance report next to the asset."""
    payload = {
        "dataset": result.dataset,
        "source": BANC_SOURCE,
        "counts": {
            "neurons_read": result.neurons_read,
            "neurons_kept": result.neurons_kept,
            "neurons_dropped": result.neurons_dropped,
            "connections_read": result.connections_read,
            "connections_kept": result.connections_kept,
            "connections_dropped": result.connections_dropped,
            "connections_below_threshold": result.dropped_below_threshold,
        },
        "regions_filled": dict(result.regions_filled.most_common()),
        "unmapped_neuropils": dict(result.region_unmapped_synapses.most_common(50)),
        "transmitters": dict(result.transmitter_counts.most_common()),
        "problems": result.problems,
        "provenance": {
            "topology": "RECONSTRUCTED (EM tracing, BANC)",
            # The release's atlas tag is measured, but reducing a tag (often a
            # COMPOUND one, 5,152 neurons carry e.g. "ME.LO") to ONE simulator
            # region is our own choice, and it cannot be read off synapse counts
            # either: measured on banc-888, the release's tag lists are ordered
            # by descending synapse count in only 17.9% of multi-tag neurons and
            # the first-listed tag equals the measured argmax in 60.2%, so no
            # component is demonstrably "the dominant" one. INFERRED, not
            # reconstructed.
            "neuropil_assignment": (
                "INFERRED (atlas tag -> single simulator region; compound tags "
                "resolved to the leading mapped component) — the release tag is "
                "RECONSTRUCTED, the region reduction is not"),
            "soma_position": "RECONSTRUCTED (voxel coordinates, 4/4/40 nm)",
            "transmitter": "PREDICTED (classifier in the release)",
            # Per-transmitter, because the transmitters do NOT share a valence
            # story: ACH excitatory and GABA inhibitory have strong support,
            # HIST is inhibitory (chloride channel), and GLUT / the amines carry
            # no decidable valence from the label alone.
            "sign": {
                "status": "INFERRED (transmitter label -> valence, per transmitter)",
                "ACH": "+1 (cation-permeant nicotinic AChR)",
                "GABA": "-1 (Rdl GABA-gated Cl- channel)",
                "HIST": "-1 (histamine-gated Cl- channels, lamina L1-L3)",
                "GLUT": "0 (iGluR excitatory AND GluClalpha inhibitory both exist; "
                        "valence is cell-type-specific)",
                "DA/SER/OCT/TYR": "0 (GPCR-only families; no modelled fast current)",
                "unpredicted": "0 (no evidence to excite OR inhibit)",
            },
            "efficacy": "INFERRED (synapse-count saturation curve)",
            "delay": "APPROXIMATED (constant, not in the release)",
        },
    }
    if extra:
        payload["extra"] = extra
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


__all__ = ["build_banc_asset", "write_ingest_report", "BancIngestResult",
           "sign_for", "transmitter_enum", "BANC_VERSION", "BANC_SOURCE"]