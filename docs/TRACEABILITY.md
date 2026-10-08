# FlyBrain Pet — ID traceability

Golden rule #1 is "real connectome topology, **traceable IDs**"; spec #4 requires
every neuron to keep its *original* dataset + neuron ID and spec #120 requires
connectome IDs to remain traceable. This document records what the wire format
actually carries, measured on the shipped assets, and why.

## The defect this documents

`NeuronRecord`'s first field is `canonicalID: Int32`. Until v3 every writer
filled it with the neuron's **position in the kept array**:

| writer | code |
|---|---|
| `flybrain/banc.py` | `canonicalID=len(neurons)` |
| `flybrain/pid.py` (demo) | `canonicalID=neuron_id` (a running counter) |
| `Connectome.packNeuronRecord` (Swift) | the field as given |

Meanwhile the ingest *reads* the release's own `Root ID` (`banc.py`,
`_load_neuron_table`) and keeps it only as an in-memory dict key
(`canonical[rid] = len(neurons) - 1`) used to resolve synapse endpoints. It never
reaches the file.

So on the shipped BANC asset, `canonicalID` is `0..153745` — reconstructible
from the array position and therefore carrying **no information**. A user can
select a neuron, but nothing in the asset says which BANC cell it is, so the
selection cannot be checked against the release, re-ingested, or cross-referenced
to FAFB. `docs/CONNECTOME.md` claimed the opposite.

### Measured magnitude

The BANC release root IDs are **60-bit** integers (measured on
`data/raw/flywire/banc/neurons.csv.gz`):

```
rows 158262   min 720575940381905254   max 720575941734593579   bits 60
fits in int32? False     fits in int64? True
```

`720575940381905254 = 0x0A00000000281166`: the top byte is `0x0A`, i.e. these
are not small counters but 64-bit FlyWire-style body IDs. They cannot go in the
existing `i32` slot at all — and with 60 bits used there is no room to pun the
field as unsigned either.

The magnitude is what makes the defect hard to see rather than merely real: an
asset whose IDs are `0..n-1` looks *correct*, because every value is plausible
and nothing in the file is self-contradictory. The only way to notice is to go
and look at what the numbers in the release actually are.

## v3 layout

The neuron record stays **44 bytes** — widening `canonicalID` to `i64` in place
would move every following offset and change the stride on a format that three
implementations parse, and the stride is not the problem. Instead:

1. `canonicalID: i32` keeps its slot and its meaning becomes explicit: it is the
   **dense simulator index**, equal to the array position and to the endpoint
   numbering used by `SynapseRecord.preNeuron/postNeuron`. Both writers do
   produce `canonicalID == i`; that is now stated in the type's doc comment
   rather than left incidental, because the field still *looks* like it holds an
   ID and the next reader will assume it does.
2. A new length-prefixed block, `sourceIDs`, carries one **`u64` per neuron**
   immediately after the `region` block. Neuron `i`'s original release ID is
   `sourceIDs[i]`. Block order (neuron, synapse, range, region, sourceIDs) keeps
   the four pre-existing blocks at their old offsets, so only a v3 reader has to
   know about the new block — and a pre-v3 reader that stops reading after
   `region` sees a shorter well-formed file rather than a misparse.

An absent block is meaningful: it means **the asset has no original IDs**, which
is different from "the ID is 0". The writer emits the block only when *every*
neuron has an ID (`pack._source_id_bytes`), sets `header.hasSourceIDs` from that
same helper, and the Swift loader rejects a file whose flag and payload disagree
in either direction. Accessors return `nil` rather than a fabricated `0`, and
`Connectome.validate()` rejects duplicate IDs — two neurons naming the same cell
is worse than no traceability, because one of them resolves to the wrong neuron
while still looking authoritative.

Why a separate block rather than a column in the header JSON: 153,746 × 8 B =
1.2 MB of binary vs ~2.2 MB of JSON digits, and the header is decoded by
`JSONDecoder` on the main thread at load.

## Per-neuron identity, not just the header

The header also carries `sourceDatasets: [String]` (e.g. `["BANC"]`) and each
neuron carries `datasetID: u8`. That pair answers "which release", the
`sourceIDs` block answers "which cell in it", and together they satisfy spec #4.
Cross-dataset alignment (spec #91) needs exactly this pair to be checkable.

## What remains INFERRED

Nothing about this changes what was *measured*: the ID is RECONSTRUCTED (it is
read from the release), but the region reduction and the transmitter stay
INFERRED/PREDICTED as documented in `CONNECTOME.md`. Traceability is about being
able to go back to the source, not about the source being right.

## Gates

- `tools/verify_traceability.py` (in CI) — reads the **shipped demo asset** and
  the Swift/Python sources, and asserts: the block is present, its length is
  `neuronCount × 8`, every ID is distinct, **no ID equals its array index**, the
  IDs exceed `int32` (so a reader that still took the ID from the `canonicalID`
  slot cannot pass), and that the two languages agree on the layout. It runs in
  CI, so it works only on the committed demo asset — the real BANC asset is
  `.gitignore`d and is not on the runner.
- `tools/verify_banc.py` (**not in CI** — needs the 48 MB raw release) — replays
  `neurons.csv.gz` and `connections_princeton.csv.gz` and is the only gate that
  checks the real asset against the release: every sampled neuron's **Root ID is
  a member of the release's own `Root ID` set**, its class byte matches the
  release's `Super Class` label, and its region/side/position re-derive. Run it
  by hand against `data/generated/banc_cns.fbpack` after any ingest change.
  (An earlier revision of this document credited the two per-neuron checks to
  `verify_traceability.py`; that gate cannot do them, and for a while
  `verify_banc.py` did not either — it computed both counters and then never
  asserted them, so the comparisons were discarded.)
- `python/tests/test_banc.py` — `test_banc_asset_records_the_release_root_ids`,
  `test_source_id_is_not_the_array_index`, `test_asset_without_source_ids_says_so`,
  `test_source_id_block_survives_the_round_trip_multi_byte`.
- `ios/Tests/FlyBrainCoreTests/FBPackFormatTests.swift` — round trip, 60-bit
  values past `int32`, absent-is-nil, duplicate rejection, v2 rejection.
- `ios/Tests/FlyBrainCoreTests/FBPackCrossLanguageTests.swift` — decodes the
  block out of the Python-written demo asset's bytes and requires the Swift
  reader to agree with them.

Both the gate and the tests were checked by reverting the fix and confirming
they fail (three checks fire on `verify_traceability.py`; see the commit
message). A gate that cannot fail is a branch nobody tests.

### A second defect found while doing this

The BANC writer had its own hand-rolled neuron serialiser, and it wrote a
literal `0` into the `flags` byte instead of `n.flags`. The ingest had already
computed the release's `Super Class` label and handed it to the model; the
serialiser discarded it, so the shipped whole-CNS asset had **every** neuron
classless (measured: `motor 0, sensory 0` over 153,746 cells). The runtime motor
readout depends on that label to avoid summing the sensory afferents that share
the leg neuromere, and the shipped-asset gate had nothing to find because the
label never reached the file. Fixed with `n.flags` in the record; measured
after: `motor 805, sensory 15595`. Pinned by
`test_banc_serialiser_keeps_the_class_label`, and the fixture now labels one
motor and one sensory cell so the case is observable at all.