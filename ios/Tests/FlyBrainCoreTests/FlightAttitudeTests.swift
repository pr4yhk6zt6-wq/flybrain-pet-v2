import XCTest
@testable import FlyBrainCore

/// Flight attitude (TASK-008): the wing asymmetry `BodyDynamics` reads has a
/// PRODUCER, and an unequal stroke is what makes the fly roll or yaw.
///
/// The defect this covers: `BodyDynamics` builds `rollTorque = (fLeft - fRight)
/// * arm` from `command.wingAsymmetry`, and that value came from
/// `MotorSystem.out.leftRightAsymmetry` — which was read there and written
/// NOWHERE (0 assignments, 2 mentions). The wings could beat, but the fly could
/// never bank or turn: the whole attitude axis was structurally dead, the same
/// "declared with no producer" shape as TASK-006 and TASK-007.
///
/// These tests assert on the CONSUMER side — the asymmetry that actually
/// reaches the body command, and the moment the body derives from it — because
/// a field that exists and is never written passes any test that only reads it.
final class FlightAttitudeTests: XCTestCase {

    // MARK: - the producer

    /// Equal wing drive must produce no asymmetry, and unequal drive must
    /// produce one with the right sign. This is the function the readout uses.
    func testAsymmetryIsZeroWhenBothWingsAreEqual() throws {
        let core = try makeCore()
        XCTAssertEqual(core.wingAsymmetryForTesting(left: 50, right: 50), 0,
                       accuracy: 1e-6,
                       "a fly whose wings beat equally does not roll")
    }

    /// A louder LEFT wing reads POSITIVE, and `BodyDynamics` gives that wing the
    /// faster stroke (`vLeft = tip * (1 + 0.5·asym)`), so positive asymmetry
    /// means "the left wing is producing more force". Getting this backwards
    /// would roll the fly AWAY from a perturbation — destabilising, which is
    /// worse than no loop at all, so the sign is pinned here and mirrored in
    /// `tools/mirror_flight_attitude.py`.
    func testAsymmetrySignFollowsWhicheverWingIsStronger() throws {
        let core = try makeCore()
        XCTAssertGreaterThan(core.wingAsymmetryForTesting(left: 80, right: 20), 0,
                             "a louder left wing must read positive")
        XCTAssertLessThan(core.wingAsymmetryForTesting(left: 20, right: 80), 0,
                          "a louder right wing must read negative")
    }

    /// Silence is not a turn: with no wing drive at all there is no asymmetry to
    /// publish, and a readout that invented one would move a stationary fly.
    func testSilentWingsProduceNoAsymmetry() throws {
        let core = try makeCore()
        XCTAssertEqual(core.wingAsymmetryForTesting(left: 0, right: 0), 0, accuracy: 1e-6)
    }

    /// The asymmetry must saturate rather than run away: the body's torque is
    /// built from it, and an unbounded value would let one loud frame flip the
    /// animal regardless of how the wings are actually moving.
    func testAsymmetryIsBoundedToOne() throws {
        let core = try makeCore()
        XCTAssertLessThanOrEqual(core.wingAsymmetryForTesting(left: 1000, right: 0), 1)
        XCTAssertGreaterThanOrEqual(core.wingAsymmetryForTesting(left: 0, right: 1000), -1)
    }

    // MARK: - the consumer (what BodyDynamics does with it)

    /// THE test the defect needed: a fly whose wings are driven unequally must
    /// end up rotating. Before TASK-008 this could not happen on any asset,
    /// because the asymmetry was a constant 0 and `rollTorque` was therefore
    /// always 0 — a body with lift but no attitude authority.
    func testUnequalWingDriveRotatesTheBody() throws {
        var unequal = BodyDynamics()
        var cmd = BodyMotorCommand()
        cmd.wingStrokeFrequency = 180
        cmd.wingStrokeAmplitude = 0.9
        cmd.wingAsymmetry = 1.0
        for _ in 0..<200 { unequal.step(command: cmd, dt: 0.0004) }

        var level = BodyDynamics()
        var levelCmd = cmd
        levelCmd.wingAsymmetry = 0
        for _ in 0..<200 { level.step(command: levelCmd, dt: 0.0004) }

        XCTAssertNotEqual(unequal.yawRateDegPerS, level.yawRateDegPerS, accuracy: 1e-6,
                          "an unequal stroke must produce a rotation the level "
                          + "stroke does not — this is the moment the asymmetry "
                          + "was never able to generate")
        XCTAssertGreaterThan(abs(unequal.angularRateMagnitude),
                             abs(level.angularRateMagnitude),
                             "the asymmetry must ADD rotation, not merely fail "
                             + "to remove it")
    }

    /// And the two directions must be OPPOSITE, not merely different: the sign
    /// is the whole content of a correction.
    func testOppositeAsymmetriesRotateOppositeWays() throws {
        func yawRate(asymmetry a: Float) -> Float {
            var d = BodyDynamics()
            var cmd = BodyMotorCommand()
            cmd.wingStrokeFrequency = 180
            cmd.wingStrokeAmplitude = 0.9
            cmd.wingAsymmetry = a
            for _ in 0..<200 { d.step(command: cmd, dt: 0.0004) }
            return d.yawRateDegPerS
        }
        let left = yawRate(asymmetry: 1.0)
        let right = yawRate(asymmetry: -1.0)
        XCTAssertNotEqual(left, 0, "a fully one-sided stroke must turn the fly")
        XCTAssertLessThan(left * right, 0,
                          "the two directions must be opposite — a correction "
                          + "that turns the same way both times corrects nothing")
    }

    // MARK: - the loop is not a circle (the TASK-005/TASK-006 lesson)

    /// The attitude loop must have a live INPUT, not only a writer. The producer
    /// is worthless if nothing can move it, so this checks the haltere channel
    /// the loop injects into actually exists on the shipped assets — the same
    /// "channel with no producer, or producer with no channel" class this
    /// project keeps finding.
    ///
    /// The measured behaviour lives in `tools/probe_haltere_wing_asymmetry.py`
    /// (it needs the engine, which only runs in the Python mirror). What is
    /// asserted here is the part Swift can see: the haltere channel resolves to
    /// a real cell, so the drive the probe applies is a drive the app can apply.
    func testHaltereChannelHasALiveTargetOnTheShippedAsset() throws {
        let core = try makeCore()
        let target = core.sensory.haltereTargetForTesting()
        XCTAssertNotNil(target,
                        "the haltere channel must resolve to a cell on the asset "
                        + "the app ships, or the attitude loop's only input is "
                        + "unreachable and the asymmetry can never move")
        if let t = target {
            let n = core.connectome.neurons[Int(t)]
            XCTAssertEqual(n.region, RegionID.haltereNeuropil.rawValue,
                           "the haltere afferent must land in the haltere neuropil")
            XCTAssertFalse(n.isMotorNeuron,
                           "injecting into a motor cell would drive the readout "
                           + "directly and bypass the connectome")
        }
    }

    // MARK: - fixtures

    private func demoAssetURL() throws -> URL {
        var bundles: [Bundle] = []
        #if SWIFT_PACKAGE
        bundles.append(Bundle.module)
        #else
        bundles.append(Bundle(for: FlightAttitudeTests.self))
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
}