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
(excitatory/inhibitory/modulatory) with confidence, because fly-specific effects
can differ from vertebrate intuitions.

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