# FlyBrain Pet — World

## Design (spec #29, #30, #31)

A miniature 3D environment where every object exposes **sensory properties** —
the world is the source of stimulation, never the source of commands.

Objects (planned):

- FOOD → odor + taste + texture + energy
- WATER → odor + contact + hydration
- FRUIT / FERMENTED MATERIAL → strong odor + nutrition
- LEAVES / BRANCHES → visual + tactile surfaces
- WALLS / OBSTACLES → visual + tactile
- LIGHT SOURCES → visual intensity + wavelength approximation
- MOVING OBJECTS / OTHER FLIES → visual motion + looming
- SHELTER → rest / thermoregulation opportunity

## Physics (spec #30)

Gravity, collision, friction, surface contact, airflow approximation,
temperature, light. Not video-game movement.

## Time (spec #31)

Simulation clock independent of render clock:

- REAL TIME (1×), FAST (5×), VERY FAST (20×), PAUSED
- Accelerating time preserves neural event ordering and numerical stability —
  steps are never skipped, only batched.

## Circadian (spec #25)

Day/night cycle: light, dawn/dusk, temperature change, feeding availability,
sleep/rest opportunity. Circadian phase modulates neural/neuromodulatory state —
it does not swap a behavior table.

## Odor field (spec #13)

Odor sources with concentration, diffusion, gradient; left/right antennal
sampling → antennal lobe → mushroom body / lateral horn. The fly discovers food,
decay, fermentation, aversive substances through the network, not an
`if odor == food → eat` rule.

## Player interaction (spec #42)

The player changes the **environment**: drop food, move objects, touch, change
light/temperature. The nervous system responds. No direct control of the fly.
An "Observe Only" mode disables interference entirely.