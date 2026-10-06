# FlyBrain Pet — Validation

## Principle (spec #27, #61, #62, #95)

Automated tests + scenario harness prove the closed loop works. All assertions
compare against **published biological ranges** where available; measurements
are real, never tuned for demo looks.

## Current automated tests

### Python pipeline (`python/tests/`)

- synthetic build is consistent & passes integrity validation
- `.fbpack` layout: neuron block 44 B, synapse block 20 B (byte-exact)
- all synthetic neurons provenance=INFERRED (never MEASURED)
- synapse sign/transmitter validity
- key regions present (lamina, medulla, MB, VNC, leg, SEZ, wing)
- pack round-trip preserves counts

### Swift core (XCTest, `ios/Tests/FlyBrainCoreTests/`) — requires Xcode

- event heap total order & deterministic tie-break
- chain propagation produces genuine spikes (5-neuron chain)
- determinism: same seed ⇒ same trace; different seeds differ
- inhibition veto suppresses downstream firing
- snapshot/restore round-trip ⇒ identical continuation
- connectome validation catches orphan edges
- CSR layout valid
- telemetry reflects real firing

Run: `cd ios && xcodebuild -scheme FlyBrainCore -destination 'platform=iOS Simulator,name=iPhone 15' test`
(or `swift test` once a SwiftPM manifest is added).

## Planned scenario suite (spec #95)

| # | Scenario | Expected (published-informed) |
|---|---|---|
| 01 | neutral environment | exploration/rest; stable E/I balance |
| 02 | food nearby | approach within latency range |
| 03 | odor gradient | asymmetric antennal sampling → steering |
| 04 | sudden looming | escape/takeoff behavior |
| 05 | light direction change | orientation response |
| 06 | tactile stimulus | body-part-specific grooming |
| 07 | obstacle during walking | detour |
| 08 | flight initiation | wing activation → thrust |
| 09 | landing | deceleration + leg extension |
| 10 | repeated odor | habituation |
| 11 | learning | odor–reward association (dopamine-gated) |
| 12 | circadian | day/night activity differences |

## Metrics (spec #96)

approach/avoidance probability, turning bias, walking speed, flight duration,
feeding/grooming latency, response latency, orientation accuracy — compared to
published ranges, reported honestly.

## Biosanity (spec #62)

firing distributions, region activity, spike propagation, E/I balance, sensory &
motor latencies, walking/flight stability. No chasing FPS at the expense of
biological correctness (spec #121).