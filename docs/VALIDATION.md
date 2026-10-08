# FlyBrain Pet — Validation

## Principle (spec #27, #61, #62, #95)

Automated tests + scenario harness prove the closed loop works. All assertions
compare against **published biological ranges** where available; measurements
are real, never tuned for demo looks.

## Current automated tests

### Python pipeline (`python/tests/`)

- synthetic build is consistent & passes integrity validation
- `.fbpack` layout: neuron block 44 B (field offsets pinned), synapse block
  20 B, **region block 28 B** (byte-exact)
- all synthetic neurons provenance=INFERRED (never MEASURED)
- synapse sign/transmitter validity, per-transmitter (ACh +1, GABA −1,
  HIST −1, GLUT 0, amines 0, unpredicted 0)
- key regions present (lamina, medulla, MB, VNC, leg, SEZ, wing)
- pack round-trip preserves counts
- **the complete banc-888 neuropil vocabulary (111 tags) maps to a region** —
  the guard that catches a dropped/renamed release tag without the raw data
- compound tags resolve through their components; a tag is split on "." only
- BANC ingest: neuron keep/drop accounting by reason, CSR tiling, u16 cell-type
  round-trip with a >255 vocabulary, positions in the release's own format

### Real-data validation (`tools/verify_banc.py`, `tools/measure_*.py`)

The BANC ingest is verified against measurements taken on the real banc-888
release, not against hand-written constants: connection ordering and
duplication, Root-ID ordering, coordinate-frame orientation, voxel scale
(4/4/40 nm), transmitter census, tag-list ordering, and the full drop
breakdown with the number of connections each drop actually removes.

`tools/verify_banc.py` is the gate that compares the compiled `.fbpack` against
the release itself (it reads both, so a bug in the ingest cannot validate
itself). **It is not run in CI** — it needs the 48 MB raw release, which is not
on the runner — so it must be run by hand after any ingest change:

```
python3 tools/verify_banc.py data/generated/banc_cns.fbpack --sample 3000
```

It re-derives, per sampled neuron, the region, side, soma position, **the class
byte from the release's `Super Class`**, and **the neuron's own Root ID**, and
per sampled edge the `(pre, post, merged synapse count)`. Every one of those
must be asserted for the comparison to mean anything: two of them were once
computed and then discarded, and the verifier passed on an asset whose class
bytes and IDs were both wrong. Verified to FAIL by doctoring one neuron's class
byte and one neuron's ID in a copy of the real asset (3 checks fire).

### Swift core (XCTest, `ios/Tests/FlyBrainCoreTests/`) — requires Xcode

- event heap total order & deterministic tie-break
- chain propagation produces genuine spikes (5-neuron chain)
- determinism: same seed ⇒ same trace; different seeds differ
- inhibition veto suppresses downstream firing
- snapshot/restore round-trip ⇒ identical continuation
- connectome validation catches orphan edges
- CSR layout valid
- a **sign-0 (unpolarised) synapse carries no current** — both graphs identical
  except the sign, so only polarity can explain the difference
- telemetry reflects real firing
- `.fbpack` format round-trips: a neuron keeps every field (id, region, side,
  transmitter, provenance, u16 type, morphology, in/out ranges, xyz), region
  bounds survive the 28-byte stride, and validateCSR() accepts a real asset

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