# FlyBrain Pet — Embodied Drosophila Neural Simulation for iPhone

> "Do not create a fly pet with scripted AI. Create a biologically grounded embodied neural simulation in which the fly's behavior **emerges** from a connectome-based nervous system."

**FlyBrain Pet** is an iOS app (iPhone 11 and newer, A13+) that simulates an adult female *Drosophila melanogaster* as a **closed-loop embodied nervous system**:

```
WORLD → sensors → sensory neurons → CONNECTOME → motor neurons → BODY → WORLD → ...
```

There is no behavior tree, no scripted personality, no LLM controlling the fly.
Behavior is **emergent action selection** from real spiking neural dynamics over connectome-derived network topology.

## Status
- [x] Phase 1: repo scaffold, architecture, data model, provenance system
- [x] Phase 2 (core): event-driven spiking neural engine (LIF/AdEx), sparse spike propagation, deterministic, unit-tested
- [x] Phase 3 (sensory + loop): vision (compound-eye sampling, ON/OFF/looming), olfaction, gustation, mechano/haltere transduction; internal state; closed-loop `SimulationCore`; passive behavior classifier
- [x] Phase 4/5 starters: motor CPG (6-leg gait), articulated body model, 3D `World` (lights/odor/obstacles, collision) wired into the loop
- [x] CI/CD: GitHub Actions — Python pipeline tests + Swift core build/test on macOS runners; signed .ipa workflow (requires app shell, Phase 9-10)
- [ ] Phase 6+: flight dynamics, walking validation, connectome visualization (Metal), Life/Connectome/Experiment modes, real BANC/FAFB ingestion

**Current caveat:** the bundled dataset (`data/generated/demo_micro.fbpack`) is a **small synthetic stand-in** explicitly labeled `SYNTHETIC-DEMO`. It exists so the engine, tests and pipeline are real and verifiable *now*. Real connectome ingestion (BANC + FAFB/FlyWire, adult female) is the next priority — see `docs/CONNECTOME.md` and `python/tools/`.

## Scientific integrity (short version)
- Every neuron/synapse carries **provenance + confidence** (`MEASURED` … `UNKNOWN`).
- Anatomical connection ≠ physiological strength → we store `estimatedEfficacy` as **INFERRED**, never as measured.
- Every visual spike in the app corresponds to a real simulation event. No decorative sparkles.
- Full rules: `docs/BIOLOGY.md`, `docs/BIOLOGICAL_LIMITATIONS.md`, `docs/SCIENCE_SOURCES.md`.

## Repository layout
```
ios/Sources/FlyBrainCore/   Swift simulation core (platform-free, testable)
ios/Tests/FlyBrainCoreTests/ XCTest
python/                      desktop data pipeline (raw → compressed iOS assets)
tools/tests/                 pipeline validation harness
docs/                        architecture + science docs
data/generated/              compiled .fbpack assets
scripts/                     build & test helpers
```

## Build & test
Pipeline (macOS / Linux / iSH):
```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r python/requirements.txt
python3 -m pytest python/tests -q            # pipeline tests
./scripts/gen_synth_dataset.sh               # regenerate synthetic demo asset
python3 tools/tests/validate_fbpack.py data/generated/demo_micro.fbpack
```
iOS core (any platform with Swift toolchain):
```sh
cd ios && swift build && swift test          # SwiftPM — runs on macOS AND Linux
```
iOS app + Xcode project (generated in CI, or locally on macOS):
```sh
brew install xcodegen
cd ios && xcodegen generate && xcodebuild -project FlyBrainPet.xcodeproj \
  -scheme FlyBrainPet -destination 'generic/platform=iOS Simulator' build
```

### Building an .ipa without a MacBook
The repo's **GitHub Actions** build it for you:
1. Push to `main` → `CI — Pipeline & Core Tests` runs Python pipeline + Swift core build/test on macOS runners automatically.
2. Open **Actions → "Build iOS .ipa (signed, on demand)" → Run workflow**.
3. To sign for a real iPhone (11+), first add repo secrets (Settings → Secrets and variables → Actions):
   `APPLE_CERT_BASE64` (distribution .p12, base64), `APPLE_CERT_PASSWORD`,
   `APPLE_PROFILE_BASE64`, `APPLE_TEAM_ID`.
4. Download the `.ipa` artifact and install (Apple Configurator / AltStore / TestFlight).

Until the signing secrets exist, the workflow still produces an **unsigned simulator build** so the app shell can be tested in the Simulator.

## License & data
- Code: MIT (see `LICENSE`).
- Synthetic demo dataset: CC0 (generated, not biological data).
- Real connectome datasets are external works with their own licenses — documented in `docs/SCIENCE_SOURCES.md`.