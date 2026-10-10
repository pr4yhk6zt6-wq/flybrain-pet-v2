import XCTest
@testable import FlyBrainCore

/// The feeding loop (spec #24, #29): hunger has a CONSUMER, and a meal is MATTER
/// that moves out of the world.
///
/// Every test here asserts a quantity, never a flag. "Did it eat" is a state
/// transition, and a loop that flips `feedingState` without moving anything
/// would satisfy it; what is asserted instead is the reserve that left the
/// substrate, the energy that came back, or an angle. TASK-005 exists because
/// `InternalState.feed()` had no caller at all — a state field with no consumer
/// — so these tests are about the consumer, not the field.
final class FeedingLoopTests: XCTestCase {

    // MARK: - fixtures

    /// A world whose taste field and edible reserve are under the test's
    /// control, so the loop is checked against a rule and not against a scene
    /// the test also wrote.
    private final class TasteWorld: WorldProvider {
        var acceptance: Float = 0
        var reserveLeft: Float = 0
        var lastIngestAmount: Float = 0
        var ingestCalls = 0
        func luminance(atX x: Float, y: Float, z: Float) -> Float { 0 }
        func odorConcentration(atX x: Float, y: Float, z: Float) -> (left: Float, right: Float) {
            (0, 0)
        }
        func temperature(atX x: Float, y: Float, z: Float) -> Float { 22 }
        func tasteAcceptance(atX x: Float, y: Float, z: Float) -> Float { acceptance }
        var isConsumable: Bool { true }
        func ingest(atX x: Float, y: Float, z: Float, amount: Float) -> Float {
            ingestCalls += 1
            let taken = min(amount, reserveLeft)
            reserveLeft -= taken
            lastIngestAmount = taken
            return taken
        }
    }

    private func demoAssetURL() throws -> URL {
        var bundles: [Bundle] = []
        #if SWIFT_PACKAGE
        bundles.append(Bundle.module)
        #else
        bundles.append(Bundle(for: FeedingLoopTests.self))
        bundles.append(Bundle(for: Connectome.self))
        #endif
        for bundle in bundles {
            for sub in [nil, "Resources"] {
                if let url = bundle.url(forResource: "demo_micro", withExtension: "fbpack",
                                        subdirectory: sub) {
                    return url
                }
            }
        }
        XCTFail("demo_micro.fbpack is not bundled")
        throw XCTSkip("asset not bundled")
    }

    private func makeCore() throws -> SimulationCore {
        let c = try Connectome.loadFBPack(from: try demoAssetURL())
        return SimulationCore(connectome: c)
    }

    /// Put the proboscis at full extension without going through the motor loop:
    /// holding the joint is the only way to test the contact predicate on a
    /// value that is not immediately overwritten.
    ///
    /// NOTE: this is a HOLD, not a proof. It deliberately bypasses the
    /// connectome so the ingestion rule can be tested in isolation. Whether the
    /// fly can get its mouth open BY ITSELF is a different question and is asked
    /// in `testTheMouthOpensItselfThroughTheConnectome` below — TASK-005's
    /// warning was that a joint the test forces open proves nothing about the
    /// loop closing.
    private func forceProboscisOpen(_ core: SimulationCore) {
        core.setProboscisAngleForTesting(FlyBody.proboscisMaxAngle)
    }

    // MARK: - the geometry the whole loop rests on

    /// The extended labellum must be able to reach the substrate. This is the
    /// assertion whose ABSENCE let the loop be unreachable while every constant
    /// looked reasonable: the head segment sits at y = 0 and the fly stands at
    /// `standHeightMm`, so the tip's height is entirely the proboscis's descent.
    ///
    /// Before this was fixed the extension fraction divided by a literal 1.4, so
    /// "full drive" extended only 57% and the tip hung 0.431 mm up against a
    /// 0.25 mm contact reach. No joint angle touched food: labellar taste and
    /// ingestion were dead for every fly.
    func testExtendedLabellumActuallyReachesTheSubstrate() {
        let stand = BodyDynamics().parameters.standHeightMm
        let h = FlyBody.labellumHeightAboveGroundMm(angle: FlyBody.proboscisMaxAngle,
                                                    standHeightMm: stand)
        XCTAssertLessThanOrEqual(
            h, OdorSource.contactReach,
            "at full extension the labellum must come within contact reach "
            + "(\(OdorSource.contactReach) mm) of the ground; it is \(h) mm up, "
            + "so no fly can ever put its mouth on food")
        XCTAssertGreaterThan(h, 0,
                             "the labellum must reach DOWN to the substrate, "
                             + "not through it (negative = buried)")
    }

    /// A folded proboscis is NOT in contact, and that must be true at the angle
    /// the motor actually produces when idle — not just at zero.
    func testFoldedProboscisIsNotInContact() {
        XCTAssertFalse(FlyBody.proboscisReaches(0))
        XCTAssertFalse(FlyBody.proboscisReaches(FlyBody.proboscisReachAngle - 0.01))
        XCTAssertTrue(FlyBody.proboscisReaches(FlyBody.proboscisMaxAngle))
    }

    // MARK: - the consumer TASK-005 was about

    func testHungryFlyIngestsMatterAndRegainsEnergy() throws {
        let core = try makeCore()
        let world = TasteWorld()
        // The starvation phase must actually be starvation. `TasteWorld`'s
        // acceptance is position-independent, so leaving it at 1 while the fly
        // "starves" put food under its feet for the whole phase: measured, the
        // fly ate for 357 of the 400 steps and finished at 0.871 — ABOVE the
        // 0.8 it started from. The fixture was feeding the animal in the one
        // phase that exists to make it hungry, and the assertion below was
        // reading the meal it had just served.
        world.acceptance = 0
        world.reserveLeft = 10
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))

        // Starve first, so "energy went up" cannot be satisfied by the fly
        // starting full. Compared against this core's OWN starting reserve
        // rather than a hard-coded 0.8: the metabolic rate is a calibrated
        // constant and a fixture pinned to the default would drift out of
        // agreement with it silently.
        let energyStart = core.internalState.energy
        for _ in 0..<400 { core.step() }
        let energyBefore = core.internalState.energy
        XCTAssertLessThan(energyBefore, energyStart,
                          "the fly should have burned some reserve")
        XCTAssertEqual(core.ingestedReserveForTesting, 0, accuracy: 1e-6,
                       "nothing edible was present yet — the fly cannot have eaten "
                       + "during the phase that makes it hungry")

        world.acceptance = 1                 // now the food is actually there
        forceProboscisOpen(core)
        for _ in 0..<60 {
            core.step()
            forceProboscisOpen(core)   // the motor loop rewrites the joint each step
        }

        XCTAssertGreaterThan(core.ingestedReserveForTesting, 0,
                             "a fly with its mouth on phagostimulant must SWALLOW "
                             + "something — this is the consumer InternalState.feed() never had")
        XCTAssertGreaterThan(world.lastIngestAmount, 0,
                             "the matter must come out of the world, not appear from a flag")
        XCTAssertGreaterThan(core.internalState.energy, energyBefore,
                             "eating must restore energy")
    }

    /// Nothing edible under the mouth means nothing gained. Without this the
    /// test above would pass on a fly that simply has energy injected.
    func testNoIngestionWithoutEdibleReserve() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 1        // it TASTES food...
        world.reserveLeft = 0       // ...but the patch is already eaten bare
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        for _ in 0..<200 { core.step() }

        forceProboscisOpen(core)
        for _ in 0..<60 {
            core.step()
            forceProboscisOpen(core)
        }
        XCTAssertEqual(core.ingestedReserveForTesting, 0, accuracy: 1e-6,
                       "an exhausted source must yield nothing — otherwise a meal "
                       + "is a constant and the loop conserves nothing")
    }

    /// An AVERSIVE substance is tasted but never swallowed.
    func testAversiveSubstanceIsNotSwallowed() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = -1
        world.reserveLeft = 10
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        for _ in 0..<200 { core.step() }
        forceProboscisOpen(core)
        for _ in 0..<60 {
            core.step()
            forceProboscisOpen(core)
        }
        XCTAssertEqual(core.ingestedReserveForTesting, 0, accuracy: 1e-6,
                       "bitter food is tasted and rejected, not eaten")
    }

    // MARK: - hunger modulation (the citation that changed the design)

    /// Starvation must make sugar MORE appetitive and bitter LESS aversive.
    ///
    /// This is the one test that would have caught the first design. A single
    /// gain on a signed acceptance scales both halves together, so a starving
    /// fly becomes MORE repelled by bitter food — backwards on the nutrient
    /// state where it most needs calories. Inagaki, Panse & Anderson, *Neuron*
    /// 2014 (PMID 25451195) show starvation raises sugar sensitivity and lowers
    /// bitter sensitivity, recruited at increasing hunger levels.
    func testHungerRaisesSweetAndLowersBitterSensitivity() {
        var fed = InternalState()
        fed.setEnergyForTesting(1.0)
        var starved = InternalState()
        starved.setEnergyForTesting(0.0)

        XCTAssertLessThan(fed.hungerDrive, starved.hungerDrive,
                          "the fixture must actually differ in hunger")

        // Appetitive side: rises with hunger.
        XCTAssertGreaterThan(starved.gustatoryAppetitiveGain, fed.gustatoryAppetitiveGain,
                             "starvation must make sugar MORE appetitive")

        // Aversive side: falls with hunger, and never to nothing.
        XCTAssertLessThan(starved.gustatoryAversiveGain, fed.gustatoryAversiveGain,
                          "starvation must make bitter LESS aversive")
        XCTAssertGreaterThan(starved.gustatoryAversiveGain, 0,
                             "a starving fly is less deterred by bitter, not blind to it")
    }

    /// The ordering from the same paper: the appetitive change comes first, the
    /// aversive blunting only once the animal is properly hungry. Losing your
    /// aversion to toxins is the riskier change and must not come first.
    func testAversiveBluntingBeginsAfterAppetitiveRise() {
        var fed = InternalState()
        fed.setEnergyForTesting(1.0)
        var mid = InternalState()
        // "Mildly hungry" cannot be an arbitrary number. This fixture used
        // 0.75, which is ABOVE the model's own `hungerEnergyThreshold` (1/1.4
        // = 0.714): `hungerDrive` is exactly 0 there, so "mild hunger" was
        // byte-identical to "fed" and the test compared a state with itself.
        // It failed as soon as the gains became reachable. 0.6 is below the
        // threshold (hunger exists) and above the hunger level at which bitter
        // blunting starts (0.4), which is the regime the ordering is about.
        mid.setEnergyForTesting(0.6)       // mild hunger
        var starved = InternalState()
        starved.setEnergyForTesting(0.0)

        XCTAssertGreaterThan(mid.hungerDrive, 0,
                             "the 'mild hunger' fixture must actually be hungry; "
                             + "a state with no hunger cannot order the two responses")
        XCTAssertGreaterThan(mid.gustatoryAppetitiveGain, fed.gustatoryAppetitiveGain,
                             "mild hunger should already sharpen sweet sensitivity")
        XCTAssertEqual(mid.gustatoryAversiveGain, fed.gustatoryAversiveGain, accuracy: 1e-6,
                       "mild hunger must NOT yet blunt bitter sensitivity")
        XCTAssertLessThan(starved.gustatoryAversiveGain, mid.gustatoryAversiveGain,
                          "only strong hunger blunts bitter sensitivity")
    }

    /// The sign of a taste sample must survive modulation, at every hunger
    /// level, on both sides — a modulated sample that crossed zero would turn a
    /// deterrent into an attractant.
    func testHungerModulationNeverFlipsTheSignOfTaste() throws {
        let core = try makeCore()
        for energy in [Float(0), 0.3, 0.7, 1.0] {
            core.setEnergyForTesting(energy)
            for acceptance in [Float(-1), -0.5, 0.5, 1] {
                let v = core.modulatedTasteForTesting(acceptance)
                XCTAssertEqual(v == 0, acceptance == 0,
                               "zero acceptance must stay zero")
                if acceptance != 0 {
                    XCTAssertEqual(v > 0, acceptance > 0,
                                   "acceptance \(acceptance) at energy \(energy) "
                                   + "flipped sign to \(v)")
                }
                XCTAssertLessThanOrEqual(abs(v), 1, "modulated taste must stay bounded")
            }
        }
    }

    // MARK: - the loop closes on a session timescale

    /// A fly must be able to go from hungry to fed inside a session, or hunger
    /// is a number nobody can ever observe.
    ///
    /// It could not before: metabolism ran on the neural clock crossed with the
    /// app's ~42x slow motion, so fed-to-hungry took ~40 wall-clock HOURS
    /// (`tools/measure_metabolism_balance.py`). `hungerDrive` was correct and
    /// invisible.
    func testMetabolismRunsOnASessionTimescale() {
        var s = InternalState()
        s.setEnergyForTesting(1.0)
        // The neural ms a frame actually advances (4 substeps of 0.1 ms).
        let neuralMsPerFrame = 0.4
        let framesPerSecond = 60.0
        var frames = 0
        while s.energy > InternalState.hungerEnergyThreshold && frames < 2_000_000 {
            s.advance(dtMs: neuralMsPerFrame, activityLevel: 0.5)
            frames += 1
        }
        let wallSeconds = Double(frames) / framesPerSecond
        XCTAssertLessThan(s.energy, InternalState.hungerEnergyThreshold,
                          "the fly must actually get hungry")
        XCTAssertLessThan(wallSeconds, 600,
                          "fed-to-hungry must fit in a session; it takes "
                          + "\(wallSeconds / 60) wall-clock minutes")
        XCTAssertGreaterThan(wallSeconds, 1,
                             "metabolism must not be instantaneous — a fly that "
                             + "starves in under a second cannot forage")
    }

    /// Eating must reduce hunger — the satiety half. Without it, feeding only
    /// ever adds energy and `hungerDrive` is monotone.
    func testEatingReducesHunger() {
        var s = InternalState()
        s.setEnergyForTesting(0.0)
        let before = s.hungerDrive
        s.feed(amount: 0.5)
        XCTAssertLessThan(s.hungerDrive, before, "a meal must reduce hunger")
        XCTAssertGreaterThan(s.energy, 0, "a meal must add energy")
    }

    // MARK: - the loop is not a circle (TASK-005's own warning)

    /// THE question TASK-005 says to ask before counting a loop closed: can the
    /// mouth open ITSELF, or does the test have to hold the joint?
    ///
    /// Every test above calls `forceProboscisOpen`. That is fair for checking
    /// the ingestion rule, and it is exactly wrong for checking whether the loop
    /// closes, because a joint the test holds open cannot tell us whether taste
    /// can open it. This test never touches the joint: it drives the animal with
    /// a taste field and reads the joint the MOTOR LOOP produced.
    ///
    /// It failed when first written, and the reason was not the loop — the
    /// SHIPPED demo asset's synapse weights were ~100x too small for the engine
    /// (`synapseCount` 1-3, efficacy 0.1-0.6 -> 0.1-1.8 nA per event, against a
    /// threshold that needs ~100 nA for one spike). MEASURED: the largest summed
    /// excitatory current into any neuron was 8.664 nA -> 0.866 mV of excursion
    /// against a 10 mV rest->threshold gap, so NO neuron could be driven to
    /// threshold by synapses at all. Injecting a cell made that single cell fire
    /// and nothing else, ever: the shipped connectome was decorative and the app
    /// only looked alive because the gait has a drift timeout that steps the legs
    /// with no neural drive.
    func testTheMouthOpensItselfThroughTheConnectome() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 1
        world.reserveLeft = 10
        core.world = world
        // Stand the fly ON the patch so its tarsal sampler reports the food.
        // y must be the STANDING height, not 0: `isGrounded` is
        // `position.y <= groundY + standHeightMm`, and a fly at y=0 is buried.
        let stand = BodyDynamics().parameters.standHeightMm
        core.setPose(position: SIMD3(0.5, stand, 0),
                     forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setEnergyForTesting(0.0)          // hungry: appetitive gain is at its floor

        let folded = core.body.proboscis.angle
        XCTAssertLessThan(folded, FlyBody.proboscisReachAngle,
                          "the fixture must start with the mouth SHUT, or the "
                          + "test cannot show it opening")

        var peak = folded
        for _ in 0..<400 {
            core.step()                         // no forceProboscisOpen
            peak = max(peak, core.body.proboscis.angle)
        }

        XCTAssertGreaterThan(peak, folded,
                             "taste must raise the proboscis drive off its resting "
                             + "angle — the connectome has to be able to start the loop")
        XCTAssertTrue(FlyBody.proboscisReaches(peak),
                      "the mouth must open far enough to reach food on its own "
                      + "(peak angle \(peak), reach threshold \(FlyBody.proboscisReachAngle)). "
                      + "If this fails the feeding loop is a circle: the only taste "
                      + "sample that drives the SEZ is gated on a mouth the test opened.")
    }

    /// The other half of the same question: the connectome must not be a
    /// firecracker. A network that propagates its input AND stays quiet with no
    /// input is doing something; one that propagates because it is unstable is
    /// not. Measured on the shipped asset: 0 spikes at rest.
    ///
    /// This passes for a reason that is worth naming, because it used to pass
    /// for TWO of them stacked. The `TasteWorld` has no taste, but the tarsal
    /// channel does not read taste — it reads contact load, and the fly used to
    /// be spawned 0.78 mm INSIDE the floor, which the contact spring answered
    /// with a 42-body-weight launch. That transient drove the network for 67
    /// steps. The spawn is now a force balance (`restHeightMm`), so the fixture
    /// is a pose and the silence is the network's.
    func testTheConnectomeIsSilentWithNoStimulus() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 0                    // no food, no taste
        world.reserveLeft = 5
        core.world = world
        core.setPose(position: SIMD3(-40, 0, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))

        // The spawn itself must be a pose: no launch load, which is the defect
        // that produced the spike this assertion was reading.
        var peakLoad: Float = 0
        for _ in 0..<600 {
            core.step()
            peakLoad = max(peakLoad, core.dynamics.groundLoadFraction)
        }
        XCTAssertLessThan(peakLoad, 1.5,
                          "the spawn launched the body \(peakLoad) body weights "
                          + "into the substrate; the 'spike' below would then be "
                          + "the fixture, not the network")

        XCTAssertEqual(core.engine.spikeCount, 0,
                       "with no sensory drive the network must be silent; "
                       + "spontaneous activity would mean the propagation above "
                       + "is instability, not signal")
    }
}