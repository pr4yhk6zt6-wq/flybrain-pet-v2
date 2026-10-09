# FlyBrain Pet — World

## Design (spec #29, #30, #31)

A miniature 3D environment where every object exposes **sensory properties** —
the world is the source of stimulation, never the source of commands.

Objects (implemented, and what is still missing):

- FOOD → odor + taste + **finite reserve → energy** ✅
  Ingestion is real: `World.ingest` removes reserve until the patch is bare, so
  the energy the fly gains is the matter that left the substrate. The patch has
  a radius because the fly's tarsi and its extended labellum are ~1.2 mm apart —
  a point source could not be touched by both, and the tarsal→labellar bootstrap
  would be physically impossible.
  ⚠️ **TEXTURE IS NOT MODELLED.** This line previously promised it. It is a real
  sense (Li & Montell, *Neuron* 2022, PMID 36386873: labellar mechanosensilla
  report grittiness and flies reject gritty food) but there is no `texture` field
  and nothing reads one; inventing the field would repeat the unread-variable
  bug this project keeps finding. Substrate mechanosensation that *is* modelled
  arrives via the tarsal load channel.
- WATER → odor + contact + hydration
  ⚠️ Water does not deplete (`OdorSource.isNutritive` is false for `.water`, so
  it is neither swallowed nor drunk-from). `hydration` currently only falls;
  the thirst half of the loop is NOT closed.
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