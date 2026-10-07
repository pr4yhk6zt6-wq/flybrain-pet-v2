# FlyBrain Pet — Biology

## Default organism (spec #1)

**Drosophila melanogaster, adult female.** The principal whole-brain FlyWire/FAFB
dataset and the newer BANC dataset both derive from adult females (BANC also
includes the ventral nerve cord, neck connective, sensory and motor pathways).
This project therefore builds the **female** organism and does **not** invent
male-specific circuitry. A male connectome can be loaded later as a separate
biological dataset through `ConnectomeProvider` (spec #118).

## Nervous system organization (spec #10)

Regions supported by `RegionID`:

- Optic system: retina (L/R), lamina, medulla, lobula, lobula plate, optic lobe
- Central brain: antennal lobe, mushroom body, lateral horn, central complex,
  superior brain, SEZ
- Neck: cervical connective
- VNC: ventral nerve cord, leg neuromeres, wing neuropil, haltere neuropil,
  abdominal neuromeres
- Endocrine / visceral pathways

The BANC dataset currently lacks the lamina and ocellar ganglion; FAFB/FlyWire
supplies those. Cross-dataset ID mapping retains provenance (spec #4).

## Transmitter classes (spec #8)

Cholinergic, GABAergic, glutamatergic, dopaminergic, serotonergic,
octopaminergic, tyraminergic, peptidergic, histaminergic, unknown.
Sign is not implied by transmitter: `SynapseRecord.sign` is stored explicitly
(excitatory +1 / inhibitory −1 / **unpolarised 0**) with confidence, because
fly-specific effects can differ from vertebrate intuitions.

### What each transmitter's inferred sign is, and why

The BANC release predicts a transmitter per neuron but no valence, so the sign
is inferred from the label. Three of the ten labels do NOT map onto a fast
excitatory/inhibitory current, and treating them as "excitatory by default"
is the single largest source of invented activity in the model:

| label | sign | basis |
|---|---|---|
| ACh | +1 | cation-permeant nicotinic AChR (fast EPSP) |
| GABA | −1 | Rdl GABA-gated Cl⁻ channel — the canonical fast inhibitor |
| **HIST** | **−1** | histamine-gated **Cl⁻** channels (hclA/ort, HisCl1/2) hyperpolarise lamina L1–L3 (Gengs 2002 PMID 12196539; Zheng 2002 PMID 11714703) |
| **GLUT** | **0** | BOTH exist: ionotropic GluR is excitatory (the larval NMJ is glutamatergic and excitatory — Jan & Jan 1976 PMID 186587) and GluClα is inhibitory (Liu & Wilson 2013 PNAS PMID 23729809). Valence is cell-type-specific, so a bare "GLUT" cannot decide it. |
| DA / SER / OCT / TYR | 0 | GPCR-only families in Drosophila (DopR/DopEcR, 5-HT1/2/7, OAMB/OctβR, TyrR/TAR1) — no fast current to model. The ionotropic amine-gated channels (MOD-1, LGC-55) are *C. elegans*, not fly. |
| peptidergic | 0 | slow neuromodulation |
| unknown (unpredicted) | 0 | no evidence for either polarity |

`unpolarised` (0) means **the edge carries no fast current** — the engine skips
it — NOT "excitatory with a small weight". On banc-888 that is 59.4% of synaptic
weight (GABA −1 40.6%; ACh +1 none of it; the rest 0), so the sign convention is
load-bearing: reading 0 as +1 asserts 2.2M excitatory synapses that were never
measured. What the simulator loses by zeroing a GPCR/peptidergic edge is
neuromodulation, and that is recorded as a limitation rather than silently
folded into excitation.

## Sensory → neural → motor pathways represented

- **Vision**: photoreceptor → lamina → medulla → T4/T5-like → lobula plate →
  wide-field motion → descending → motor (spec #11). Approximated as biological
  feature channels (ON/OFF, looming, optic flow), **not** a generic camera/CNN.
- **Olfaction**: antennal sampling → antennal lobe → projection neurons →
  mushroom body / lateral horn (spec #13).
- **Gustation**: labellum / legs / proboscis contact → SEZ → feeding circuits
  (spec #14).
- **Mechanosensation & proprioception**: bristles, legs, wings, halteres,
  neck (spec #15, #16).
- **Motor**: connectome → motor neurons → VNC → leg/wing/haltere muscles
  (spec #17). Where exact muscle wiring is unavailable we use a documented
  biomechanical approximation and say so.

## Internal state (spec #24)

Energy, hydration, temperature, circadian phase, arousal, satiety, stress/injury,
learning state — these **modulate** neural/neuromodulatory input; they do **not**
select behavior by threshold rules.

## Key caveats (full list in `BIOLOGICAL_LIMITATIONS.md`)

- Anatomical connection ≠ physiological strength. `estimatedEfficacy` is always
  INFERRED (spec #7, #97).
- Synaptic delays and membrane parameters are inferred approximations calibrated
  to published ranges where available.
- The currently bundled dataset is a **synthetic stand-in** (see
  `CONNECTOME.md`); real BANC/FAFB ingestion is the next milestone.