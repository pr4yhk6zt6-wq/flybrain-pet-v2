# FlyBrain Pet — Science Sources

Registry of biological data sources (spec #3, #88, #89). Every dataset used in
the simulation must appear here with version, provenance, license, limitation,
and access date. Integrity rule: if it is not listed here with a source, it is
UNKNOWN/INFERRED in the app.

Legend — status: `verified` = accessed & confirmed during development, `pending` = to
verify when ingestion starts. Nothing with status `pending` is presented as measured
biology in the app.

## Connectome datasets

### 1. FlyWire / FAFB (whole-brain, adult female)
- **Dataset**: FlyWire v783 (FAFB), full adult female brain connectome
- **Paper**: Dorkenwald, Matsliah, Sterling, … Seung & Murthy. "Neuronal wiring
  diagram of an adult brain of Drosophila melanogaster." *Nature* 634, 124–138 (2024).
- **Related**: Schlegel et al. "Whole-brain annotation and multi-connectome cell
  typing of Drosophila." *Nature* 634, 139–152 (2024) (cell types).
- **Portal**: https://codex.flywire.ai / https://fafb-flywire.readthedocs.io
- **License**: FlyWire community data; academia-friendly terms — confirm before
  redistribution in app bundle (see below).
- **Coverage**: whole brain; **lacks** lamina/ocellar (BANC supplements), VNC.
- **Status**: `pending` (pipeline ready; bulk download requires agreement).
- **Known limitation**: multiple annotations per cell, proofreading completeness
  varies; synaptic strengths are anatomical counts, not physiology.

### 2. BANC (brain + nerve cord, adult female)
- **Dataset**: BANC (Brain And Nerve Cord) connectome — brain, VNC, cervical
  connective, sensory/motor pathways, ascending/descending neurons.
- **Paper/preprint**: Dorkenwald et al. (2024/2025), "Whole-animal connectome of
  the adult Drosophila" (verify exact title/venue at ingestion; BANC shipped via
  https://codex.flywire.ai/banc).
- **Why it matters**: closest whole-organism match to project goal (spec #1, #4).
- **Coverage**: brain + VNC; currently **lacks** lamina and ocellar ganglion
  (FAFB/FlyWire supplies those).
- **Status**: `pending`.
- **Known limitation**: VNC synaptic reconstruction density/validation differs
  from brain; sex = adult female (default organism).

### 3. Supplementary / metadata
- **FlyBase** — gene/transcript/neurotransmitter annotations: https://flybase.org
- **Virtual Fly Brain** — anatomy/neuropil ontology & 3D templates:
  https://virtualflybrain.org
- **FlyCircuit** — templates & some cell morphology (older, male-biased in parts;
  use with care for our female default).
- **Neurotransmitter inference**: Eckstein et al. "Neurotransmitter classification
  from electron microscopy images at synaptic contacts in Drosophila."
  *Cell* 187, 2574–2594 (2024).

## Vision & behavior literature (for validation, spec #61/#62/#96)
- Borst, Haag & Reiff. "Fly motion vision." *Annu. Rev. Neurosci.* (2010) — motion
  detection, T4/T5, lobula plate tangential cells.
- Mauss, Borst et al. — wide-field motion, optomotor responses.
- **Klapoetke et al. "Ultra-selective looming detection from radial motion
  opponency." *Nature* 542, 469–473 (2017) (PMID 29120418)** — identifies
  **LPLC2** (lobula plate/lobula columnar, type II) as the Drosophila
  looming detector; selectivity comes from radial motion opponency. This is the
  cell class the app's loom channel drives, and the reason looming is modelled
  as the EXPANSION of retinal coverage: a loom is the growth of the retinal
  image, and a static object — however large — produces none.
- **Peek & Card. "Comparative approaches to escape." *Curr. Opin. Neurobiol.*
  (2016) / Gabbiani et al.** — escape is released at a fixed angular size when
  the object reaches a threshold time-to-contact; the peak response of
  loom-selective cells tracks the R/v ratio. Thresholding an expansion RATE is
  the same rule (rate ≈ 1/τ), which is how `loomExpansionReference` is defined.
- Bahl et al. — looming/escape circuits (giant fiber system, DLM).
- Card & Dickinson — escape takeoff biomechanics.
- **Inagaki, Panse & Anderson. "Independent, reciprocal neuromodulatory control
  of sweet and bitter taste sensitivity during starvation in *Drosophila*."
  *Neuron* 84:806 (2014). PMID 25451195.** — MEASURED: starvation raises sugar
  sensitivity and LOWERS bitter sensitivity, as separate pathways "recruited at
  increasing hunger levels, such that low-risk changes (higher sugar
  sensitivity) precede high-risk changes (lower sensitivity to potentially toxic
  resources)". This is why the model has TWO gustatory gains and an onset
  (`aversiveBluntingOnsetHunger`) rather than one unsigned multiplier — a single
  gain would make a starving fly *more* repelled by bitter food, backwards on
  the nutrient state where it most needs calories. Measured: direction and
  ordering. INFERRED: the curve shapes and the two span values.
- **Li & Montell. "Mechanosensory encoding of food texture in *Drosophila*."
  *Neuron* (2022). PMID 36386873.** — MEASURED: labellar mechanosensilla detect
  food grittiness via bristle deflection and flies reject gritty food. Recorded
  here because it is a REAL sense this model does NOT implement — see
  docs/WORLD.md. Called out explicitly so the absence reads as a known gap
  rather than as a claim that texture is pseudo-science.
- (feeding) Tarsal vs labellar contact chemoreception and the proboscis
  extension reflex: the tarsal route can itself release extension, which is what
  makes the tarsal→labellar bootstrap in `SensoryInterface` physically sensible.
  The model's `contactReach` (0.25 mm) and patch radius are OPTIMISATION
  constants, not measured sensitivities — see `tools/mirror_feeding_loop.py`.
- Strauss — leg coordination & walking mutants.
- Yamada et al. — grooming sequence.
- von Philipsborn et al. — courtship song (male; NOT used for female default).

## Access & redistribution notes
- **Data licenses**: BANC/FAFB/FlyWire data are distributed for research;
  bundling in a public app may require permission — the project will ship real
  data only after confirming license terms; until then the app bundles the
  synthetic demo and documents it transparently.
- Access dates: recorded in commit metadata per dataset at ingestion time.

## Policy
- Preference order (spec #88): primary papers > verified databases > secondary
  literature. Random blogs are never biological truth.
- Every constant used in code must trace to one of these sources or be tagged
  INFERRED/UNKNOWN.