#!/usr/bin/env python3
"""Ingest the real BANC whole-CNS connectome into a .fbpack asset.

Usage:
    python3 tools/ingest_banc.py [--data data/raw/flywire/banc]
                                 [--out data/generated/banc_cns.fbpack]
                                 [--min-synapses 1]
                                 [--keep-unmapped]
                                 [--limit N]

Downloads are NOT performed here: `tools/fetch_banc.py` does that, so this
script is deterministic and offline-testable once the raw files exist.

Outputs:
    data/generated/banc_cns.fbpack         the asset the iOS app loads
    data/generated/banc_cns.report.json    provenance / counts / unmapped tags
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from flybrain.banc import (BANC_VERSION, build_banc_asset,  # noqa: E402
                           write_ingest_report)
from flybrain.pack import write_fbpack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data/raw/flywire/banc"))
    ap.add_argument("--out", default=str(ROOT / "data/generated/banc_cns.fbpack"))
    ap.add_argument("--report", default=None)
    ap.add_argument("--min-synapses", type=int, default=1,
                    help="connection threshold (1 = keep every traced edge)")
    ap.add_argument("--keep-unmapped", action="store_true",
                    help="keep neurons whose atlas tag has no simulator region")
    ap.add_argument("--no-exclude-glia", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    result, asset_bytes = build_banc_asset(
        Path(args.data),
        Path(args.out),
        min_synapses=args.min_synapses,
        exclude_glia=not args.no_exclude_glia,
        keep_unmapped_neurons=args.keep_unmapped,
        generated_by="tools/ingest_banc.py",
    )
    elapsed = time.time() - t0

    out = Path(args.out)
    report_path = Path(args.report) if args.report else out.with_suffix(".report.json")
    write_ingest_report(result, report_path, extra={
        "asset": str(out.relative_to(ROOT)) if out.is_relative_to(ROOT) else str(out),
        "asset_bytes": asset_bytes,
        "min_synapses": args.min_synapses,
        "elapsed_seconds": round(elapsed, 2),
        "type_vocabulary_size": result.type_vocabulary_size,
        "dropped_reasons": dict(result.dropped_reasons),
        "neurons_without_position": result.missing_position,
        "connections_before_pair_merge": result.connections_kept_raw,
    })

    print(result.summarize())
    print(f"  asset : {out} ({asset_bytes:,} bytes)")
    print(f"  report: {report_path}")
    print(f"  time  : {elapsed:.1f}s")
    print("  regions filled:")
    for name, n in result.regions_filled.most_common(24):
        print(f"    {n:>8,}  {name}")
    if result.region_unmapped_synapses:
        print("  atlas tags with NO simulator region (reported, not guessed):")
        for tag, n in result.region_unmapped_synapses.most_common(12):
            print(f"    {n:>8,}  {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())