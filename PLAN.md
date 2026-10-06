# FlyBrain Pet — Engineering Plan

Project: Embodied Drosophila neural simulation for iPhone 11+ (iOS 16+).
Repo: `pr4yhk6zt6-wq/flybrain-pet-v2` (started empty — scaffolded from scratch).

## Golden rules (from master prompt)
1. Real connectome topology, traceable IDs, provenance labels (MEASURED / RECONSTRUCTED / INFERRED / PREDICTED / APPROXIMATED / UNKNOWN).
2. Closed loop only: world → sensors → sensory neurons → connectome → motor neurons → body → world.
3. NO behavior trees, NO scripted fallback, NO LLM/RL controller, NO fake spikes.
4. iPhone 11 (A13) is a first-class target: Swift + Metal + packed structs, event-driven sparse neural core.
5. Scientific instrument over game. When in doubt, mark it INFERRED/UNKNOWN.

## Phase breakdown (from #100 / #107)
1. **Repository audit + scaffold** — done: empty repo, created architecture + core.
2. **Connectome data pipeline** — Python → compact binary assets (`.fbpack`). Current: synthetic compact dataset (identified as such) + real FlyWire pilot support.
3. **Spiking neural simulator** — LIF/AdEx event-driven core (this commit).
4. Sensory interfaces (vision/olfaction/gustation/mechano).
5. Motor system + articulated body model (skeleton, wings, halteres).
6. Walking / 7. Flight / 8. Closed-loop world physics.
9. Connectome visualization (Metal instanced points; no decorative spikes).
10. Life Mode / 11. Experiment Mode / 12. Optimize / 13. Validate / 14. Polish.

## Real connectome data strategy (live research)
- Target: **BANC** (brain+VNC, adult female — closest to spec #1) + **FAFB/FlyWire** for lamina/ocellar regions absent from BANC (spec #4).
- BANC bulk graph downloads are research-proxy gated → we build the pipeline now and will ingest real data when access is granted; current bundled dataset is an openly-licensed synthetic stand-in explicitly labeled `SYNTHETIC-DEMO`.
- FlyWire codex graph: community edition is accessible; pilot ingestion script included (`python/tools/fetch_flywire.py`).

## Branch strategy (spec #108)
main (stable) ← develop ← feature/neural-core, feature/connectome-import, feature/vision, feature/body, feature/world, feature/connectome-ui