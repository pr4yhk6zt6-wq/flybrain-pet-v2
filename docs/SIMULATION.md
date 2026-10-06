# FlyBrain Pet — Simulation

## Principle (spec #2, #28, #43)

Closed loop only:

```
WORLD → SENSORS → SENSORY NEURONS → CONNECTOME → INTERNAL STATE
      → MOTOR NEURONS → BODY → WORLD → NEW SENSORY INPUT → …
```

Behavior is **EMERGENT ACTION SELECTION** — the network produces competing and
interacting motor outputs. We never claim consciousness or subjective experience.

## Neural model hierarchy (spec #6, #23)

| Level | Model | Use |
|---|---|---|
| L0 | simple LIF | background / low-power regions |
| L1 | AdEx-like adaptive LIF | default whole-network |
| L2 | compartmental approximation | selected circuits (future) |
| L3 | full detail | single selected neuron/morphology (future) |

The engine currently implements L0 + L1 with region-based parameter defaults
(motor periphery: fast/small τ; mushroom body/central complex: strong adaptation).

## Event-driven execution (spec #6)

- Sparse: only neurons receiving input this step are integrated.
- Deterministic min-heap of `SynapticEvent(time, seq, post, current, transmitter)`
  with `(time, seq)` ordering → reproducible replays (spec #63).
- Seeded xorshift64* noise; same seed + connectome + parameters ⇒ same trace.

## Synapses (spec #7)

Each synapse records topology + counts + transmitter + sign + confidence +
delay + **inferred** efficacy. Plasticity (STP, dopamine-gated learning) is a
separate dynamic layer (spec #9) — it never rewrites the structural connectome
unless an explicit experimental feature is enabled.

## Telemetry (spec #38)

Real counters only: render FPS, neural events/sec, active neurons, simulation
time/speed, CPU/GPU/RAM. The HUD never shows fabricated numbers.

## Determinism & snapshots (spec #56, #63, #64, #94)

`NeuralEngine.snapshot()/restore()` capture clock, RNG, dynamics, event heap →
save/load/replay/time-travel are exact. Same inputs ⇒ same outcome; stochastic
mode uses controlled, seeded noise.

## Replay

Derived from snapshot + event log (neural spikes, world changes, parameter
changes, player interactions). Enables "why did the fly escape?" post-hoc
analysis driven by real simulation state (spec #45).