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

## Data model

Packed binary `.fbpack` (see `ARCHITECTURE.md`):

```
NeuronRecord (44 B)   canonicalID, datasetID, type, region, side, transmitter,
                      provenance, morphologyIndex, CSR in/out ranges, x,y,z
SynapseRecord (20 B)  pre, post, synapseCount, transmitter, sign, confidence,
                      delaySteps, estimatedEfficacy (INFERRED)
```

Provenance classes: `MEASURED / RECONSTRUCTED / INFERRED / PREDICTED / APPROXIMATED / UNKNOWN`.

## Current bundled dataset

`data/generated/demo_micro.fbpack` — **SYNTHETIC-DEMO** (480 neurons, 1250 synapses).

- Explicitly labeled synthetic; all attributes `INFERRED`.
- Biologically motivated graph layout (retina→lamina→medulla→lobula/lobula‑plate;
  antennal lobe→mushroom body→lateral horn; central complex→SEZ→VNC→leg/wing/
  haltere neuropils; abdominal→endocrine–visceral).
- Purpose: exercise the engine, tests, pipeline and validation with real code
  paths before real data lands.

## Real data ingestion (next milestone)

Status: **in progress.** BANC bulk graph downloads are research‑proxy gated; the
pipeline (`python/flybrain/`, `python/tools/`) is ready to ingest once access is
granted. FlyWire codex community graph: pilot script at `python/tools/fetch_flywire.py`
(placeholder; see repo docs).

## Cross‑dataset alignment (spec #91)

Matching by morphology, cell type, region, hemisphere, known identity,
connectivity, and published mappings — **never** nearest‑neighbor coordinates
alone. Each mapping gets a confidence score.

## Integrity (spec #90)

`flybrain/validate.py` + `tools/tests/validate_fbpack.py` enforce: duplicate IDs,
missing neurons, orphan edges, invalid endpoints, bad transmitter/region/provenance,
coordinate errors, dataset mismatch. Serious errors **fail the build**.