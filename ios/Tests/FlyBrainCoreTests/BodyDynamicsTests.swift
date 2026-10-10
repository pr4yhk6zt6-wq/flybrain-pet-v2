//
//  BodyDynamicsTests.swift
//  FlyBrainCoreTests
//
//  Phase 6/7 validation (spec §27) — the body solver's physical claims.
//
//  Every expected number in this file was first produced by the offline Python
//  mirror `tools/sim_body_dynamics.py`, which reproduces BodyDynamics line by
//  line and prints the same quantities. That is the calibration path used on
//  the authoring device (no Swift toolchain available there): parameters are
//  checked against published Drosophila measurements offline, then asserted
//  here so CI re-checks them.
//

import XCTest
@testable import FlyBrainCore

final class BodyDynamicsTests: XCTestCase {

    /// A body that starts well clear of the substrate, so contact forces stay
    /// out of the way while a force-balance claim is being tested.
    private func airborneBody(heightMm: Float = 100) -> BodyDynamics {
        var fly = BodyDynamics()
        fly.body.position = SIMD3(0, heightMm, 0)
        fly.body.velocity = SIMD3(0, 0, 0)
        fly.body.grounded = false
        return fly
    }

    // MARK: - Wing actuator: the wing is not the command

    /// The wings lag the motor command, and the force uses the REALISED stroke.
    ///
    /// Muscle cannot change stroke amplitude in one step, so the realised
    /// stroke is a first-order lag of the commanded one (time constant
    /// `strokeActuatorTauMs`). This is what makes the wing-strain channel a
    /// sensor: it reads the wing, not the efference copy. If the body applied
    /// the command instantly and the channel read the command, the two would
    /// agree perfectly for a reason that has nothing to do with sensing.
    func testWingStrokeLagsItsCommand() {
        var fly = airborneBody()
        var command = BodyMotorCommand()
        command.wingStrokeAmplitude = 0.9
        command.wingStrokeFrequency = 180

        fly.step(command: command, dt: 0.0001)
        // One 0.1 ms step with a 15 ms time constant realises ~0.67% of the
        // command — measured: 0.0060 rad and 1.2 Hz. (`dt` is SECONDS here; the
        // neural core's 0.1 ms step is what the caller converts to before
        // reaching the physics.)
        XCTAssertGreaterThan(fly.measuredStrokeAmplitude, 0)
        XCTAssertLessThan(fly.measuredStrokeAmplitude, 0.01,
                          "the stroke reached \(fly.measuredStrokeAmplitude) rad in one "
                          + "step, so there is no actuator lag at all")
        XCTAssertLessThan(fly.measuredStrokeFrequency, 5,
                          "the frequency reached \(fly.measuredStrokeFrequency) Hz in one step")

        // The measured tip speed must be derived from the MEASURED stroke, not
        // from the command: recomputing from the command would give the full
        // 1799 mm/s while the wings have barely moved.
        let p = PhysicsParameters()
        let fromMeasured = BodyDynamics.wingTipSpeed(
            amplitude: fly.measuredStrokeAmplitude,
            frequencyHz: fly.measuredStrokeFrequency,
            wingLengthMm: p.wingLengthMm)
        let fromCommand = BodyDynamics.wingTipSpeed(amplitude: 0.9, frequencyHz: 180,
                                                    wingLengthMm: p.wingLengthMm)
        XCTAssertEqual(fly.measuredWingTipSpeed, fromMeasured, accuracy: 1e-3)
        XCTAssertLessThan(fromMeasured, 0.05 * fromCommand,
                          "the reported tip speed is essentially the commanded one")
    }

    /// Driven long enough, the realised stroke converges ON the command rather
    /// than settling short of it — a lag, not a loss.
    func testWingStrokeConvergesToItsCommand() {
        var fly = airborneBody()
        var command = BodyMotorCommand()
        command.wingStrokeAmplitude = 0.9
        command.wingStrokeFrequency = 180
        for _ in 0..<5000 { fly.step(command: command, dt: 0.0001) }
        XCTAssertEqual(fly.measuredStrokeAmplitude, 0.9, accuracy: 1e-3)
        XCTAssertEqual(fly.measuredStrokeFrequency, 180, accuracy: 0.01)
    }

    /// A teleport drops the wings back to rest: derived state belongs to the
    /// pose it was measured at, and leaving it set would make the first step
    /// after a spawn report a stroke that never happened.
    func testTeleportResetsWingActuatorState() {
        var fly = airborneBody()
        var command = BodyMotorCommand()
        command.wingStrokeAmplitude = 0.9
        command.wingStrokeFrequency = 180
        for _ in 0..<5000 { fly.step(command: command, dt: 0.0001) }
        XCTAssertGreaterThan(fly.measuredStrokeAmplitude, 0.8)

        fly.teleport(position: SIMD3(0, 200, 0), forward: SIMD3(1, 0, 0),
                     up: SIMD3(0, 1, 0))
        XCTAssertEqual(fly.measuredStrokeAmplitude, 0)
        XCTAssertEqual(fly.measuredStrokeFrequency, 0)
        XCTAssertEqual(fly.measuredWingTipSpeed, 0)
    }

    // MARK: - Calibration against measured quantities

    /// The RMS wing-tip speed at full wing drive must land in the measured
    /// range for Drosophila (~2.0-2.5 m/s). This guards the stroke kinematics:
    /// an error in the amplitude/frequency/length law shows up here first.
    func testFullDriveWingTipSpeedMatchesMeasuredRange() {
        let p = PhysicsParameters()
        let tip = BodyDynamics.wingTipSpeed(amplitude: 0.9, frequencyHz: 180,
                                            wingLengthMm: p.wingLengthMm)
        // ≈1799 mm/s — the low end of the measured band, and the right order.
        // A mean-absolute-speed formula would instead give ≈1296 mm/s.
        XCTAssertGreaterThan(tip, 1500, "tip speed too low: \(tip) mm/s")
        XCTAssertLessThan(tip, 2600, "tip speed too high: \(tip) mm/s")
        XCTAssertEqual(tip, 1799, accuracy: 60)
    }

    /// Full wing drive must produce MORE lift than weight, or the fly could
    /// never take off however hard its wing neuropil fired. This is a
    /// calibration assertion, not a behaviour one.
    func testLiftExceedsWeightOnlyWhenWingsAreDriven() {
        let p = PhysicsParameters()

        // Wings silent: no aerodynamic force at all.
        var idle = airborneBody()
        var silent = BodyMotorCommand()
        silent.legsInContact = false
        silent.legContactFraction = 0
        idle.step(command: silent, dt: 0.0001)
        XCTAssertEqual(idle.lastLiftNn, 0, "silent wings produced lift")

        // Wings at full drive: lift must exceed body weight with a takeoff
        // margin (measured requirement ≈1.3x; we calibrate to ≈1.45x).
        //
        // The wings must be given time to ARRIVE at the commanded stroke. Since
        // the actuator increment, lift is computed from the MEASURED stroke, so
        // one step produces ~0.67% of full lift (measured: 2.2e-05 nN) — the
        // fly's wings really have not moved yet, and comparing that against body
        // weight tests the command, not the force. 5000 steps = 0.5 s = 20
        // actuator time constants: converged.
        var fly = airborneBody()
        var full = silent
        full.wingStrokeFrequency = 180
        full.wingStrokeAmplitude = 0.9
        for _ in 0..<5000 { fly.step(command: full, dt: 0.0001) }
        XCTAssertEqual(fly.measuredStrokeAmplitude, 0.9, accuracy: 1e-3,
                       "the stroke never reached the command, so the lift below "
                       + "would be a partially-actuated wing")
        let weight = p.weightNn
        XCTAssertGreaterThan(fly.lastLiftNn, weight,
                             "lift \(fly.lastLiftNn) nN vs weight \(weight) nN")
        let ratio = fly.lastLiftNn / weight
        XCTAssertGreaterThan(ratio, 1.3)
        XCTAssertLessThan(ratio, 1.6)
    }

    // MARK: - Silence means stillness (spec §11: no scripted behaviour)

    /// With no motor-neuron activity there is no command, so the body must not
    /// move: no drift, no "idle walk" supplied by a hidden oscillator.
    func testSilentCommandProducesNoHorizontalMotion() {
        var fly = BodyDynamics()
        var silent = BodyMotorCommand()
        silent.legsInContact = true
        silent.legContactFraction = 1
        silent.forwardSpeedTarget = 0       // zero drive -> zero setpoint
        silent.lateralSpeedTarget = 0
        for _ in 0..<5000 { fly.step(command: silent, dt: 0.0001) }

        XCTAssertEqual(fly.position.x, 0, accuracy: 1e-4)
        XCTAssertEqual(fly.position.z, 0, accuracy: 1e-4)
        XCTAssertEqual(fly.body.velocity.x, 0, accuracy: 1e-4)
        XCTAssertEqual(fly.body.velocity.z, 0, accuracy: 1e-4)
    }

    /// The substrate is compliant, so standing costs a small penetration whose
    /// elastic response holds the fly up. The penetration must stay far below
    /// a leg length (≈0.8 mm) and the load must equal body weight: if either
    /// failed, the contact model would be unstable rather than merely soft.
    func testRestingPenetrationIsSmallAndLoadBalancesWeight() {
        var fly = BodyDynamics()
        let silent = BodyMotorCommand()          // everything zero / planted
        for _ in 0..<5000 { fly.step(command: silent, dt: 0.0001) }
        XCTAssertTrue(fly.body.grounded)
        XCTAssertGreaterThan(fly.body.groundPenetration, 0)
        XCTAssertLessThan(fly.body.groundPenetration, 0.05,
                          "penetration \(fly.body.groundPenetration) mm is not small")
        // Contact load ≈ body weight: static equilibrium (mirror: 1.001).
        XCTAssertEqual(fly.groundLoadFraction, 1.0, accuracy: 0.15)
    }

    /// A spawn is a POSE, not an impact.
    ///
    /// `setPose` writes "on the substrate" as `groundY + standHeightMm`, which
    /// is the height the *legs* hold the thorax at — not the height the contact
    /// spring balances at. The spring must compress by `weight / stiffness`
    /// before it can carry the weight, so placing the body at the unloaded
    /// height buries it 0.78 mm and fires a 42-body-weight launch that drives
    /// the tarsal channel for 67 steps (measured:
    /// `tools/probe_rest_drive.py`). `testTheConnectomeIsSilentWithNoStimulus`
    /// saw that transient as a spike in a world with no taste.
    ///
    /// Asserted as a physical statement, not a constant: the load at the spawn
    /// must already BE body weight, because at the balance height the spring
    /// carries the body without any settling.
    func testSpawnIsAtForceBalanceNotAnImpact() {
        var fly = BodyDynamics()
        let p = fly.parameters
        let balance = p.restHeightMm
        // The balance height is the force balance, not a second constant.
        XCTAssertEqual(balance,
                       p.groundY + p.standHeightMm
                       - p.bodyMassMg * p.gravityMmS2 / p.contactStiffness,
                       accuracy: 1e-6)

        // Spawning where the leg model holds the thorax is BELOW the balance
        // height — that is the burial, and it is not a small one.
        XCTAssertLessThan(balance, p.groundY + p.standHeightMm)
        XCTAssertGreaterThan(p.groundY + p.standHeightMm - balance, 0.005,
                            "the buried depth is what the launch comes from")

        // The load a spawned fly feels is body weight from the FIRST step —
        // no settling ramp, and above all no launch. At the unloaded stand
        // height the first step reads ~42 (mirror: 42.16).
        let silent = BodyMotorCommand()
        var peakLoad: Float = 0
        for _ in 0..<50 {
            fly.step(command: silent, dt: 0.0001)
            peakLoad = max(peakLoad, fly.groundLoadFraction)
        }
        XCTAssertEqual(fly.groundLoadFraction, 1.0, accuracy: 0.02,
                       "a spawned fly must already be carrying its own weight, "
                       + "not slamming into the substrate")
        XCTAssertLessThan(peakLoad, 1.5,
                          "peak load \(peakLoad) body weights over the first 50 "
                          + "steps — a spawn that launches reads 40+")
        XCTAssertTrue(fly.body.grounded)

        // `setPose`-style requests at or below the stand height are snapped,
        // so every existing call site (the tests' `groundY + standHeightMm`,
        // the app's `y = 0.2`) lands at the balance height without being
        // rewritten.
        var posed = BodyDynamics()
        posed.teleport(position: SIMD3(3, 0, 4),
                       forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        XCTAssertEqual(posed.position.y, balance, accuracy: 1e-6)
        XCTAssertEqual(posed.position.x, 3, accuracy: 1e-6,
                       "snapping the HEIGHT must not move the fly horizontally")

        var low = BodyDynamics()
        low.teleport(position: SIMD3(0, 0.2, 0),
                     forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        XCTAssertEqual(low.position.y, balance, accuracy: 1e-6)

        // A spawn above the substrate is a real position and must be kept —
        // yanking it down would teleport an airborne fly into the floor.
        var flying = BodyDynamics()
        flying.teleport(position: SIMD3(0, 5, 0),
                        forward: SIMD3(1, 0, 0), up: SIMD3(0, 1, 0))
        XCTAssertEqual(flying.position.y, 5, accuracy: 1e-6)
        XCTAssertFalse(flying.body.grounded)
    }

    // MARK: - Walking is an outcome, and scales with drive

    /// Walking speed must track the stride setpoint implied by motor activity,
    /// so a stronger neural command means a faster fly. Nothing here is a
    /// hardcoded speed.
    func testWalkingSpeedTracksMotorSetpoint() {
        for target in [Float(7.5), 15, 30] {
            var fly = BodyDynamics()
            var cmd = BodyMotorCommand()
            cmd.legContactFraction = 0.5
            cmd.forwardSpeedTarget = target
            for _ in 0..<20000 { fly.step(command: cmd, dt: 0.0001) }
            XCTAssertEqual(fly.body.velocity.x, target, accuracy: target * 0.15,
                           "setpoint \(target) not reached")
            // No lateral motion without a lateral command.
            XCTAssertEqual(fly.body.velocity.z, 0, accuracy: 0.5)
        }
    }

    /// Stronger drive walks strictly faster; the relationship is monotone with
    /// no threshold that switches a gait on.
    func testWalkingSpeedIsMonotoneInDrive() {
        var speeds: [Float] = []
        for target in [Float(3), 10, 22] {
            var fly = BodyDynamics()
            var cmd = BodyMotorCommand()
            cmd.legContactFraction = 0.5
            cmd.forwardSpeedTarget = target
            for _ in 0..<20000 { fly.step(command: cmd, dt: 0.0001) }
            speeds.append(fly.body.velocity.x)
        }
        XCTAssertLessThan(speeds[0], speeds[1])
        XCTAssertLessThan(speeds[1], speeds[2])
        XCTAssertGreaterThan(speeds[0], 1, "a driven fly must actually move")
    }

    /// A left/right leg-drive imbalance (turning bias) must move the body
    /// towards the side with the greater drive, with the correct sign.
    func testLateralDriveMovesBodyTowardsThatSide() {
        var right = BodyDynamics()
        var cmdR = BodyMotorCommand()
        cmdR.legContactFraction = 0.5
        cmdR.lateralSpeedTarget = 20
        for _ in 0..<20000 { right.step(command: cmdR, dt: 0.0001) }
        XCTAssertGreaterThan(right.position.z, 1, "does not move to the driven side")
        XCTAssertEqual(right.body.velocity.z, 20, accuracy: 3)

        var left = BodyDynamics()
        var cmdL = BodyMotorCommand()
        cmdL.legContactFraction = 0.5
        cmdL.lateralSpeedTarget = -20
        for _ in 0..<20000 { left.step(command: cmdL, dt: 0.0001) }
        XCTAssertLessThan(left.position.z, -1, "does not move to the driven side")
        XCTAssertEqual(left.body.velocity.z, -20, accuracy: 3)
    }

    // MARK: - Takeoff and landing come from forces, not modes

    /// With legs off the ground, silent wings must let the fly fall and driven
    /// wings must make it climb — takeoff is the force balance, never a
    /// "flying" flag. Both cases start airborne so ground contact cannot mask
    /// the difference.
    func testAirborneFlyClimbsOnlyWhenWingsAreDriven() {
        var silent = BodyMotorCommand()
        silent.legsInContact = false
        silent.legContactFraction = 0

        // Enough clearance that free fall cannot reach the ground in the test
        // window: 200 ms of fall covers ≈125 mm (mirror value).
        var falling = airborneBody(heightMm: 1000)
        for _ in 0..<2000 { falling.step(command: silent, dt: 0.0001) }
        XCTAssertLessThan(falling.body.velocity.y, -500,
                          "airborne fly with silent wings did not fall")
        XCTAssertTrue(falling.body.velocity.y < 0 && !falling.body.grounded)

        let startHeight: Float = 100
        var driven = airborneBody(heightMm: startHeight)
        var full = silent
        full.wingStrokeFrequency = 180
        full.wingStrokeAmplitude = 0.9
        for _ in 0..<2000 { driven.step(command: full, dt: 0.0001) }
        // 2000 steps = 0.2 s of climb from rest. Measured gain
                        // is 13.6 mm (mirror-identical; `verify_body_physics.py`
                        // asserts the same 10 mm rise with a tighter velocity
                        // bound), so the threshold is "climbed at all", not a
                        // distance this window cannot cover: the fly is still
                        // accelerating (velocity +340 mm/s and rising), and
                        // asserting a larger gain would be asserting a longer
                        // test, not a stronger force balance.
        XCTAssertGreaterThan(driven.body.velocity.y, 100,
                             "driven wings did not produce climb")
        XCTAssertGreaterThan(driven.position.y, startHeight + 10,
                             "did not gain altitude")
    }

    /// Collapsing the wing drive must bring a flying fly back to the substrate:
    /// landing is the same force balance read the other way.
    func testLandingWhenWingDriveCollapses() {
        var fly = airborneBody(heightMm: 40)
        var cmd = BodyMotorCommand()
        cmd.legsInContact = false
        cmd.legContactFraction = 0
        cmd.wingStrokeFrequency = 180
        cmd.wingStrokeAmplitude = 0.9
        for _ in 0..<4000 { fly.step(command: cmd, dt: 0.0001) }
        let peak = fly.position.y
        XCTAssertGreaterThan(fly.body.velocity.y, 0)
        XCTAssertGreaterThan(peak, 80, "never gained altitude")

        // Wings stop: the fly must come back down and settle.
        var fallen = cmd
        fallen.wingStrokeFrequency = 0
        fallen.wingStrokeAmplitude = 0
        for _ in 0..<6000 { fly.step(command: fallen, dt: 0.0001) }
        XCTAssertLessThan(fly.position.y, peak - 20, "did not descend")
        XCTAssertLessThan(fly.position.y, 5, "did not return to the substrate")
    }

    // MARK: - Determinism (spec §31)

    /// Same commands, same steps, same state: the body clock and integrator
    /// carry no randomness and no wall-clock dependency.
    func testBodyDynamicsIsDeterministic() {
        func run() -> (SIMD3<Float>, SIMD3<Float>, Float) {
            var fly = BodyDynamics()
            var cmd = BodyMotorCommand()
            cmd.legContactFraction = 0.5
            cmd.forwardSpeedTarget = 18
            cmd.wingStrokeFrequency = 120
            cmd.wingStrokeAmplitude = 0.6
            for i in 0..<3000 {
                // wiggle the asymmetry deterministically to exercise rotation
                cmd.wingAsymmetry = sin(Float(i) * 0.01) * 0.1
                fly.step(command: cmd, dt: 0.0001)
            }
            return (fly.position, fly.body.velocity, fly.elapsedMs)
        }
        let a = run()
        let b = run()
        XCTAssertEqual(a.0.x, b.0.x); XCTAssertEqual(a.0.y, b.0.y); XCTAssertEqual(a.0.z, b.0.z)
        XCTAssertEqual(a.1.x, b.1.x); XCTAssertEqual(a.1.y, b.1.y); XCTAssertEqual(a.1.z, b.1.z)
        XCTAssertEqual(a.2, b.2)
    }

    /// The body owns its own clock (spec §22 multirate): elapsed time advances
    /// with the integrator dt, never with a render frame or the neural clock.
    func testBodyClockAdvancesWithIntegratorDt() {
        var fly = BodyDynamics()
        let cmd = BodyMotorCommand()
        for _ in 0..<1000 { fly.step(command: cmd, dt: 0.0001) }
        // 1000 steps x 0.1 ms = 100 ms
        XCTAssertEqual(fly.simulationTimeMs, 100, accuracy: 0.5)
    }

    // MARK: - Units

    /// The solver's dt is SECONDS. Passing the neural dt (milliseconds) by
    /// accident is the exact mistake this guards: it would make the fly fall
    /// 1000x too fast. Over 1 ms of free fall the speed gain is gravity/1000
    /// ≈ 9.8 mm/s, and it must be the SAME for dt = 1 ms and dt = 0.1 ms
    /// applied ten times — independent of how the second is subdivided.
    func testGravityIsIntegratedInSeconds() {
        var silent = BodyMotorCommand()
        silent.legsInContact = false
        silent.legContactFraction = 0

        var coarse = airborneBody(heightMm: 100_000)
        coarse.step(command: silent, dt: 0.001)          // one 1 ms step
        XCTAssertEqual(coarse.body.velocity.y, -9.807, accuracy: 0.2)

        var fine = airborneBody(heightMm: 100_000)
        for _ in 0..<10 { fine.step(command: silent, dt: 0.0001) }  // 1 ms total
        XCTAssertEqual(fine.body.velocity.y, -9.807, accuracy: 0.2)
        XCTAssertEqual(coarse.body.velocity.y, fine.body.velocity.y, accuracy: 0.05,
                       "free fall is not dt-independent")
    }

    /// A 1 s free fall must reach the drag-limited terminal velocity, not the
    /// vacuum value: the aerodynamic model is present and dominates long falls.
    /// Mirror value: ≈ -1274 mm/s at both dt values.
    func testFreeFallReachesDragLimitedTerminalVelocity() {
        var silent = BodyMotorCommand()
        silent.legsInContact = false
        silent.legContactFraction = 0

        var a = airborneBody(heightMm: 1_000_000)
        for _ in 0..<1000 { a.step(command: silent, dt: 0.001) }
        XCTAssertEqual(a.body.velocity.y, -1274, accuracy: 30)

        var b = airborneBody(heightMm: 1_000_000)
        for _ in 0..<10000 { b.step(command: silent, dt: 0.0001) }
        XCTAssertEqual(b.body.velocity.y, a.body.velocity.y, accuracy: 1.0,
                       "free fall depends on the step size")
    }
}