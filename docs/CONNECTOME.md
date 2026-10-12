# FlyBrain Pet — Connectome

## Strategy (spec #3, #4, #118)

Hybrid pipeline:

- **BASE: BANC** — adult female brain + ventral nerve cord + cervical connective,
  with sensory/motor pathways. Closest match to the whole‑organism goal.
- **SUPPLEMENT: FAFB / FlyWire** — covers lamina + ocellar ganglion missing from
  BANC, and provides the large whole‑brain proof‑of‑principle graph.
- Optional: FlyBase / Virtual Fly Brain metadata, published literature for
  transmitter/identity predictions.

Every neuron keeps: original dataset, original neuron ID, canonical ID, cell type,
hemisphere, neuropil, body part, transmitter, confidence, source dataset,
morphology source, partner references (spec #5). Never merge graph IDs blindly.

> **"Original neuron ID" was not actually kept until format v3.** The field that
> looked like it held the ID — `NeuronRecord.canonicalID` — was filled with the
> neuron's **array index** by both writers, while this line claimed the release's
> own ID was preserved. The BANC release's `Root ID`s are 60-bit
> (measured min `720575940381905254`) and cannot fit that i32 slot, so the
> original IDs were being read and then discarded. v3 appends a `u64` per neuron
> in a separate block; `canonicalID` is now explicitly documented as the dense
> simulator index. See `TRACEABILITY.md` for the measurement and the fix.
> `tools/verify_traceability.py` is the gate.

## Data model

Packed binary `.fbpack` (see `ARCHITECTURE.md`):

```
NeuronRecord (44 B)   canonicalID, datasetID, type, region, side, transmitter,
                      provenance, morphologyIndex, CSR in/out ranges, x,y,z
SynapseRecord (20 B)  pre, post, synapseCount, transmitter, sign, confidence,
                      delaySteps, estimatedEfficacy (INFERRED)
sourceIDBlock (8 B/neuron, v3)  original dataset ID (u64), array order
```

`canonicalID` is the dense simulator index (the array position, and the
numbering synapse endpoints use) — NOT the source dataset's ID. The original ID
is `sourceIDBlock[i]`, present only when `header.hasSourceIDs` is true; an
unidentified neuron is never written as 0, because 0 is a legal ID.

Provenance classes: `MEASURED / RECONSTRUCTED / INFERRED / PREDICTED / APPROXIMATED / UNKNOWN`.

## Current bundled dataset

`data/generated/demo_micro.fbpack` — **SYNTHETIC-DEMO** (480 neurons, 1455 synapses).

- Explicitly labeled synthetic; all attributes `INFERRED`.
- Biologically motivated graph layout (retina→lamina→medulla→lobula/lobula‑plate;
  antennal lobe→mushroom body→lateral horn; central complex→SEZ→VNC→leg/wing/
  haltere neuropils; abdominal→endocrine–visceral).
- Purpose: exercise the engine, tests, pipeline and validation with real code
  paths before real data lands.

## Real data ingestion (next milestone)

Status: **ingest implemented and verified against the real release.** BANC bulk
graph downloads are research-proxy gated; the pipeline (`python/flybrain/`,
`tools/ingest_banc.py`) ingests the released files directly.

### How a neuron is placed in a region - and what is NOT measured

The release tags every neuron with the neuropils its synapses fall in, but:

1. tags can be **compound** (`ME.LO`, `NO_CONS.ME`, `HTct_UTct_T3_L` - 30,514
   neurons in banc-888), and the simulator has no compound region, so one
   component must be chosen;
2. the tag lists are **not ordered by synapse count** - measured across 18,801
   sampled neurons, the first-listed tag equals the measured argmax only 60.2%
   (input) / 46.5% (output) of the time, and only 17.9% of multi-tag lists are
   themselves descending. The file stores per-*connection* neuropil, not
   per-tag counts, so no component can be shown to be "the dominant" one.

**The primary source is the release's own `Top in/out region` column**, not the
tag reduction. The ingest reads that column per neuron and maps it to one
`RegionID`; only when the column is empty or `NO_CONS` (4,043 of 158,262
neurons) does it fall back to reducing the atlas tag lists by hand. Which source
placed each neuron is counted in `banc_cns.report.json` under `region_source`.

The column is used because it was **measured against an independent ground
truth** - the release's per-connection neuropil and synapse counts
(`connections_princeton.csv.gz`), which neither rule can see:

| population                |       n | tag reduction | release column |
|---------------------------|--------:|--------------:|---------------:|
| all, strided sample       |  42,774 |         72.5% |          79.1% |
| motor neurons             |     805 |         61.7% |          97.3% |

The strided row scores each neuron twice (once against its output-side synapse
mass, once against both sides), so its `n` counts comparisons over a 22,609-
neuron sample taken every 7th release row - not the first N. That sampling
matters: an earlier first-4,000 sample put the tag rule *ahead* (73.6% vs
72.1%), because the leading rows are optic-lobe cells, not a fair draw.

The gap is widest exactly where the simulator is most sensitive. The tag
reduction scans `input_neuropils` before `output_neuropils`, so a motor neuron
is filed by where it RECEIVES rather than where it SENDS: it put every flight
power muscle outside `WING_NEUROPIL`, leaving that region with 0 motor cells
and `wingMuscleDrive` reading a permanent zero (TASK-007). The column keeps 42
of the 48 measured wing motor cells there. Measurement tooling:
`tools/measure_region_source_unbiased.py`,
`tools/measure_motor_region_ground_truth.py`,
`tools/measure_region_source_blast_radius.py`.

Both reductions are still an explicit, documented **INFERRED choice** - a
release annotation (column or tag) is RECONSTRUCTED, the reduction of it to a
single `RegionID` is not - and both are recorded as INFERRED in `.fbpack`
provenance and in `banc_cns.report.json`. Tags the taxonomy cannot express are
counted as mapping gaps in the report, with the offending tag named and the
neuron kept: the tag vocabulary needs extending, but the neuron itself was
placed by the column. Measurement tooling:
`tools/measure_tag_order.py`, `tools/measure_banc_assumptions.py`.

### Per-transmitter sign

See `docs/BIOLOGY.md` for the per-transmitter table and citations. In short:
ACh +1, GABA -1, **HIST -1** (chloride channel), **GLUT 0** (iGluR excitatory
and GluClalpha inhibitory both exist), **DA/SER/OCT/TYR 0** (GPCR-only),
unpredicted 0. A sign of 0 carries **no fast current** (the engine skips it);
reading it as +1 is what previously asserted millions of unmeasured excitatory
synapses.

## Cross‑dataset alignment (spec #91)

Matching by morphology, cell type, region, hemisphere, known identity,
connectivity, and published mappings — **never** nearest‑neighbor coordinates
alone. Each mapping gets a confidence score.

## Integrity (spec #90)

`flybrain/validate.py` + `tools/tests/validate_fbpack.py` enforce: duplicate IDs,
missing neurons, orphan edges, invalid endpoints, bad transmitter/region/provenance,
coordinate errors, dataset mismatch. Serious errors **fail the build**.