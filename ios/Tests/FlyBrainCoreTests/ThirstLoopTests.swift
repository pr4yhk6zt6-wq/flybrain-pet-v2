import XCTest
@testable import FlyBrainCore

/// The thirst loop (TASK-006): thirst has a CONSUMER, water is a SENSE, and a
/// folded fly can still start drinking.
///
/// The counterpart to `FeedingLoopTests`, and it exists for the same reason:
/// `InternalState.drink()` had NO caller anywhere in the app or the pipeline and
/// `thirstDrive` was computed for nobody. A field with no consumer is a
/// decoration, so every test here asserts on the CONSUMER side — the hydration
/// that actually rose, the water sample that actually reached the brain, or the
/// surface the drink came from.
///
/// The one test that matters most is `testAFoldedFlyCanSenseAndDrinkWater`:
/// TASK-005's lesson was that a loop can be wired and still be circular, and the
/// only way to catch that is to check the mouth can open ITSELF. For water the
/// same question is answered by the tarsi.
final class ThirstLoopTests: XCTestCase {

    // MARK: - fixtures

    /// A world that supplies water (and nothing else) under the test's control,
    /// so the loop is checked against a rule rather than a scene the test wrote.
    ///
    /// `feedAcceptance` is deliberately present and non-zero-capable: several
    /// tests need to prove that drinking does NOT come from food, so the fixture
    /// must be able to present food and water in the same place.
    private final class WaterWorld: WorldProvider {
        var water: Float = 0            // water contact at every point
        var acceptance: Float = 0       // phagostimulant taste at every point
        var reserveLeft: Float = 0      // edible matter available
        var drinkCalls = 0
        var lastDrinkRequest: Float = 0
        func luminance(atX x: Float, y: Float, z: Float) -> Float { 0 }
        func odorConcentration(atX x: Float, y: Float, z: Float) -> (left: Float, right: Float) {
            (0, 0)
        }
        func temperature(atX x: Float, y: Float, z: Float) -> Float { 22 }
        func tasteAcceptance(atX x: Float, y: Float, z: Float) -> Float { acceptance }
        func waterContact(atX x: Float, y: Float, z: Float) -> Float { water }
        var isConsumable: Bool { true }
        func ingest(atX x: Float, y: Float, z: Float, amount: Float) -> Float {
            let taken = min(amount, reserveLeft)
            reserveLeft -= taken
            return taken
        }
        func drinkWater(atX x: Float, y: Float, z: Float, amount: Float) -> Float {
            drinkCalls += 1
            lastDrinkRequest = amount
            guard water > 0 else { return 0 }
            return amount * water     // standing water: not consumed to exhaustion
        }
    }

    private func demoAssetURL() throws -> URL {
        var bundles: [Bundle] = []
        #if SWIFT_PACKAGE
        bundles.append(Bundle.module)
        #else
        bundles.append(Bundle(for: ThirstLoopTests.self))
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

    // MARK: - the consumer TASK-006 was about

    /// A thirsty fly standing on water must actually take water up, and the
    /// hydration must come from the amount the world gave — not from a flag.
    func testThirstyFlyDrinksAndRegainsHydration() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 0                    // dry first, so the gain cannot be pre-existing
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.2)

        for _ in 0..<60 { core.step() }
        XCTAssertEqual(core.drunkWaterForTesting, 0, accuracy: 1e-6,
                       "nothing drinkable was present yet — the fly cannot have "
                       + "drunk during the dry phase")

        let hydrationBefore = core.internalState.hydration
        world.water = 1                    // now the water is actually there
        for _ in 0..<60 { core.step() }

        XCTAssertGreaterThan(core.drunkWaterForTesting, 0,
                             "a fly standing on water must DRINK — this is the "
                             + "consumer InternalState.drink() never had")
        XCTAssertGreaterThan(world.drinkCalls, 0,
                             "the water must come out of the world, not from a flag")
        XCTAssertGreaterThan(core.internalState.hydration, hydrationBefore,
                             "drinking must restore hydration")
    }

    /// Dry world, thirsty fly: nothing gained. Without this the test above would
    /// pass on a fly that simply has hydration injected.
    func testNoDrinkingWithoutWaterInReach() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 0
        world.acceptance = 1        // food is here, but NO water
        world.reserveLeft = 10
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.1)

        for _ in 0..<200 { core.step() }

        XCTAssertEqual(core.drunkWaterForTesting, 0, accuracy: 1e-6,
                       "a fly on food with no water must gain NO hydration — "
                       + "otherwise the water channel is reading the food patch")
        XCTAssertFalse(core.waterSampledForTesting,
                       "no water in reach means the water sense must not fire")
    }

    /// Water must not feed the energy pool: a fly that could live on water is
    /// not modelling two different needs.
    func testDrinkingDoesNotRestoreEnergy() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 1
        world.acceptance = 0
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.1)
        core.setEnergyForTesting(0.5)

        let energyBefore = core.internalState.energy
        for _ in 0..<120 { core.step() }

        XCTAssertGreaterThan(core.drunkWaterForTesting, 0, "the fly did drink")
        XCTAssertLessThanOrEqual(core.internalState.energy, energyBefore,
                                 "water must not add energy — eating and drinking "
                                 + "are different needs with different consequences")
    }

    // MARK: - the loop is not a circle (TASK-005's warning, applied to water)

    /// A FOLDED fly must be able to sense water and start drinking. This is the
    /// question TASK-005 says to ask before counting a loop closed.
    ///
    /// If water were sampled only at the labellum, the channel that has to open
    /// the mouth would require the mouth to be already open — the exact circular
    /// wiring that kept the original gustatory channel permanently silent. The
    /// tarsal water neurons are what break it, and this test never touches the
    /// proboscis joint.
    func testAFoldedFlyCanSenseAndDrinkWater() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 1
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.1)
        // The proboscis is left exactly as the simulation produced it. The test
        // must not open it — that is the whole point.
        for _ in 0..<120 { core.step() }

        XCTAssertTrue(core.waterSampledForTesting,
                      "a folded fly standing on water must still SENSE it — the "
                      + "tarsal route is the only water sense that does not "
                      + "require the mouth the loop is trying to open")
        XCTAssertGreaterThan(core.drunkWaterForTesting, 0,
                             "and it must be able to DRINK from the tarsi")
        // The source is read from the JOINT, not assumed: if the loop managed to
        // open the mouth by itself the labellum route is correct and takes
        // precedence. What must never happen is a LABELLAR reading while the
        // proboscis is folded — that would mean the test (or the code) opened
        // the mouth for the fly.
        let reached = FlyBody.proboscisReaches(core.body.proboscis.angle)
        if reached {
            XCTAssertEqual(core.waterSourceForTesting, "labellum",
                           "the loop opened the mouth itself; drinking is then "
                           + "labellar, which is the correct precedence")
        } else {
            XCTAssertEqual(core.waterSourceForTesting, "tarsus",
                           "with the proboscis folded the drink can only come "
                           + "from the tarsi")
        }
    }

    /// The mouth route must still exist and take precedence when it IS open —
    /// otherwise the tarsal bootstrap would have replaced labellar drinking
    /// rather than supplemented it.
    func testAnOpenProboscisDrinksWithTheLabellum() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 1
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.1)

        for _ in 0..<60 {
            core.step()
            core.setProboscisAngleForTesting(FlyBody.proboscisMaxAngle)
        }
        XCTAssertEqual(core.waterSourceForTesting, "labellum",
                       "a fly with its mouth out drinks with the labellum")
    }

    // MARK: - thirst is a DRIVE, not a flag

    /// Thirstier flies must push a stronger water signal through the SAME
    /// contact. Without this, `thirstDrive` could be read by nothing and every
    /// other test here would still pass.
    func testThirstRaisesTheWaterSignalFromTheSameContact() {
        var parched = InternalState()
        parched.setHydrationForTesting(0.0)
        var satiated = InternalState()
        satiated.setHydrationForTesting(1.0)

        let p = parched.thirstModulatedWater(0.5)
        let s = satiated.thirstModulatedWater(0.5)
        XCTAssertGreaterThan(p, s,
                             "a thirstier fly must respond more strongly to the "
                             + "same water — this is the consumer thirstDrive lacked")
        XCTAssertGreaterThan(s, 0,
                             "a fully hydrated fly must still SENSE water: a gain "
                             + "that reaches zero makes taste a thirst alarm "
                             + "rather than a sense")
        XCTAssertEqual(parched.thirstModulatedWater(0), 0,
                       "no contact means no current, however thirsty the fly is")
    }

    /// Thirst must be reachable inside a session — the trap TASK-005 hit was a
    /// drive that could not be reached in any run.
    func testThirstIsReachableOnASessionTimescale() {
        var s = InternalState()
        s.setHydrationForTesting(1.0)
        let neuralMsPerFrame = 0.4
        let framesPerSecond = 60.0
        var frames = 0
        while s.hydration > 1 / 1.4 && frames < 2_000_000 {
            s.advance(dtMs: neuralMsPerFrame, activityLevel: 0.5)
            frames += 1
        }
        let wallMinutes = Double(frames) / framesPerSecond / 60
        XCTAssertLessThan(s.hydration, 1 / 1.4, "the fly must actually get thirsty")
        XCTAssertLessThan(wallMinutes, 60,
                          "well-hydrated to thirsty must fit in a session; it "
                          + "takes \(wallMinutes) wall-clock minutes")
        XCTAssertGreaterThan(wallMinutes, 0.05,
                             "thirst must not be instantaneous — a fly that dries "
                             + "out in a frame cannot seek water")
    }

    /// Drinking must reduce thirst — the satiety half. Without it hydration only
    /// ever rises and `thirstDrive` is monotone.
    func testDrinkingReducesThirst() {
        var s = InternalState()
        s.setHydrationForTesting(0.1)
        let before = s.thirstDrive
        let gained = s.drink(amount: 0.4)
        XCTAssertGreaterThan(gained, 0, "a drink must actually add hydration")
        XCTAssertLessThan(s.thirstDrive, before, "drinking must reduce thirst")
    }

    /// The bound: a drink cannot bank hydration the animal does not need. An
    /// unbounded drink pins hydration at 1 and the drive at 0 forever, which is
    /// the same defect seen from the other side.
    func testDrinkingIsBoundedByTheDeficit() {
        var full = InternalState()
        full.setHydrationForTesting(1.0)
        XCTAssertEqual(full.drink(amount: 10), 0, accuracy: 1e-6,
                       "a fully hydrated fly must gain nothing from more water")

        var nearlyFull = InternalState()
        nearlyFull.setHydrationForTesting(0.95)
        var parched = InternalState()
        parched.setHydrationForTesting(0.0)
        XCTAssertLessThan(nearlyFull.drink(amount: 0.5), parched.drink(amount: 0.5),
                          "the uptake must be bounded by the deficit, not by a "
                          + "flat clip at 1")
    }

    /// The loop carries a QUANTITY: longer on the water means more hydration.
    /// A per-contact constant would make drinking a boolean.
    func testDrinkingScalesWithTimeOnTheWater() throws {
        let core = try makeCore()
        let world = WaterWorld()
        world.water = 1
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        core.setHydrationForTesting(0.0)

        for _ in 0..<10 { core.step() }
        let shortDrink = core.drunkWaterForTesting
        for _ in 0..<40 { core.step() }
        XCTAssertGreaterThan(core.drunkWaterForTesting, shortDrink,
                             "staying on the water longer must hydrate more — the "
                             + "loop must carry a quantity, not a flag")
    }

    // MARK: - water is a SENSE, not food

    /// Water must carry no taste valence. This is the property that makes the
    /// two needs distinguishable at all, and it is the one a well-meaning
    /// "just make water taste good" change would break.
    func testWaterIsNotAPhagostimulant() {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .water, emissionRate: 1))
        XCTAssertEqual(w.tasteAcceptance(atX: 0, y: 0, z: 0), 0, accuracy: 1e-6,
                       "water must carry no taste valence")
        XCTAssertGreaterThan(w.waterContact(atX: 0, y: 0, z: 0), 0,
                             "but the water sense must find it")
    }

    /// And a food patch must not read as water — the other direction.
    func testFoodIsNotWater() {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .food, emissionRate: 1))
        XCTAssertGreaterThan(w.tasteAcceptance(atX: 0, y: 0, z: 0), 0,
                             "food is a phagostimulant")
        XCTAssertEqual(w.waterContact(atX: 0, y: 0, z: 0), 0, accuracy: 1e-6,
                       "food is not water — a fly that cannot tell them apart "
                       + "cannot balance two needs")
        XCTAssertEqual(w.drinkWater(atX: 0, y: 0, z: 0, amount: 1), 0, accuracy: 1e-6,
                       "you cannot drink food")
    }

    /// The water sense must be a CONTACT sense: out beyond the patch's own extent
    /// plus the contact reach, it must read zero.
    func testWaterSenseIsContactOnly() {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .water,
                                   emissionRate: 1, diffusionConstant: 3))
        XCTAssertGreaterThan(w.waterContact(atX: 0, y: 0, z: 0), 0, "at the source")
        // The patch has a RADIUS, so "outside contact reach" means beyond
        // radius + reach — measuring at a distance smaller than the radius
        // would still be inside the patch and the test would be asserting on
        // the wrong geometry.
        let outsideD = OdorSource.contactReach + 1.1 + 0.5
        XCTAssertEqual(w.waterContact(atX: outsideD, y: 0, z: 0), 0, accuracy: 1e-6,
                       "past the patch edge plus contact reach the water sense must "
                       + "report nothing — it is a contact sense, not a second "
                       + "odour channel")
        XCTAssertGreaterThan(w.odorConcentration(atX: outsideD, y: 0, z: 0).left, 0.01,
                             "while the plume must still carry, or 'contact-only' "
                             + "would be a claim about an empty world")
    }
}