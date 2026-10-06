#!/usr/bin/env python3
"""FlyWire codex community graph pilot fetcher (spec #3, #88).

Downloads a small community FlyWire neighborhood graph for a seed root id,
writes it as a canonical ingest JSON (see flybrain/ingest.py).

NOTE: 
- Requires network access and a FlyWire/Codex API query (research-gated; see
  docs/SCIENCE_SOURCES.md). This script documents the integration path.
- By default it runs OFFLINE and only validates the ingest schema against a
  tiny sample fixture, so CI/pipeline tests never depend on network.

Usage:
    python3 tools/fetch_flywire.py                 # offline schema check
    python3 tools/fetch_flywire.py --seed 12345    # try live fetch (optional)
"""
import argparse
import json
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "python"
sys.path.insert(0, str(PACKAGE))

from flybrain.ingest import ingest_canonical  # noqa: E402


def offline_fixture(out: Path) -> None:
    """A minimal canonical ingest sample for schema validation."""
    fixture = {
        "dataset": "FAFB/FlyWire-community-fixture",
        "version": "fixture-v1",
        "organism": {"species": "Drosophila melanogaster", "sex": "female",
                     "lifeStage": "adult"},
        "neurons": [
            {"id": "720575940621039164", "canonical": 0, "region": "medulla",
             "hemisphere": "right", "cell_type": "Mi1",
             "transmitter": "cholinergic", "confidence": 0.9,
             "provenance": "RECONSTRUCTED", "x": 123.4, "y": -55.2, "z": 88.1},
            {"id": "720575940621039165", "canonical": 1, "region": "lobula",
             "hemisphere": "right", "cell_type": "T4",
             "transmitter": "glutamatergic", "confidence": 0.8,
             "provenance": "RECONSTRUCTED", "x": 100.2, "y": -60.0, "z": 90.0},
        ],
        "synapses": [
            {"pre": "720575940621039164", "post": "720575940621039165",
             "count": 5, "transmitter": "cholinergic",
             "confidence": 0.7, "provenance": "MEASURED"},
        ],
    }
    out.write_text(json.dumps(fixture, indent=2))
    print(f"wrote fixture -> {out}")


def live_fetch(seed: str, out: Path) -> None:
    """Attempt a real Codex query (network). Documented but optional."""
    try:
        import urllib.request
    except ImportError:
        print("urllib unavailable; cannot fetch", file=sys.stderr)
        sys.exit(1)
    # Codex/FlyWire graph query endpoint — placeholder; real access requires
    # a research account + API key. See docs/SCIENCE_SOURCES.md.
    url = f"https://codex.flywire.ai/api/graph/neighborhood/{seed}"
    print(f"warning: live fetch not pre-authorized; URL: {url}", file=sys.stderr)
    print("Access to the full FlyWire/BANC graph is research-gated.", file=sys.stderr)
    print("The offline fixture validates the schema; live ingestion requires", file=sys.stderr)
    print("accepting dataset terms and providing an API key (out of scope here).", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", help="FlyWire root id (live fetch)")
    ap.add_argument("--out", default="data/raw/flywire_fixture.json")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.seed:
        live_fetch(args.seed, out)
        return 0

    offline_fixture(out)
    # validate it parses through the ingest pipeline
    result = ingest_canonical(out)
    print(result.summarize())
    if result.dropped or result.problems:
        print("problems:", result.problems)
        print("dropped:", result.dropped[:5])
        return 1
    print("OK: fixture passes ingestion schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())