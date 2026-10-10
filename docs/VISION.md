# FlyBrain Pet — Visual System

## Goal (spec #11, #12, #98)

The visual system is **not** a camera/CNN feeding labels into the brain. It is a
biological approximation of the Drosophila compound-eye pipeline:

```
WORLD → FLY EYE → RETINAL SIGNAL → OPTIC LOBE → spiking response → MOTOR OUTPUT
```

## Pathway

```
Photoreceptors
  → Lamina     (contrast, ON/OFF edge detection)
  → Medulla     (motion polarity, direction selectivity precursors)
  → T4/T5-like + lobula lobula plate (wide-field motion, ON/OFF, looming)
  → descending neurons → thoracic/VNC motor systems
```

The pathway above is the **biological target**. The rows marked EMITTED are the
channels that actually produce `VisualEvent`s today; the rest are
**NOT EMITTED** — the `VisualPathway` enum and `mapToInput` reserve targets for
them, and reserving a target is not implementing a channel. This list is
mirrored exactly by the header of `ios/Sources/FlyBrainCore/VisionSystem.swift`,
which is the authority; if it disagrees with this document, the header wins.

| Channel | Status | Note |
|---|---|---|
| brightness / contrast (local) | EMITTED | per-ommatidium luminance, adapted baseline |
| ON and OFF pathways | EMITTED | separate dark/light edges |
| wide-field flicker (temporal derivative) | EMITTED | feeds the medulla target |
| looming (expansion) — threat input (spec #23) | EMITTED | the escape channel; see `Vision-Looming` |
| optic flow (translational, rotational) | **NOT EMITTED** | `motionDirectional` target reserved only |
| small-object motion | **NOT EMITTED** | `smallObject` target reserved only |
| self-motion compensation (haltere-driven, spec #16) | NOT EMITTED here | haltere input exists as a separate mechanosensory channel, not as a visual one |

## Sampling strategy (spec #12)

- **One fixed ommatidial array.** The eye raycasts `Ommatidium` axes (Drosophila
  inter-ommatidial angle ~5°) against the world at a constant range. There is no
  HIGH/MEDIUM/LOW-power level of detail: an earlier version of this document
  described those tiers and `EyeConfig` carried an unread `lod` field to match.
  The field had no reader and there are no tiers to select, so both are gone —
  re-add them together, with the sampling code that chooses between them.

The neural system receives **feature signals** matching biological visual
channels, not raw pixels. No decorative blinkies — every spike corresponds to a
real simulated event.

## Use in behavior

Looming drives neurons officially labeled as visual-motion / wide-field /
descending; they synapse into motor pattern generators (escape, orientation,
landing). Looming expansion is computed from the sampled field, then converted
to synaptic drive — never via `if threat → flee` rules (spec #23, #43).

**Direction selectivity is not computed yet** — that sentence described the
intended design, not the code. Only expansion is computed today.

See `docs/BIOLOGICAL_LIMITATIONS.md` for the measured vs inferred split.