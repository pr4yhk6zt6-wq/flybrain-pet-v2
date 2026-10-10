# FlyBrain Pet — Biological Limitations

This document is a living list of every major approximation in the simulation.
It exists so users and reviewers can tell **what is measured vs inferred** at a
glance (spec #3, #28, #97, #102, #121). Nothing here is presented as measured
biology.

## Current dataset (Phase 3+ status)

- **The bundled `demo_micro.fbpack` is a SYNTHETIC stand-in.** 480 neurons,
  1250 synapses, labeled `SYNTHETIC-DEMO`; every attribute `INFERRED`.
  It exercises the engine/pipeline but represents **no real animal**.
- **This is what the app ships.** `AppModel` loads `demo_micro.fbpack`; the fly
  on screen is driven by the synthetic 480-neuron stand-in, not by a real
  connectome.
- **A real BANC CNS connectome HAS been ingested and is in the repo**:
  `data/generated/banc_cns.fbpack` — 153,746 neurons, 3,036,600 connections,
  with real neuropil and transmitter labels (provenance BANC, not INFERRED
  anatomy). It is **not bundled in the app** yet: 66.7 MiB (70.0 MB) against the
  demo's 56 KB, so shipping it is a deliberate packaging decision, not a missing
  capability.
- FAFB/FlyWire remains un-ingested. Until a real connectome is bundled, all
  "biology" the user sees in the app is a documented approximation.

## Neural dynamics

| Item | Status |
|---|---|
| Neuron model (LIF/AdEx) | APPROXIMATED — condensed vs real conductance models |
| Membrane time constants / thresholds | INFERRED from published ranges |
| Synaptic efficacy | **always INFERRED** (anatomy ≠ physiology, spec #7) |
| Synaptic delay | INFERRED (1–2 steps) |
| Neuromodulation | APPROXIMATED (dopamine gate; octopamine etc. future) |
| Plasticity | RESEARCH-grade; OFF by default; never rewrites connectome unless explicitly enabled |

## Time scale (not real time)

- **The simulation runs ~42× slower than real time.** `parameters.dt` is 0.1 ms
  of neural time and the app advances 4 steps per 60 Hz frame, so 0.4 ms of
  neural time elapses per 16.7 ms of wall time (`tools/measure_time_scale.py`,
  gated in CI). One second of neural time takes ~42 s of real time.
- **Consequence for any windowed neural signal:** the 1 s recent-spike counter
  takes ~42 s of wall time to roll. It is therefore an *inspection* signal only.
  Everything that drives the body samples the leaky firing-rate estimate
  (`rateHz`), whose time constant is 20 ms of neural time ≈ 0.83 s of real time.
  Neither is a real-time sensorimotor loop; the fly does not move at fly speed.
- **Why it is not simply made faster:** at 0.1 ms per step, reproducing the
  ~0.8 ms between two spiking neurons at true speed would need ~8,000 steps per
  frame on an A13. A faster (larger) `dt` would change the integration
  accuracy, so the clock is deliberately slow rather than coarse.
- **Motor drive fidelity is INFERRED throughout:** the leg/wing rate reference
  (`motorDriveReferenceHz = 100`) and the 20 ms rate constant are engineering
  choices matched to a documented insect sensorimotor range, not measurements
  from this connectome.

## Sensory systems

| System | Structural fidelity | Physiological fidelity |
|---|---|---|
| Vision (ommatidial array) | MEDIUM | APPROXIMATED — a real array of ommatidial axes (Drosophila ~5°) raycast against the world, emitting per-ommatidium luminance, ON/OFF edges, wide-field flicker and looming (expansion). Optic-flow / small-object motion channels are **NOT emitted**; no receptor-level phototransduction |
| Olfaction (odor fields, antennal lobe, MB) | MEDIUM | APPROXIMATED |
| Gustation (labellum/legs/proboscis) | LOW | APPROXIMATED — two contact sites (tarsal, labellar) with a physical reach gate; no receptor-level transduction, taste is a scalar acceptance, not a receptor array |
| Mechanosensation / proprioception | LOW | APPROXIMATED — tarsal contact load reaches the leg neuropil through a dedicated afferent, rectified to a phasic (high-pass) signal with a dead band so standing still produces zero drive |
| Haltere inertial feedback | LOW | APPROXIMATED — inertial input from integrated body state; no dedicated haltere mechanosensory organ model |

**Reachability note (2026-10-09).** "Wired in the core" and "reachable by the
animal" are different claims, and this project has been wrong about the second
while being right about the first. Two channels — gustation and looming — were
implemented, unit-tested and gated in the core, yet had **zero** producers in
the app: the looming detector ran every frame against a world that contained no
moving object, and `gustatoryInput` had no call site at all. Both are fixed, and
`tools/probe_app_stimulus_reachability.py` now asserts that the shipped app can
produce the stimulus each channel consumes. `Loom` in the app is the control
that makes the escape channel reachable.

## Motor / body

- Leg/wing/haltere muscle wiring → documented **biomechanical approximation**
  until connectome-derived muscle mapping is available (spec #17).
- Flight uses reduced-order aerodynamics, not CFD (spec #19).
- Gait CPGs are neural-pattern-generator approximations. The stepping rhythm
  and body translation are verified against the offline mirror and asserted in
  tests, but they are **NOT yet validated against published stepping
  kinematics** — this line claimed that validation while no such check existed
  anywhere in the repo.
- Short-term plasticity (facilitation/depression) is implemented in
  `Plasticity.swift` and applied to synapses, but the **long-term learning
  layer has no call sites in the simulation**: `learningEnabled` defaults to
  `false`, so no reward signal reaches synapses today. Learning behaviors are
  therefore not claimed.

## Behavioral claims

- Emergent action selection, **not** consciousness (spec #2).
- Behavior classifier is passive observation only (spec #44).
- No learned/memory behavior is claimed beyond the implemented plasticity layer.

## Provenance policy

Any value without a source tag is `UNKNOWN`. We never invent neuron names,
connections, transmitters, exact weights, behaviors, or regions (spec #60).

Last updated: Phase 6/7 in progress (`docs/PHYSICS.md`). Updated as subsystems
land — and the tables above must match the code, not the plan. This file
previously listed mechanosensation and haltere feedback as `PLANNED` long after
both had real call sites, and the whole file claimed "Last updated: project
scaffold (Phase 1–2)" at Phase 9; stale docs are how a missing channel survived
here before.