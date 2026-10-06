# ingestion framework tests
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from flybrain.ingest import (
    map_region,
    map_transmitter,
    provenance_from_str,
    ingest_canonical,
)
from flybrain.pid import RegionID, TransmitterType


def test_region_mapping():
    assert map_region("VNC") == int(RegionID.VENTRAL_NERVE_CORD)
    assert map_region("mushroom_body") == int(RegionID.MUSHROOM_BODY)
    assert map_region("lobula-plate") == int(RegionID.LOBULA_PLATE)
    assert map_region("sez") == int(RegionID.SUBESOPHAGEAL_ZONE)
    # retina resolves by side
    assert map_region("retina", "right") == int(RegionID.RETINA_RIGHT)
    assert map_region("retina", "left") == int(RegionID.RETINA_LEFT)
    assert map_region("bogus") is None


def test_transmitter_mapping():
    assert map_transmitter("GABA") == int(TransmitterType.GABAERGIC)
    assert map_transmitter("octopamine") == int(TransmitterType.OCTOPAMINERGIC)
    assert map_transmitter("") == int(TransmitterType.UNKNOWN)


def test_provenance_mapping():
    assert provenance_from_str("MEASURED") == 0
    assert provenance_from_str("RECONSTRUCTED") == 1
    assert provenance_from_str("inferred") == 2
    assert provenance_from_str("") == 5


def test_ingest_fixture(tmp_path):
    data = {
        "dataset": "FAFB/FlyWire-test",
        "version": "v1",
        "organism": {"species": "Drosophila melanogaster", "sex": "female",
                     "lifeStage": "adult"},
        "neurons": [
            {"id": "a", "region": "medulla", "hemisphere": "left",
             "transmitter": "cholinergic", "provenance": "RECONSTRUCTED",
             "x": 1, "y": 2, "z": 3},
            {"id": "b", "region": "VNC", "hemisphere": "center",
             "transmitter": "glutamatergic", "provenance": "INFERRED",
             "x": 0, "y": 0, "z": 0},
        ],
        "synapses": [
            {"pre": "a", "post": "b", "count": 4, "transmitter": "cholinergic",
             "confidence": 0.8, "provenance": "MEASURED"},
        ],
    }
    p = tmp_path / "ingest.json"
    p.write_text(json.dumps(data))
    result = ingest_canonical(p)
    assert result.neurons_ingested == 2
    assert result.synapses_ingested == 1
    assert not result.dropped
    assert not result.problems


def test_ingest_rejects_unknown_region(tmp_path):
    data = {
        "dataset": "test",
        "neurons": [
            {"id": "x", "region": "not-a-region", "hemisphere": "center",
             "transmitter": "cholinergic", "provenance": "INFERRED"},
        ],
        "synapses": [],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(data))
    result = ingest_canonical(p)
    assert result.dropped
    assert "unknown region" in result.dropped[0]