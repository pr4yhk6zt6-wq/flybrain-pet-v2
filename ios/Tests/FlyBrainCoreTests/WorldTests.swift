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
        let light = LightSource(position: SIMD3(0, 0, 0), intensity: 1)
        let near = light.illuminance(at: SIMD3(0.5, 0, 0))
        let far = light.illuminance(at: SIMD3(10, 0, 0))
        XCTAssertGreaterThan(near, far)
        XCTAssertLessThanOrEqual(light.illuminance(at: SIMD3(100, 0, 0)), 0.02)
    }

    func testOdorConcentrationDecaysWithDistance() {
        let odor = OdorSource(position: SIMD3(0, 0, 0), kind: .food)
        let near = odor.concentration(at: SIMD3(0.5, 0, 0))
        let far = odor.concentration(at: SIMD3(8, 0, 0))
        XCTAssertGreaterThan(near, far)
        XCTAssertGreaterThan(near, 0)
        XCTAssertLessThan(far, 0.01)
    }

    func testWorldLuminanceFromLights() {
        let w = World()
        w.addLight(LightSource(position: SIMD3(0, 5, 0), intensity: 1))
        let bright = w.luminance(atX: 0, y: 0, z: 0)
        let dim = w.luminance(atX: 50, y: 0, z: 50)
        XCTAssertGreaterThan(bright, dim)
    }

    func testObstacleCollisionResolves() {
        let w = World()
        w.addObstacle(Obstacle(position: SIMD3(1, 0, 0), size: SIMD3(0.5, 1, 0.5)))
        var pos = SIMD3(0.9, 0, 0)   // inside box
        w.resolveCollision(position: &pos, radius: 0.1)
        // must be pushed outside the box
        XCTAssertGreaterThanOrEqual(abs(pos.x - 1), 0.25 + 0.1 - 0.001)
        // ground clamp
        var low = SIMD3(0, -1, 0)
        w.resolveCollision(position: &low)
        XCTAssertGreaterThanOrEqual(low.y, 0)
    }

    func testSceneIntegratesIntoClosedLoop() {
        let core = SimulationCore(connectome: TestSupport.regionalConnectome(neuronsPerRegion: 6))
        let w = World()
        w.addLight(LightSource(position: SIMD3(0, 4, 0), intensity: 1))
        w.addOdorSource(OdorSource(position: SIMD3(2, 0, 0), kind: .food, emissionRate: 2, diffusionConstant: 3))
        core.setScene(w)
        core.run(steps: 300)
        // The fly should have SOME neural activity through the closed loop
        XCTAssertGreaterThan(core.engine.spikeCount, 0)
        // and locomotion should move it through the scene (odour attracts via
        // sensory loop — procedural, no scripted "approach food").
        XCTAssertGreaterThanOrEqual(core.position.x, 0)
    }

    func testWorldRemovesOdorSources() {
        let w = World()
        w.addOdorSource(OdorSource(position: SIMD3(0, 0, 0), kind: .food))
        XCTAssertEqual(w.odorConcentration(atX: 0.1, y: 0, z: 0).left, w.odorConcentration(atX: 0.1, y: 0, z: 0).right, accuracy: 0.01)
        w.removeAllOdorSources()
        XCTAssertEqual(w.odorConcentration(atX: 0, y: 0, z: 0).left, 0, accuracy: 0.001)
    }
}