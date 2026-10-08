# flybrain/pid.py
"""Provenance & ID taxonomy shared with the Swift core (Types.swift).

Storage format matches the .fbpack wire format:
  * NeuronRecord / SynapseRecord / OutEdgeRange / RegionBounds are packed
    into fixed-layout little-endian structs (see pack.py)
  * Provenance / DatasetID / RegionID / TransmitterType are stored as their
    integer index in allCases, byte-identical to the Swift side.
"""

from __future__ import annotations

import enum

# Cell-class bits live with the packer (pack.py) and pid is imported BY pack,
# so the constants are re-stated here as plain byte values to avoid a circular
# import. `python/tests/test_banc.py` pins them against pack.py's definitions,
# so the duplication cannot drift silently.
FLAG_MOTOR = 1 << 0
FLAG_SENSORY = 1 << 1

# --------------------------------------------------------------------------
# Provenance taxonomy (spec #3, #28, #60)
# --------------------------------------------------------------------------

class Provenance(enum.IntEnum):
    MEASURED = 0
    RECONSTRUCTED = 1
    INFERRED = 2
    PREDICTED = 3
    APPROXIMATED = 4
    UNKNOWN = 5


def _provenance_name(value) -> str:
    """Canonical NAME of a provenance value, for the JSON header.

    The header is decoded by Swift as `ConnectomeHeader.dataProvenance: String`
    (the Swift `Provenance` enum is `String`-backed), so the wire form must be
    the member NAME ("RECONSTRUCTED"), never the integer raw value. Accepts an
    enum member, its integer, or an already-correct name so any caller can pass
    what it has.
    """
    if isinstance(value, Provenance):
        return value.name
    if isinstance(value, str):
        return value.upper()
    return Provenance(int(value)).name


# Datasets this pipeline knows how to consume (spec #4, #118)
class DatasetID(enum.IntEnum):
    SYNTHETIC = 0
    BANC = 1
    FLYWIRE = 2      # FAFB / FlyWire
    FICTRAC = 3
    OTHER = 4


# Brain regions / neuropils (spec #10) — must match RegionID in Types.swift
class RegionID(enum.IntEnum):
    UNKNOWN = 0
    RETINA_LEFT = 1
    RETINA_RIGHT = 2
    LAMINA = 3
    MEDULLA = 4
    LOBULA = 5
    LOBULA_PLATE = 6
    OPTIC_LOBE = 7
    ANTENNAL_LOBE = 8
    MUSHROOM_BODY = 9
    LATERAL_HORN = 10
    CENTRAL_COMPLEX = 11
    SUPERIOR_BRAIN = 12
    SUBESOPHAGEAL_ZONE = 13     # SEZ
    CERVICAL_CONNECTIVE = 14
    VENTRAL_NERVE_CORD = 15     # VNC
    LEG_NEUROMERE = 16
    WING_NEUROPIL = 17
    HALTERE_NEUROPIL = 18
    ABDOMINAL_NEUROMERE = 19
    ENDOCRINE_VISCERAL = 20


# Neurotransmitters (spec #8)
class TransmitterType(enum.IntEnum):
    UNKNOWN = 0
    CHOLINERGIC = 1
    GABAERGIC = 2
    GLUTAMATERGIC = 3
    DOPAMINERGIC = 4
    SEROTONERGIC = 5
    OCTOPAMINERGIC = 6
    TYRAMINERGIC = 7
    PEPTIDERGIC = 8
    HISTAMINERGIC = 9


# --------------------------------------------------------------------------
# Compact records (mirror Swift structs)
# --------------------------------------------------------------------------

class NeuronRecord:
    __slots__ = (
        "canonicalID", "datasetID", "type", "region", "side",
        "transmitter", "provenance", "morphologyIndex",
        "incomingStart", "incomingCount", "outgoingStart", "outgoingCount",
        "x", "y", "z", "flags",
    )

    def __init__(self, canonicalID, datasetID, type, region, side,
                 transmitter, provenance, morphologyIndex,
                 incomingStart, incomingCount, outgoingStart, outgoingCount,
                 x, y, z, flags=0):
        self.canonicalID = int(canonicalID)
        self.datasetID = int(datasetID)
        self.type = int(type)
        self.region = int(region)
        self.side = int(side)
        self.transmitter = int(transmitter)
        self.provenance = int(provenance)
        self.flags = int(flags)
        self.morphologyIndex = int(morphologyIndex)
        self.incomingStart = int(incomingStart)
        self.incomingCount = int(incomingCount)
        self.outgoingStart = int(outgoingStart)
        self.outgoingCount = int(outgoingCount)
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


class SynapseRecord:
    __slots__ = (
        "preNeuron", "postNeuron", "synapseCount", "transmitter",
        "sign", "confidence", "delaySteps", "estimatedEfficacy",
    )

    def __init__(self, preNeuron, postNeuron, synapseCount, transmitter,
                 sign, confidence, delaySteps, estimatedEfficacy):
        self.preNeuron = int(preNeuron)
        self.postNeuron = int(postNeuron)
        self.synapseCount = int(synapseCount)
        self.transmitter = int(transmitter)
        self.sign = int(sign)
        self.confidence = int(confidence)
        self.delaySteps = int(delaySteps)
        self.estimatedEfficacy = float(estimatedEfficacy)


class OutEdgeRange:
    __slots__ = ("start", "count")

    def __init__(self, start, count):
        self.start = int(start)
        self.count = int(count)


class RegionBounds:
    __slots__ = ("region", "minX", "minY", "minZ", "maxX", "maxY", "maxZ")

    def __init__(self, region, minX, minY, minZ, maxX, maxY, maxZ):
        self.region = int(region)
        self.minX = float(minX); self.minY = float(minY); self.minZ = float(minZ)
        self.maxX = float(maxX); self.maxY = float(maxY); self.maxZ = float(maxZ)


class ConnectomeHeader:
    """JSON-encoded header (matches Swift ConnectomeHeader Codable)."""

    def __init__(self, *, magic, version, flags, neuronCount, synapseCount,
                 morphologyCount, regionCount, organism, sourceDatasets,
                 dataProvenance, generationDate, generatedBy, description):
        self.magic = magic
        self.version = version
        self.flags = flags
        self.neuronCount = neuronCount
        self.synapseCount = synapseCount
        self.morphologyCount = morphologyCount
        self.regionCount = regionCount
        self.organism = organism
        self.sourceDatasets = sourceDatasets
        self.dataProvenance = dataProvenance
        self.generationDate = generationDate
        self.generatedBy = generatedBy
        self.description = description

    def to_json_dict(self):
        return {
            "magic": self.magic,
            "version": self.version,
            "flags": self.flags,
            "neuronCount": self.neuronCount,
            "synapseCount": self.synapseCount,
            "morphologyCount": self.morphologyCount,
            "regionCount": self.regionCount,
            "organism": self.organism,
            "sourceDatasets": self.sourceDatasets,
            # Swift decodes this field as `String` (ConnectomeHeader.dataProvenance:
            # String, mirroring the `Provenance: String` enum). Writing the
            # IntEnum here serialised an INTEGER (1/2/...), which made
            # JSONDecoder throw and no Python-generated .fbpack could be opened
            # by the app at all. Emit the enum's NAME so both languages agree.
            "dataProvenance": _provenance_name(self.dataProvenance),
            "generationDate": self.generationDate,
            "generatedBy": self.generatedBy,
            "description": self.description,
        }


# --------------------------------------------------------------------------
# Synthetic demo dataset generator
# --------------------------------------------------------------------------
# The bundled dataset is an openly-licensed synthetic stand-in (spec #1/#2:
# default organism adult female Drosophila; topology is illustrative and
# clearly labeled SYNTHETIC-DEMO until real BANC/FAFB ingestion lands).
#
# Region layout is biologically motivated (spec #10): visual pathway
# retina → lamina → medulla → lobula/lobula-plate; central brain
# (antennal lobe → mushroom body → lateral horn, central complex);
# SEZ → VNC → leg neuromeres; wing/haltere neuropils; endocrine visceral.
# Left/right hemispheres (side 1/2). All connections are labeled INFERRED,
# because the synthetic graph is an engineering stand-in, never measured.

def build_synthetic_demo(*, neurons_per_region: int = 24, seed: int = 42):
    import random
    rng = random.Random(seed)

    # region -> (short label, x,y,z center)
    centers = {
        RegionID.RETINA_LEFT: ("retina-L", -7.0, 3.5, 0.0),
        RegionID.RETINA_RIGHT: ("retina-R", 7.0, 3.5, 0.0),
        RegionID.LAMINA: ("lamina", -6.0, 3.0, 0.5),
        RegionID.MEDULLA: ("medulla", -4.5, 2.5, 0.5),
        RegionID.LOBULA: ("lobula", -5.5, 2.0, 1.0),
        RegionID.LOBULA_PLATE: ("lobula-plate", -6.2, 2.0, 1.8),
        RegionID.OPTIC_LOBE: ("optic-lobe", 4.5, 2.5, 0.5),
        RegionID.ANTENNAL_LOBE: ("antennal-lobe", 0.5, 1.8, 1.2),
        RegionID.MUSHROOM_BODY: ("mushroom-body", 0.0, 1.5, 2.0),
        RegionID.LATERAL_HORN: ("lateral-horn", -0.8, 1.8, 1.6),
        RegionID.CENTRAL_COMPLEX: ("central-complex", 0.0, 1.0, 1.5),
        RegionID.SUPERIOR_BRAIN: ("superior-brain", 0.0, 0.8, 2.4),
        RegionID.SUBESOPHAGEAL_ZONE: ("SEZ", 0.0, -0.5, 0.8),
        RegionID.CERVICAL_CONNECTIVE: ("cervical-connective", 0.0, -1.6, 0.2),
        RegionID.VENTRAL_NERVE_CORD: ("VNC", 0.0, -3.0, 0.0),
        RegionID.LEG_NEUROMERE: ("leg-neuromere", 0.0, -3.5, 0.6),
        RegionID.WING_NEUROPIL: ("wing-neuropil", 3.2, -2.8, 1.0),
        RegionID.HALTERE_NEUROPIL: ("haltere-neuropil", -3.2, -2.8, 1.0),
        RegionID.ABDOMINAL_NEUROMERE: ("abdominal-neuromere", 0.0, -4.8, 1.2),
        RegionID.ENDOCRINE_VISCERAL: ("endocrine-visceral", 0.0, 0.2, 0.4),
    }

    # connectome edges: (pre_region, post_region, sign, transmitter, count_range)
    edges = [
        (RegionID.RETINA_LEFT, RegionID.LAMINA, +1, TransmitterType.CHOLINERGIC, (2, 4)),
        (RegionID.RETINA_RIGHT, RegionID.LAMINA, +1, TransmitterType.CHOLINERGIC, (2, 4)),
        (RegionID.LAMINA, RegionID.MEDULLA, +1, TransmitterType.CHOLINERGIC, (2, 5)),
        (RegionID.MEDULLA, RegionID.LOBULA, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.MEDULLA, RegionID.LOBULA_PLATE, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.LOBULA, RegionID.OPTIC_LOBE, +1, TransmitterType.CHOLINERGIC, (1, 2)),
        (RegionID.LOBULA_PLATE, RegionID.OPTIC_LOBE, +1, TransmitterType.CHOLINERGIC, (1, 2)),
        (RegionID.OPTIC_LOBE, RegionID.CENTRAL_COMPLEX, +1, TransmitterType.CHOLINERGIC, (1, 2)),
        (RegionID.ANTENNAL_LOBE, RegionID.MUSHROOM_BODY, +1, TransmitterType.CHOLINERGIC, (2, 4)),
        (RegionID.ANTENNAL_LOBE, RegionID.LATERAL_HORN, +1, TransmitterType.CHOLINERGIC, (2, 4)),
        (RegionID.MUSHROOM_BODY, RegionID.CENTRAL_COMPLEX, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.MUSHROOM_BODY, RegionID.SUPERIOR_BRAIN, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.MUSHROOM_BODY, RegionID.SUPERIOR_BRAIN, -1, TransmitterType.GABAERGIC, (1, 2)),  # modulatory/inhibitory fan-out
        (RegionID.CENTRAL_COMPLEX, RegionID.SUBESOPHAGEAL_ZONE, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.SUPERIOR_BRAIN, RegionID.SUBESOPHAGEAL_ZONE, +1, TransmitterType.CHOLINERGIC, (1, 3)),
        (RegionID.SUBESOPHAGEAL_ZONE, RegionID.VENTRAL_NERVE_CORD, +1, TransmitterType.CHOLINERGIC, (2, 4)),
        (RegionID.VENTRAL_NERVE_CORD, RegionID.LEG_NEUROMERE, +1, TransmitterType.GLUTAMATERGIC, (2, 5)),
        (RegionID.VENTRAL_NERVE_CORD, RegionID.WING_NEUROPIL, +1, TransmitterType.GLUTAMATERGIC, (2, 4)),
        (RegionID.VENTRAL_NERVE_CORD, RegionID.HALTERE_NEUROPIL, +1, TransmitterType.GLUTAMATERGIC, (2, 4)),
        (RegionID.WING_NEUROPIL, RegionID.ABDOMINAL_NEUROMERE, +1, TransmitterType.CHOLINERGIC, (1, 2)),
        (RegionID.HALTERE_NEUROPIL, RegionID.VENTRAL_NERVE_CORD, -1, TransmitterType.GABAERGIC, (1, 2)),
        (RegionID.ABDOMINAL_NEUROMERE, RegionID.ENDOCRINE_VISCERAL, +1, TransmitterType.PEPTIDERGIC, (1, 2)),
    ]

    # Cell class, following the release's own `Super Class` semantics so the
    # synthetic asset exercises the same code path as the real one: afferents
    # are sensory, the neuromeres and VNC that drive muscles are motor, the
    # rest carries no class. Without this every demo neuron reads flags == 0
    # and the test that pins the class byte (FBPackCrossLanguageTests) would
    # compare two zeroes and pass without testing anything.
    _SENSORY = {
        RegionID.RETINA_LEFT, RegionID.RETINA_RIGHT, RegionID.LAMINA,
        RegionID.MEDULLA, RegionID.LOBULA, RegionID.LOBULA_PLATE,
        RegionID.ANTENNAL_LOBE, RegionID.HALTERE_NEUROPIL,
    }
    _MOTOR = {
        RegionID.VENTRAL_NERVE_CORD, RegionID.LEG_NEUROMERE,
        RegionID.WING_NEUROPIL, RegionID.ABDOMINAL_NEUROMERE,
    }
    # Neuropils that mix motor neurons with the afferents that report into them,
    # which is what the real release does (measured on banc-888:
    # legNeuromere = 187 motor + 4,770 sensory of 9,954). The first two indices
    # of such a region are motor and the next two are sensory, so a channel and
    # the readout have genuinely different cells to land on — exactly like the
    # Swift `regionalConnectome(cellClasses: true)` fixture. Giving the whole
    # region one class instead made `wingNeuropil` MOTOR-ONLY, which left the
    # wing-strain channel with no afferent on the asset the app actually runs.
    _MIXED = {
        RegionID.LEG_NEUROMERE, RegionID.WING_NEUROPIL, RegionID.VENTRAL_NERVE_CORD,
        RegionID.SUBESOPHAGEAL_ZONE,
    }

    def _class_flags(region, i) -> int:
        if region in _MIXED:
            return FLAG_MOTOR if i < 2 else FLAG_SENSORY if i < 4 else 0
        if region in _SENSORY:
            return FLAG_SENSORY
        if region in _MOTOR:
            return FLAG_MOTOR
        return 0

    neurons: list[NeuronRecord] = []
    synapses: list[SynapseRecord] = []
    region_ranges: dict[int, tuple[int, int]] = {}

    neuron_id = 0
    for region, (label, cx, cy, cz) in centers.items():
        start = neuron_id
        for i in range(neurons_per_region):
            neurons.append(NeuronRecord(
                canonicalID=neuron_id,
                datasetID=DatasetID.SYNTHETIC,
                type=1,  # generic interneuron; motor types refined later
                region=int(region),
                # Sides: the real release has no centre-line cell at all — it
                # says left or right for every one of its 153,746 neurons
                # (measured in `tools/measure_motor_pool_coverage.py`). The demo
                # used to leave every non-retina cell at side 0, so a channel
                # that asked for a side silently resolved to the region's first
                # cell. Alternating the cells left/right makes the demo exercise
                # the same selection the release does, and puts motor AND
                # sensory cells on both sides of every mixed neuropil
                # (legNeuromere, wingNeuropil).
                side=(1 if region == RegionID.RETINA_LEFT
                      else 2 if region == RegionID.RETINA_RIGHT
                      else 1 if i % 2 == 0 else 2),
                transmitter=int(TransmitterType.CHOLINERGIC) if region not in (
                    RegionID.MUSHROOM_BODY, RegionID.CENTRAL_COMPLEX) else int(TransmitterType.GABAERGIC),
                provenance=int(Provenance.INFERRED),
                flags=_class_flags(region, i),
                morphologyIndex=-1,
                incomingStart=0, incomingCount=0,
                outgoingStart=0, outgoingCount=0,
                x=cx + rng.uniform(-0.8, 0.8),
                y=cy + rng.uniform(-0.5, 0.5),
                z=cz + rng.uniform(-0.5, 0.5),
            ))
            neuron_id += 1
        region_ranges[int(region)] = (start, neuron_id)

    # Build synapses. outgoingStart/count filled after we know edge counts.
    # We first create the actual connections, tracking per-pre counts.
    pre_counts = [0] * neuron_id
    conns: list[list] = [[] for _ in range(neuron_id)]

    for (pre_r, post_r, sign, trans, crange) in edges:
        s, e = region_ranges[int(pre_r)]
        t_s, t_e = region_ranges[int(post_r)]
        for pre in range(s, e):
            n = rng.randint(*crange)
            posts = [rng.randrange(t_s, t_e) for _ in range(n)]
            for post in posts:
                conns[pre].append((
                    post,
                    TransmitterType(int(trans)),
                    sign,
                    rng.randint(1, 3),   # synapseCount
                    rng.uniform(0.1, 0.6),  # estimatedEfficacy (INFERRED)
                    rng.randint(0, 100),    # confidence (INFERRED → modest)
                ))

    # Flatten into synapse list; compute outgoing ranges
    synapse_list: list[SynapseRecord] = []
    outgoing_ranges: list[OutEdgeRange] = []
    for pre in range(neuron_id):
        start = len(synapse_list)
        for (post, trans, sign, scount, eff, conf) in conns[pre]:
            synapse_list.append(SynapseRecord(
                preNeuron=pre, postNeuron=post, synapseCount=scount,
                transmitter=int(trans), sign=sign, confidence=conf,
                delaySteps=1 if rng.random() < 0.7 else 2,
                estimatedEfficacy=eff,
            ))
        outgoing_ranges.append(OutEdgeRange(start=start, count=len(synapse_list) - start))

    # Fill incoming ranges (CSR over same synapse list ordered by postNeuron)
    # Build incoming index: reorder? The Swift reader keeps one synapse array;
    # incoming ranges reference it directly, so we must build a separate CSR
    # ordering. For simplicity: incoming ranges = full scan (each neuron's
    # incoming = all synapses whose post == neuron). We set incomingStart=0
    # per neuron and rely on the engine's spikeAccumulator, which iterates
    # outgoing only. incoming ranges are metadata for inspection.
    for i in range(neuron_id):
        neurons[i].incomingStart = 0
        neurons[i].incomingCount = 0

    header = ConnectomeHeader(
        magic=0x46425031,
        version=2,          # v2 widened the cell-type field from u8 to u16
        flags=0,
        neuronCount=neuron_id,
        synapseCount=len(synapse_list),
        morphologyCount=0,
        regionCount=len(centers),
        organism={
            "species": "Drosophila melanogaster",
            "sex": "female",
            "lifeStage": "adult",
            "datasetVersion": "synthetic-demo-v1",
            "simulatorVersion": "0.1.0",
            "parameterProfile": "female-adult-default-v1",
        },
        sourceDatasets=["SYNTHETIC-DEMO"],
        dataProvenance=Provenance.INFERRED,
        generationDate="2026-10-07",
        generatedBy="flybrain/pack.py (synthetic demo)",
        description=("Synthetic stand-in connectome for pipeline validation. "
                     "NOT biological data. Adult female Drosophila default organism."),
    )

    return {
        "header": header,
        "neurons": neurons,
        "synapses": synapse_list,
        "outgoingRanges": outgoing_ranges,
        "regionBounds": [],
    }