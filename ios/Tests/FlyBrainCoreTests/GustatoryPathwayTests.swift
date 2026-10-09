//
//  GustatoryPathwayTests.swift
//  FlyBrainCoreTests
//
//  The gustatory channel had a complete-looking resolver and ZERO call sites:
//  `SensoryInterface.gustatoryInput` existed, was tested for its target, and
//  was never invoked by the simulation. A gate on the resolver cannot see
//  that — the resolver was fine. These tests exercise the CHANNEL.
//
//  The harder half is the bootstrap. The proboscis is driven by SEZ activity,
//  and before this change SEZ had exactly one possible input: labellar taste.
//  Gating labellar taste on the proboscis being open therefore made the
//  channel unable to ever open it — wired, reachable, and permanently silent.
//  Drosophila breaks that circle with tarsal sensilla (the fly tastes with its
//  feet, and a tarsal sugar taste extends the proboscis). These tests assert
//  the tarsal sample does not read the proboscis, which is what keeps the loop
//  startable.
//

import XCTest
@testable import FlyBrainCore

final class GustatoryPathwayTests: XCTestCase {

    private func demoAssetURL() throws -> URL {
        var bundles: [Bundle] = []
        #if SWIFT_PACKAGE
        bundles.append(Bundle.module)
        #else
        bundles.append(Bundle(for: GustatoryPathwayTests.self))
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

    /// A world whose taste field is under the test's control, so the sampling
    /// rule is checked on the rule and not on a scene the test also authored.
    private final class TasteWorld: WorldProvider {
        var acceptance: Float = 0
        func luminance(atX x: Float, y: Float, z: Float) -> Float { 0 }
        func odorConcentration(atX x: Float, y: Float, z: Float) -> (left: Float, right: Float) {
            (0, 0)
        }
        func temperature(atX x: Float, y: Float, z: Float) -> Float { 22 }
        func tasteAcceptance(atX x: Float, y: Float, z: Float) -> Float { acceptance }
    }

    private func makeCore() throws -> SimulationCore {
        let c = try Connectome.loadFBPack(from: try demoAssetURL())
        return SimulationCore(connectome: c)
    }

    // MARK: - the channel is driven at all

    func testTasteChannelIsSampledWhileStandingOnFood() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 1
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))

        // Let the fly settle so it is grounded (the tarsal sample needs stance).
        for _ in 0..<200 { core.step() }
        XCTAssertTrue(core.tasteSampledForTesting,
                      "a grounded fly on a phagostimulant must sample taste")
        XCTAssertGreaterThan(core.lastTasteAcceptanceForTesting, 0,
                             "food must read as a POSITIVE acceptance")
    }

    func testTasteIsNotSampledOnNothing() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 0
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        for _ in 0..<200 { core.step() }
        XCTAssertFalse(core.tasteSampledForTesting,
                       "nothing under the feet must produce no taste sample")
    }

    // MARK: - the bootstrap that the circle broke

    /// The tarsal sample must be reachable with the proboscis CLOSED. If this
    /// fails, the channel can never start and every other test here is
    /// decorative — the fly would have to already be feeding to be able to
    /// taste.
    func testTarsalTasteDoesNotRequireTheProboscisToBeOpen() throws {
        let core = try makeCore()
        let world = TasteWorld()
        world.acceptance = 1
        core.world = world
        core.setPose(position: SIMD3(0.5, 0.8, 0), forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        for _ in 0..<200 { core.step() }

        // No forced joint state: SEZ receives no current except from taste, so
        // on a fresh core the proboscis stays folded. The tarsal channel must
        // still sample in exactly that state — it is the only thing that can
        // start the loop. If this fails, every other test here is decorative:
        // the fly would have to already be feeding to be able to taste.
        XCTAssertFalse(core.proboscisReachedForTesting,
                       "the proboscis must still be folded on a fresh core "
                       + "(if it is not, this test is not testing the bootstrap)")
        XCTAssertTrue(core.tasteSampledForTesting,
                      "tarsal taste must fire with the proboscis closed — "
                      + "otherwise nothing can ever open it")
    }

    // MARK: - the world's taste field is a contact sense with a sign

    func testTasteFieldIsShorterRangeThanOdor() throws {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .food,
                                   emissionRate: 1, diffusionConstant: 4))
        // At 1 mm the plume still carries odor; the labellum cannot reach it.
        let odor = w.odorConcentration(atX: 1, y: 0, z: 0).left
        let taste = w.tasteAcceptance(atX: 1, y: 0, z: 0)
        XCTAssertGreaterThan(odor, 0.1, "the odor field must still carry at 1 mm")
        XCTAssertEqual(taste, 0, accuracy: 1e-6,
                       "taste must NOT reach 1 mm — it is a contact sense, not a "
                       + "second odor channel")
    }

    func testTasteSignComesFromTheSubstance() throws {
        let food = World()
        food.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .food, emissionRate: 1))
        XCTAssertGreaterThan(food.tasteAcceptance(atX: 0, y: 0, z: 0), 0)

        let bitter = World()
        bitter.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .aversive, emissionRate: 1))
        XCTAssertLessThan(bitter.tasteAcceptance(atX: 0, y: 0, z: 0), 0)

        // Water drives drinking through hydration, and pheromone drives
        // courtship. Neither is a feeding taste; an animal that cannot tell
        // sugar from water is not tasting.
        for kind in [OdorSource.Kind.water, .pheromone] {
            let w = World()
            w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: kind, emissionRate: 1))
            XCTAssertEqual(w.tasteAcceptance(atX: 0, y: 0, z: 0), 0, accuracy: 1e-6,
                           "\(kind) must carry no taste valence")
        }
    }

    /// The counterpart to the bootstrap test: fixing the circle must not be
    /// done by sampling taste everywhere. A closed proboscis cannot use the
    /// LABELLAR route, and that distinction is what makes the two sites two
    /// sensors rather than one.
    func testLabellarRouteRequiresAnOpenProboscis() throws {
        // Pure predicate on the joint state. The loop rewrites this joint every
        // step, so a test that sets the angle by hand and then steps is
        // asserting on a value the step overwrites.
        XCTAssertFalse(FlyBody.proboscisReaches(0),
                       "a fully folded proboscis cannot reach a substrate")
        XCTAssertFalse(FlyBody.proboscisReaches(FlyBody.proboscisReachAngle - 0.01),
                       "the gate must be exclusive just below the threshold")
        XCTAssertTrue(FlyBody.proboscisReaches(FlyBody.proboscisReachAngle),
                      "the threshold itself must pass — otherwise the declared "
                      + "value and the gate disagree")
        XCTAssertTrue(FlyBody.proboscisReaches(FlyBody.proboscisReachAngle + 0.1))

        // And a fully driven joint does open past the gate: the motor output is
        // `proboscisDrive * 0.8`, so a saturated drive is the reachable max.
        XCTAssertTrue(FlyBody.proboscisReaches(0.8),
                      "a saturated proboscis drive must open past the reach gate")
    }
}