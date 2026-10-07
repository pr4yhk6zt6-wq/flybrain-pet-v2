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

    func testDiagnoseLoopTransient() {
        // TEMPORARY diagnostic: characterise the displacement curve so the
        // control threshold is set from measurement, not assumption.
        func run(connectome: Connectome, odorEmission: Float, steps: Int) -> Float {
            let core = SimulationCore(connectome: connectome)
            let w = World()
            w.backgroundLuminance = 0
            w.ambientLight = 0
            if odorEmission > 0 {
                w.addOdorSource(OdorSource(position: SIMD3<Float>(0, 0.7, 0),
                                           kind: .food, emissionRate: odorEmission,
                                           diffusionConstant: 100))
            }
            core.setScene(w)
            let start = core.position
            core.run(steps: steps)
            let d = core.position - start
            print("DIAG odour=\(odorEmission) steps=\(steps) "
                  + "spikes=\(core.engine.spikeCount) "
                  + "pos=\(core.position) moved=\(FlyMath.length(SIMD3<Float>(d.x, 0, d.z)))")
            return FlyMath.length(SIMD3<Float>(d.x, 0, d.z))
        }
        let closed = TestSupport.closedLoopConnectome()
        let regional = TestSupport.regionalConnectome(neuronsPerRegion: 10)
        for steps in [250, 500, 1000, 2000, 4000] {
            _ = run(connectome: closed, odorEmission: 0, steps: steps)
            _ = run(connectome: closed, odorEmission: 4, steps: steps)
            _ = run(connectome: regional, odorEmission: 4, steps: steps)
        }
    }

    func testSceneIntegratesIntoClosedLoop() {
        // This test used to assert only `engine.spikeCount > 0` and
        // `position.x >= 0`, against `regionalConnectome` — a fixture whose
        // chains are cut at every region boundary, so no excitation can leave
        // the sensory region and the sensory->motor path does not exist in it.
        // The position assertion was vacuous: the fly is spawned at x = 0, so
        // it passed while asserting nothing at all about the loop.
        //
        // The claim is now checkable, and it is checked in BOTH directions,
        // because a "loop" whose output does not depend on its input is not a
        // loop:
        //   odour + a connectome that reaches the motor pools  -> it walks
        //   odour + a connectome that does not reach them      -> it does not
        //   no odour + a connectome that does reach them       -> it does not
        // The scene is left DARK (backgroundLuminance = 0, no lights): the
        // world otherwise emits a photoreceptor event at every ommatidium on
        // every step (lum = 0.46 > 0.02), which would drive the animal through
        // the visual pathway and make the odour control meaningless.
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
            let start = core.position
            core.run(steps: steps)
            let d = core.position - start
            return (FlyMath.length(SIMD3<Float>(d.x, 0, d.z)), core.engine.spikeCount)
        }

        let closed = TestSupport.closedLoopConnectome()
        let regional = TestSupport.regionalConnectome(neuronsPerRegion: 10)

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