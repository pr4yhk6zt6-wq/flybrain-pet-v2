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
  → Medulla    (motion polarity, direction selectivity precursors)
  → T4/T5-like + lobula lobula plate (wide-field motion, ON/OFF, looming)
  → descending neurons → thoracic/VNC motor systems
```

Represented feature channels (from literature on fly motion vision):

- brightness / contrast (local)
- ON and OFF pathways (dark/light edges handled separately)
- optic flow (translational, rotational)
- looming (expansion) — threat input (spec #23)
- small-object motion
- self-motion compensation (haltere-driven, spec #16)

## Sampling strategy (spec #12)

- HIGH DETAIL: selected retinal region (e.g. fovea-like area of interest)
- MEDIUM: full eye sampling grid
- LOW POWER: heavily downsampled visual field

The neural system receives **feature signals** matching biological visual
channels, not raw pixels. No decorative blinkies — every spike corresponds to a
real simulated event.

## Use in behavior

Motion/looming drive neurons officially labeled as visual-motion / wide-field /
descending; they synapse into motor pattern generators (escape, orientation,
landing). Direction selectivity and looming expansion are computed from the
sampled field, then converted to synaptic drive — never via `if threat → flee`
rules (spec #23, #43).