# flybrain/ingest.py
"""Real connectome ingestion framework (spec #3, #4, #51, #88–#91).

Supports the dataset hierarchy:
    BANC (primary whole-organism, adult female) + FAFB/FlyWire (supplement)

Pipeline per dataset:
    raw graph → validate → normalize → ID mapping → compress → .fbpack

Access notes: BANC and FlyWire bulk graphs are research-gated (require
acceptance of terms). This module defines the ingestion CONTRACT so that the
moment access is granted, the data can be processed without rework. It also
records provenance for every ingested element (MEASURED/RECONSTRUCTED/
INFERRED/PREDICTED/APPROXIMATED/UNKNOWN — spec #3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .pid import Provenance, RegionID, TransmitterType
from .validate import validate_dataset

# JSON schema for a canonical "ingest record" — the interchange format the
# pipeline consumes regardless of source CSV/JSON/parquet.
#
# A canonical ingest file is a JSON object:
# {
#   "dataset": "BANC-v1",
#   "version": "2025.1",
#   "organism": {"species": "Drosophila melanogaster", "sex": "female",
#                "lifeStage": "adult"},
#   "neurons": [ {"id": "...", "canonical": 0, "region": "VNC",
#                 "hemisphere": "center", "transmitter": "glutamatergic",
#                 "confidence": 0.7, "provenance": "RECONSTRUCTED",
#                 "x": 12.3, "y": -4.2, "z": 1.1, ...}, ...],
#   "synapses": [ {"pre": "...", "post": "...", "count": 3,
#                  "transmitter": "cholinergic", "confidence": 0.6,
#                  "provenance": "MEASURED"}, ...]
# }


@dataclass
class IngestResult:
    dataset: str
    neurons_ingested: int = 0
    synapses_ingested: int = 0
    mapped_neurons: int = 0
    dropped: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summarize(self) -> str:
        return (f"Ingest {self.dataset}: {self.neurons_ingested} neurons, "
                f"{self.synapses_ingested} synapses, {self.mapped_neurons} mapped, "
                f"{len(self.dropped)} dropped, {len(self.problems)} problems")


# Region name → RegionID (supports BANC/FlyWire vocabulary, spec #10)
REGION_MAP = {
    "retina": RegionID.RETINA_LEFT,          # ambiguous → resolved by side
    "retina_left": RegionID.RETINA_LEFT,
    "retina_right": RegionID.RETINA_RIGHT,
    "lamina": RegionID.LAMINA,
    "medulla": RegionID.MEDULLA,
    "lobula": RegionID.LOBULA,
    "lobula_plate": RegionID.LOBULA_PLATE,
    "lobula-plate": RegionID.LOBULA_PLATE,
    "optic_lobe": RegionID.OPTIC_LOBE,
    "optic-lobe": RegionID.OPTIC_LOBE,
    "antennal_lobe": RegionID.ANTENNAL_LOBE,
    "antennal-lobe": RegionID.ANTENNAL_LOBE,
    "mushroom_body": RegionID.MUSHROOM_BODY,
    "mushroom-body": RegionID.MUSHROOM_BODY,
    "lateral_horn": RegionID.LATERAL_HORN,
    "lateral-horn": RegionID.LATERAL_HORN,
    "central_complex": RegionID.CENTRAL_COMPLEX,
    "central-complex": RegionID.CENTRAL_COMPLEX,
    "superior_brain": RegionID.SUPERIOR_BRAIN,
    "superior-brain": RegionID.SUPERIOR_BRAIN,
    "sez": RegionID.SUBESOPHAGEAL_ZONE,
    "subesophageal_zone": RegionID.SUBESOPHAGEAL_ZONE,
    "subesophageal-zone": RegionID.SUBESOPHAGEAL_ZONE,
    "cervical_connective": RegionID.CERVICAL_CONNECTIVE,
    "cervical-connective": RegionID.CERVICAL_CONNECTIVE,
    "vnc": RegionID.VENTRAL_NERVE_CORD,
    "ventral_nerve_cord": RegionID.VENTRAL_NERVE_CORD,
    "ventral-nerve-cord": RegionID.VENTRAL_NERVE_CORD,
    "leg_neuromere": RegionID.LEG_NEUROMERE,
    "leg-neuromere": RegionID.LEG_NEUROMERE,
    "wing_neuropil": RegionID.WING_NEUROPIL,
    "wing-neuropil": RegionID.WING_NEUROPIL,
    "haltere_neuropil": RegionID.HALTERE_NEUROPIL,
    "haltere-neuropil": RegionID.HALTERE_NEUROPIL,
    "abdominal_neuromere": RegionID.ABDOMINAL_NEUROMERE,
    "abdominal-neuromere": RegionID.ABDOMINAL_NEUROMERE,
    "endocrine": RegionID.ENDOCRINE_VISCERAL,
}


def map_region(name: str, side: str = "center") -> int | None:
    key = name.strip().lower().replace(" ", "_")
    region = REGION_MAP.get(key)
    if region is None:
        return None
    if region == RegionID.RETINA_LEFT and side == "right":
        return int(RegionID.RETINA_RIGHT)
    if region == RegionID.RETINA_RIGHT and side == "left":
        return int(RegionID.RETINA_LEFT)
    return int(region)


# Transmitter vocabulary from FlyWire/BANC annotations (spec #8)
TRANSMITTER_MAP = {
    "cholinergic": TransmitterType.CHOLINERGIC,
    "acetylcholine": TransmitterType.CHOLINERGIC,
    "ach": TransmitterType.CHOLINERGIC,
    "gaba": TransmitterType.GABAERGIC,
    "gabaergic": TransmitterType.GABAERGIC,
    "glutamatergic": TransmitterType.GLUTAMATERGIC,
    "glutamate": TransmitterType.GLUTAMATERGIC,
    "dopaminergic": TransmitterType.DOPAMINERGIC,
    "dopamine": TransmitterType.DOPAMINERGIC,
    "serotonergic": TransmitterType.SEROTONERGIC,
    "serotonin": TransmitterType.SEROTONERGIC,
    "5-ht": TransmitterType.SEROTONERGIC,
    "octopaminergic": TransmitterType.OCTOPAMINERGIC,
    "octopamine": TransmitterType.OCTOPAMINERGIC,
    "tyraminergic": TransmitterType.TYRAMINERGIC,
    "peptidergic": TransmitterType.PEPTIDERGIC,
    "histaminergic": TransmitterType.HISTAMINERGIC,
    "unknown": TransmitterType.UNKNOWN,
    "": TransmitterType.UNKNOWN,
}


def map_transmitter(name: str | None) -> int:
    key = (name or "").strip().lower()
    return int(TRANSMITTER_MAP.get(key, TransmitterType.UNKNOWN))


def provenance_from_str(s: str | None) -> int:
    key = (s or "").strip().upper()
    for p in Provenance:
        if p.name == key:
            return int(p)
    # map common aliases
    alias = {
        "MEASURED": int(Provenance.MEASURED),
        "SEGMENTED": int(Provenance.RECONSTRUCTED),
        "RECONSTRUCTED": int(Provenance.RECONSTRUCTED),
        "ANNOTATED": int(Provenance.RECONSTRUCTED),
        "INFERRED": int(Provenance.INFERRED),
        "PREDICTED": int(Provenance.PREDICTED),
        "APPROXIMATED": int(Provenance.APPROXIMATED),
        "UNKNOWN": int(Provenance.UNKNOWN),
        "": int(Provenance.UNKNOWN),
    }
    return alias.get(key, int(Provenance.UNKNOWN))


def ingest_canonical(path: Path) -> IngestResult:
    """Consume a canonical ingest JSON and produce an internal dataset dict."""
    data = json.loads(path.read_text())
    result = IngestResult(dataset=data.get("dataset", path.stem))
    neurons = []
    synapses = []
    canonical_by_id: dict[str, int] = {}

    for i, n in enumerate(data.get("neurons", [])):
        region = map_region(n.get("region", ""), n.get("hemisphere", "center"))
        if region is None:
            result.dropped.append(f"neuron {n.get('id')}: unknown region {n.get('region')}")
            continue
        transmitter = map_transmitter(n.get("transmitter"))
        prov = provenance_from_str(n.get("provenance"))
        neurons.append({
            "canonicalID": i,
            "datasetID": 2 if "flywire" in data.get("dataset", "").lower() else 1,
            "type": n.get("cell_type", 0),
            "region": region,
            "side": 1 if n.get("hemisphere") == "left" else 2 if n.get("hemisphere") == "right" else 0,
            "transmitter": transmitter,
            "provenance": prov,
            "morphologyIndex": n.get("morphology", -1),
            "x": n.get("x", 0), "y": n.get("y", 0), "z": n.get("z", 0),
        })
        canonical_by_id[str(n.get("id"))] = i
        result.neurons_ingested += 1

    for s in data.get("synapses", []):
        pre = canonical_by_id.get(str(s.get("pre")))
        post = canonical_by_id.get(str(s.get("post")))
        if pre is None or post is None:
            result.dropped.append(f"synapse {s.get('pre')}→{s.get('post')}: unknown endpoint")
            continue
        synapses.append({
            "preNeuron": pre, "postNeuron": post,
            "synapseCount": s.get("count", 1),
            "transmitter": map_transmitter(s.get("transmitter")),
            "sign": 1 if s.get("sign", "excitatory") == "inhibitory" else
                    (-1 if s.get("sign") == "inhibitory" else 1 if s.get("sign") == "excitatory" else 0),
            "confidence": int(s.get("confidence", 50)),
            "delaySteps": s.get("delay", 1),
            "estimatedEfficacy": s.get("efficacy", 0.3),
        })
        result.synapses_ingested += 1

    return result


__all__ = ["REGION_MAP", "TRANSMITTER_MAP", "map_region", "map_transmitter",
           "provenance_from_str", "ingest_canonical", "IngestResult"]