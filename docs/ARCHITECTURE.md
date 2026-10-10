# FlyBrain Pet — Architecture

## Overview

FlyBrain Pet is an interactive embodied digital fly whose behavior emerges from a
connectome-based spiking nervous system. The app has two modes (spec #32) that
observe **the same live simulation**:

- **Life Mode** — 3D world, articulated fly, environment-driven interaction.
- **Connectome Mode** — scientific visualization of the nervous system in real time.

There is **no** behavior tree, scripted personality, LLM, or RL policy controlling
the fly (spec #2, #29, #84, #85). Behavior is emergent action selection from
`world → sensors → sensory neurons → connectome → motor neurons → body → world`.

## Core modules

The list below is generated from the directory, not remembered: every `.swift`
file under `ios/Sources/FlyBrainCore/` appears, with a one-line role. An earlier
version of this section was headed "Phase 1–2, current" and named 7 of the 20
files that exist — omitting `VisionSystem`, `SensoryInterface`, `SimulationCore`,
`World`, `MotorSystem`, `BodyDynamics` and `InternalState`, i.e. every subsystem
that later turned out to be mis-described. A module inventory that trails the
code by seven phases is a documentation bug with a blast radius.

```
ios/Sources/FlyBrainCore/
├── Types.swift              enums, provenance taxonomy, organism info, regions, transmitters
├── Connectome.swift         compact sparse graph: NeuronRecord / SynapseRecord /
│                            OutEdgeRange / RegionBounds; .fbpack loader/writer;
│                            region index; neuronIndices(in:)
├── NeuralEngine.swift       event-driven spiking engine (LIF L0 / AdEx L1),
│                            deterministic min-heap event queue, seeded RNG,
│                            O(active) step, rate estimate, snapshots
├── Detectors.swift          passive spike-window / rate detectors (observers only)
├── Plasticity.swift         STP + dopamine-gated learning state; `learningEnabled`
│                            defaults false — no reward signal reaches synapses yet
├── NeuronCell.swift         per-neuron model config (LIF/Izh/Poisson/oscillator)
├── NeuronInspection.swift   tap-readout: the per-neuron state surface the app uses
├── SensoryInterface.swift   sensory channel → connectome neuron selection + injection
├── VisionSystem.swift       ommatidial array, ON/OFF, flicker, looming;
│                            motion + small-object NOT yet emitted (see header)
├── InternalState.swift      hunger/thirst/arousal drives and their decay
├── MotorSystem.swift        gait CPG, stance/swing, leg & wing actuation
├── BodyModel.swift          articulated FlyBody: segments, joints, wings, halteres
├── BodyDynamics.swift       rigid-body integration, contact springs, aerodynamics
├── World.swift              lights / odors / obstacles / moving objects, raycast
├── SimulationCore.swift     the closed loop: world → sensors → connectome → body
├── ConnectomeRenderModel.swift  GPU-ready neuron buffer for the Metal renderer
├── RenderCamera.swift       orbit camera + projection for the connectome view
├── RenderPalette.swift      region/class colour mapping
├── VectorMath.swift         shared SIMD helpers (the basis-math single source)
└── Network.hpp              (stub) C++-style graph interface, excluded from the
                             SwiftPM target; not used by any code path yet
```

## Data model

- **Neuron** — packed `NeuronRecord` (44 B): dense array index, dataset, type,
  region, side, transmitter, provenance, morphology ref, CSR incoming/outgoing
  ranges, xyz.
- **Synapse** — packed `SynapseRecord` (20 B): pre/post, synapseCount,
  transmitter, sign, confidence, delaySteps, **estimatedEfficacy (INFERRED)**.
- **Ranges** — CSR outgoing adjacency: per-neuron `(start, count)` into `synapses[]`.
- **Region bounds** — for culling / LOD.
- **Source IDs** — v3, `u64` per neuron in array order: the source dataset's own
  ID, for tracing a cell back to the release it came from. Absent (zero-length,
  with `header.hasSourceIDs == false`) when the asset does not record them.
  These do not fit the 44-byte record; see `TRACEABILITY.md`.

Static topology is **shared** across flies (spec #117); dynamic neural state is
**per fly**.

## Simulation loop (deterministic, sparse)

1. Deliver all events with `time ≤ stepTime` from the min-heap into a
   `spikeAccumulator` (per-neuron summed current).
2. Integrate **only touched neurons** (LIF or AdEx) with seeded noise.
3. On threshold crossing, push new outgoing events (with per-synapse delay) into
   the heap; update telemetry.

Telemetry (FPS, spikes/s, active neurons) comes from real counters — never faked
(spec #38).

## Pipeline (off-device)

```
RAW DATA → VALIDATE → NORMALIZE → ID MAPPING → COMPRESS → .fbpack → iOS bundle
```

Implemented in `python/flybrain/` (`pid.py` record taxonomies + synthetic builder,
`pack.py` binary serialization, `validate.py` integrity checks). The compiled asset
is byte-stable across the Python writer and the Swift reader (fixed-offset
little-endian layout, documented in `Connectome.swift`).

## Threading & performance targets (Phase 12)

- Simulation core is value-type, no globals; safe to run on a background queue
  while the renderer reads a snapshot on the main thread.
- iPhone 11 target: 60 FPS UI, 30+ FPS connectome inspection; separate clock rates
  for neural / physics / render (spec #46, #80).

## Roadmap

Phase 3–14 per `PLAN.md`. Priority: real connectome ingestion (BANC + FAFB/FlyWire),
sensory interfaces, motor + articulated body, closed-loop world, Metal renderer,
Life/Connectome/Experiment modes.