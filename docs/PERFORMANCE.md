# FlyBrain Pet — Performance

## Target (spec #80)

- 60 FPS UI on iPhone 11 (A13) under normal conditions
- 30+ FPS during extreme connectome inspection
- No UI freezing; simulation clock decoupled from render clock (spec #46)

## Neural core efficiency

- Event-driven: p-sparse — quiescent neurons cost ~0 per step
- Packed records (44 B neuron, 20 B synapse), contiguous arrays
- CSR outgoing adjacency for spike dispatch
- No per-object heap allocation in the hot loop
- Seeded xorshift64* RNG (cheap, deterministic)

## Memory budget (spec #81)

Separate pools for core simulation / visualization / morphology / raw data /
world assets. Load heavy assets on demand; adaptive LOD unloads unused
morphology & compresses buffers under pressure without terminating the core
simulation.

## Thermal & battery (spec #82, #83)

Monitor iOS thermal state; on heat: reduce render resolution, connectome visual
detail, background morphology fidelity — the **neural core is preserved**.

Modes: NORMAL / PERFORMANCE / BATTERY SAVER / RESEARCH (spec #83):

- NORMAL: balanced
- PERFORMANCE: high visual fidelity
- BATTERY: lower render FPS + reduced background simulation fidelity
- RESEARCH: maximum selected-circuit fidelity

## Telemetry (spec #38)

HUD shows real values: FPS, sim time, spikes/s, active neurons, event rate, CPU,
GPU, RAM, lag. Never fabricated; `PerfMonitor` (future Phase 12) measures on-device.

## GPU (spec #16/#47)

Metal instanced/point rendering for tens of thousands of neurons; GPU-side color
mapping/filtering; no per-node UI objects. Spike pulses only from real events
(spec #98).