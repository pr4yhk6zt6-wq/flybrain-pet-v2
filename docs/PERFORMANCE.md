# FlyBrain Pet — Performance

## Target (spec #80)

- 60 FPS UI on iPhone 11 (A13) under normal conditions
- 30+ FPS during extreme connectome inspection
- No UI freezing; simulation clock decoupled from render clock (spec #46)

## Neural core efficiency

- Event-driven: p-sparse — quiescent neurons cost ~0 per step
- No per-step sweep over all neurons. Refractory windows and the recent-spike
  counters are held in two incremental active sets (`refractoryRing`,
  `recentRing`), each containing exactly the neurons with a non-zero value:
  a neuron is added when it spikes and dropped when the value returns to 0.
  A whole-BANC connectome is 153,746 neurons, so an unconditional sweep ran
  once per 0.1 ms step regardless of activity — the largest per-step cost on
  real data, and a direct contradiction of the p-sparse claim above. Both
  sets are rebuilt from the state on `restore()` (they are derived data).
- The hot step reuses two scratch buffers instead of allocating a fresh
  Array + Set per step.
- `recentSpikesPerNeuron` is a tumbling 1 s window (like `spikesPerSecond`),
  cleared when the window rolls and only over the non-zero entries.
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