#!/usr/bin/env python3
"""Generate the synthetic demo connectome asset (.fbpack).

Usage:
    python3 -m flybrain.pack [output_path]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from flybrain.pack import write_fbpack
from flybrain.pid import build_synthetic_demo

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "data/generated/demo_micro.fbpack"
    demo = build_synthetic_demo()
    p = write_fbpack(demo, out, generated_by="pack.py (demo generator)")
    print(f"wrote {p} ({p.stat().st_size} bytes)")
    print(f"  neurons : {demo['header'].neuronCount}")
    print(f"  synapses: {demo['header'].synapseCount}")
    print(f"  organism: {demo['header'].organism['species']} "
          f"({demo['header'].organism['sex']}, {demo['header'].organism['lifeStage']})")
    print(f"  provenance: {demo['header'].dataProvenance}")