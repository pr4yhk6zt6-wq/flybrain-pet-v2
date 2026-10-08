//
//  WorldTests.swift
//  FlyBrainCoreTests
//
//  Phase: 3D world (spec #29, #30) — sensory objects, odor diffusion,
//  collision, closed-loop scene integration.
//

import XCTest
@testable import FlyBrainCore

final class WorldTests: XCTestCase {

    func testLightIlluminanceFalloff() {
        let light = LightSource(position: SIMD3<Float>(0, 0, 0), intensity: 1)
        let near = light.illuminance(at: SIMD3<Float>(0.5, 0, 0))
        let far = light.illuminance(at: SIMD3<Float>(10, 0, 0))
        XCTAssertGreaterThan(near, far)
        XCTAssertLessThanOrEqual(light.illuminance(at: SIMD3<Float>(100, 0, 0)), 0.02)
    }

    func testOdorConcentrationDecaysWithDistance() {
        let odor = OdorSource(position: SIMD3<Float>(0, 0, 0), kind: .food)
        let near = odor.concentration(at: SIMD3<Float>(0.5, 0, 0))
        let far = odor.concentration(at: SIMD3<Float>(8, 0, 0))
        XCTAssertGreaterThan(near, far)
        XCTAssertGreaterThan(near, 0)
        XCTAssertLessThan(far, 0.01)
    }

    func testWorldLuminanceFromLights() {
        let w = World()
        w.addLight(LightSource(position: SIMD3<Float>(0, 5, 0), intensity: 1))
        let bright = w.luminance(atX: 0, y: 0, z: 0)
        let dim = w.luminance(atX: 50, y: 0, z: 50)
        XCTAssertGreaterThan(bright, dim)
    }

    func testObstacleCollisionResolves() {
        let w = World()
        w.addObstacle(Obstacle(position: SIMD3<Float>(1, 0, 0), size: SIMD3<Float>(0.5, 1, 0.5)))
        var pos = SIMD3<Float>(0.9, 0, 0)   // inside box
        w.resolveCollision(position: &pos, radius: 0.1)
        // must be pushed outside the box
        XCTAssertGreaterThanOrEqual(abs(pos.x - 1), 0.25 + 0.1 - 0.001)
        // ground clamp
        var low = SIMD3<Float>(0, -1, 0)
        w.resolveCollision(position: &low)
        XCTAssertGreaterThanOrEqual(low.y, 0)
    }

    func testSceneIntegratesIntoClosedLoop() {
        // The sensory->motor arc, asserted as a falsifiable claim.
        //
        // This test used to assert only `engine.spikeCount > 0` and
        // `position.x >= 0`, against `regionalConnectome` — a fixture whose
        // chains are cut at every region boundary, so no excitation can leave
        // the sensory region and the sensory->motor path does not exist in it.
        // The position assertion was vacuous: the fly is spawned at x = 0.
        //
        // Strengthening it exposed a real bug, and the bug was in the OTHER
        // fixture, not in the brain. `closedLoopConnectome`'s `wire()` helper
        // overwrote a neuron's outgoing range when that neuron had a second
        // edge, so `wire(23, 24)` (central complex -> leg pool) was erased by
        // `wire(23, 28)` (central complex -> wing pool). The leg pool was
        // unreachable, the motor command stayed exactly 0.0 while the spike
        // count rose fourfold with odour, and the body's displacement was
        // identical with and without stimulus. `Connectome.validate()` names
        // this exactly ("synapse k referenced by 0 neurons"), which is why the
        // first thing below is to call it.
        //
        // The measured symptom, on the broken fixture:
        //   odour | spikes | walkDrive | forwardSpeedTarget | locomotion
        //   ------+--------+-----------+-------------------+-----------
        //      0  |   550  |    0.0    |        0.0        | 0.5286 mm
        //      4  |   885  |    0.0    |        0.0        | 0.5286 mm
        //
        // With the fixture fixed the command tracks the input and the fly moves
        // only when it is stimulated. `tools/verify_closed_loop_fixture.py`
        // gates the fixture itself (43 edges emitted, both motor pools
        // reachable) and fails on the pre-fix wiring.
        func darkWorld(odorEmission: Float) -> World {
            let w = World()
            w.backgroundLuminance = 0
            w.ambientLight = 0
            if odorEmission > 0 {
                // saturate the source at the antennae (concentration decays as
                // exp(-d^2/2D)); dilution 100 keeps the falloff negligible.
                w.addOdorSource(OdorSource(position: SIMD3<Float>(0, 0.7, 0),
                                           kind: .food, emissionRate: odorEmission,
                                           diffusionConstant: 100))
            }
            return w
        }

        func run(connectome: Connectome,
                 odorEmission: Float,
                 steps: Int) -> (moved: Float, spikes: UInt64) {
            let core = SimulationCore(connectome: connectome)
            core.setScene(darkWorld(odorEmission: odorEmission))
            // Settle first: the body is spawned at its stand height and settles
            // under gravity, which moves it by ~1.8 mm on its own. Measuring
            // from the spawn pose would credit that transient to the loop.
            //
            // 2500 steps, not 500: the fixtures drive the motor pools to zero,
            // so a single spike during settling leaves a velocity that decays
            // for another ~0.9 s (900 steps) and moves the animal a further
            // ~0.009 mm. At 500 the no-odour control therefore measured
            // 0.008919 mm of pure free decay — below the 0.01 threshold but
            // only by 11 %, i.e. the assertion's margin was being set by the
            // body's own coasting rather than by anything the loop did.
            // `tools/mirror_regional_fixture.py` reports both figures (its
            // `rest` parameter), so the number is measured, not guessed.
            core.run(steps: 2500)
            let start = core.position
            core.run(steps: steps)
            let d = core.position - start
            return (FlyMath.length(SIMD3<Float>(d.x, 0, d.z)), core.engine.spikeCount)
        }

        let closed = TestSupport.closedLoopConnectome()
        let regional = TestSupport.regionalConnectome(neuronsPerRegion: 10)

        // The fixture must be well formed BEFORE anything is concluded from it.
        // `validate()` checks that every synapse is owned by exactly one neuron,
        // which is the invariant the old `wire()` violated. Without this the
        // suite happily measured a connectome that was not the one described.
        XCTAssertEqual(closed.validate(), [],
                       "the closed-loop fixture is not a valid connectome")

        let short = run(connectome: closed, odorEmission: 4, steps: 1500)
        let long = run(connectome: closed, odorEmission: 4, steps: 4000)

        // The arc carries excitation through the connectome and displaces the
        // animal, increasingly so as the loop keeps running.
        XCTAssertGreaterThan(short.spikes, 0)
        XCTAssertGreaterThan(short.moved, 0.05,
                             "odour must move the fly through the sensory->motor loop")
        XCTAssertGreaterThan(long.moved, short.moved,
                             "displacement must grow with time, not stall")

        // Control 1 — same connectome, odour removed. The ONLY difference is the
        // sensory input, so any motion here would come from something other than
        // the loop.
        //
        // This is the control that FAILED on commit fe65764 (3.265094 mm in CI),
        // because the reafference arm carried a DC term: a standing fly holds
        // its tarsal load at ~1.0 body-weight, and with the absolute load
        // injected as current that constant became a permanent input, so the
        // animal walked in a dark, odourless world. Offline the same arm also
        // pins the readout's leg drive at saturation, so adding odour barely
        // changes the motion — the stimulus stops being what sets the
        // behaviour. `tools/mirror_regional_fixture.py` reproduces both branch
        // values and gates the fix.
        let noOdor = run(connectome: closed, odorEmission: 0, steps: 4000)
        XCTAssertLessThan(noOdor.moved, 0.01,
                          "the fly moved \(noOdor.moved) mm with no odour in the scene")

        // Control 2 — same odour, connectome with no path out of the sensory
        // region. Sensory neurons still fire (so `spikeCount > 0` is satisfied),
        // but nothing reaches the motor pools, so the animal cannot move. This
        // is precisely what the old assertion could not distinguish.
        let cutPath = run(connectome: regional, odorEmission: 4, steps: 4000)
        XCTAssertGreaterThan(cutPath.spikes, 0)
        XCTAssertLessThan(cutPath.moved, 0.01,
                          "the fly moved \(cutPath.moved) mm even though its connectome "
                          + "has no path from the sensory region to the motor pools")

        // Control 3 — the cut-path fixture WITH cell-class labels. Control 2
        // alone could not separate two different things, and that ambiguity was
        // hidden by the DC bug: on the unlabelled fixture the readout falls back
        // to summing every leg-neuromere neuron, so the afferent the reafference
        // arm injects into was itself summed as "motor output". The current then
        // entered the readout directly and the resulting displacement depended
        // only on whether the arm was injecting (4000 of 4000 steps), not on
        // whether the connectome carried a path. Measured offline, that fallback
        // alone accounts for 0.986033 mm against Swift's 0.9931856 mm, i.e. the
        // whole of Control 2's failure. With the labels present the readout must
        // select the motor cells and the arm must land on a sensory cell, so the
        // two are no longer the same population.
        let labelled = TestSupport.regionalConnectome(neuronsPerRegion: 10,
                                                      cellClasses: true)
        XCTAssertEqual(labelled.validate(), [],
                       "the labelled fixture is not a valid connectome")
        let cutPathLabelled = run(connectome: labelled, odorEmission: 4, steps: 4000)
        XCTAssertLessThan(cutPathLabelled.moved, 0.01,
                          "the fly moved \(cutPathLabelled.moved) mm on a labelled asset "
                          + "with no sensory->motor path; a sensory current landing on a "
                          + "cell the readout also sums would short the loop")
        // The positive control has to stay loud, or "it stopped moving" would be
        // satisfied by muting the sensory system altogether.
        XCTAssertGreaterThan(long.moved, 0.05,
                             "the loop must still move the fly when it is stimulated")
        XCTAssertGreaterThan(long.moved, 10 * max(noOdor.moved, 1e-6),
                             "the response to odour must be distinguishable from the "
                             + "unstimulated control, not merely non-zero")
    }

    func testWorldRemovesOdorSources() {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3<Float>(0, 0, 0), kind: .food))
        XCTAssertEqual(w.odorConcentration(atX: 0.1, y: 0, z: 0).left,
                       w.odorConcentration(atX: 0.1, y: 0, z: 0).right, accuracy: 0.01)
        w.removeAllOdorSources()
        XCTAssertEqual(w.odorConcentration(atX: 0, y: 0, z: 0).left, 0, accuracy: 0.001)
    }
}