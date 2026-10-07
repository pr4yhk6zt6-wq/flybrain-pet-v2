# FlyBrain Pet — Biological Limitations

This document is a living list of every major approximation in the simulation.
It exists so users and reviewers can tell **what is measured vs inferred** at a
glance (spec #3, #28, #97, #102, #121). Nothing here is presented as measured
biology.

## Current dataset (Phase 1–2 status)

- **The bundled `demo_micro.fbpack` is a SYNTHETIC stand-in.** 480 neurons,
  1250 synapses, labeled `SYNTHETIC-DEMO`; every attribute `INFERRED`.
  It exercises the engine/pipeline but represents **no real animal**.
- Real BANC + FAFB/FlyWire (adult female) ingestion is the next milestone and is
  gated on dataset access. Until then, all "biology" in the app is a
  documented approximation.

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
| Vision (compound-eye approximation) | MEDIUM | APPROXIMATED (feature channels, no real ommatidia) |
| Olfaction (odor fields, antennal lobe, MB) | MEDIUM | APPROXIMATED |
| Gustation (labellum/legs/proboscis) | PLANNED | n/a yet |
| Mechanosensation / proprioception | PLANNED | n/a yet |
| Haltere inertial feedback | PLANNED | n/a yet |

## Motor / body

- Leg/wing/haltere muscle wiring → documented **biomechanical approximation**
  until connectome-derived muscle mapping is available (spec #17).
- Flight uses reduced-order aerodynamics, not CFD (spec #19).
- Gait CPGs are neural-pattern-generator approximations, validated against
  published stepping kinematics.

## Behavioral claims

- Emergent action selection, **not** consciousness (spec #2).
- Behavior classifier is passive observation only (spec #44).
- No learned/memory behavior is claimed beyond the implemented plasticity layer.

## Provenance policy

Any value without a source tag is `UNKNOWN`. We never invent neuron names,
connections, transmitters, exact weights, behaviors, or regions (spec #60).

Last updated: project scaffold (Phase 1–2). Updated as subsystems land.