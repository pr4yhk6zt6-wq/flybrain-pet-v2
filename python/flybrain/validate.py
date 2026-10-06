# flybrain/validate.py
"""Data integrity validation (spec #90). Serious errors fail the build."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summarize(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            lines += [f"  ! {e}" for e in self.errors[:20]]
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            lines += [f"  ~ {w}" for w in self.warnings[:20]]
        lines.append("stats: " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())))
        return "\n".join(lines)


def validate_dataset(data: dict) -> ValidationResult:
    res = ValidationResult()
    neurons = data["neurons"]
    synapses = data["synapses"]
    ranges = data["outgoingRanges"]
    header = data["header"]

    n = len(neurons)
    s = len(synapses)
    res.stats["neurons"] = n
    res.stats["synapses"] = s
    res.stats["edges_per_neuron"] = round(s / n, 2) if n else 0

    if n == 0:
        res.errors.append("no neurons")
    if s == 0:
        res.errors.append("no synapses")
    if header.neuronCount != n:
        res.errors.append(f"header.neuronCount {header.neuronCount} != actual {n}")
    if header.synapseCount != s:
        res.errors.append(f"header.synapseCount {header.synapseCount} != actual {s}")
    if len(ranges) != n:
        res.errors.append(f"outgoingRanges {len(ranges)} != neurons {n}")

    # duplicate canonical IDs
    ids = [nr.canonicalID for nr in neurons]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        res.errors.append(f"duplicate canonical IDs: {sorted(dupes)[:20]}")

    # range bounds
    for i, nr in enumerate(neurons):
        if nr.incomingStart < 0 or nr.incomingStart + nr.incomingCount > s:
            res.errors.append(f"neuron {i}: incoming range oob")
        if nr.outgoingStart < 0 or nr.outgoingStart + nr.outgoingCount > s:
            res.errors.append(f"neuron {i}: outgoing range oob")
        if nr.provenance not in (0, 1, 2, 3, 4, 5):
            res.errors.append(f"neuron {i}: bad provenance {nr.provenance}")
        if nr.transmitter > 9:
            res.errors.append(f"neuron {i}: bad transmitter {nr.transmitter}")
        if nr.region > 20:
            res.errors.append(f"neuron {i}: bad region {nr.region}")

    # orphan edges / invalid endpoints
    for i, syn in enumerate(synapses):
        if syn.preNeuron < 0 or syn.preNeuron >= n:
            res.errors.append(f"synapse {i}: bad preNeuron {syn.preNeuron}")
        if syn.postNeuron < 0 or syn.postNeuron >= n:
            res.errors.append(f"synapse {i}: bad postNeuron {syn.postNeuron}")

    # outgoing ranges must reconstruct the synapse adjacency
    seen = set()
    for pre, rng in enumerate(ranges):
        for k in range(rng.start, rng.start + rng.count):
            if k < 0 or k >= s:
                res.errors.append(f"range pre={pre}: synapse index {k} oob")
                continue
            if synapses[k].preNeuron != pre:
                res.errors.append(f"range pre={pre}: synapse {k} has preNeuron {synapses[k].preNeuron}")
            seen.add(k)
    if len(seen) != s:
        res.warnings.append(f"outgoing ranges cover {len(seen)}/{s} synapses (incoming-only edges?)")

    # transmitter/sign sanity: cholinergic should rarely be inhibitory
    for i, syn in enumerate(synapses[:2000]):
        if syn.transmitter == 1 and syn.sign < 0:
            res.warnings.append(f"synapse {i}: cholinergic with inhibitory sign (possible, not error)")

    if res.ok:
        res.warnings.append("no integrity errors — dataset passes validation")
    return res