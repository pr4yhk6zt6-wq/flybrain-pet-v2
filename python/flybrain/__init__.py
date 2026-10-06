# flybrain
"""FlyBrain Pet — desktop connectome data pipeline.

Pipeline (spec #26, #51, #90):
    RAW DATA → VALIDATE → NORMALIZE → ID MAPPING → GRAPH COMPRESSION
             → GPU/CPU READY ASSETS → iOS PACKAGE (.fbpack)

Scientific rule (spec #7, #60): never present inferred data as measured.
Every attribute carries a provenance tag and confidence. Serious data
integrity errors fail the build.
"""

from .pid import Provenance, DatasetID, RegionID, TransmitterType, \
    NeuronRecord, SynapseRecord, OutEdgeRange, RegionBounds, ConnectomeHeader, \
    build_synthetic_demo

__all__ = [
    "Provenance", "DatasetID", "RegionID", "TransmitterType",
    "NeuronRecord", "SynapseRecord", "OutEdgeRange", "RegionBounds",
    "ConnectomeHeader", "build_synthetic_demo",
]