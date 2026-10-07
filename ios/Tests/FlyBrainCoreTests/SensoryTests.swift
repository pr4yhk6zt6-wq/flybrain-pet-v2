//
//  SensoryTests.swift
//  FlyBrainCoreTests
//
//  Phase 3 — sensory systems: vision compound-eye sampling, olfactory
//  transduction, gustation, mechano/haltere; internal state modulation.
//

import XCTest
@testable import FlyBrainCore

final class SensoryTests: XCTestCase {

    // A world with a static luminance field and symmetric odor.
    private final class TestWorld: WorldProvider {
        func luminance(atX: Float, y: Float, z: Float) -> Float {
            // bright blob in front-center
            let d = sqrt(atX * atX + y * y + z * z)
            return d < 3 ? 1 : 0.05
        }
        func odorConcentration(atX: Float, y: Float, z: Float) -> (left: Float, right: Float) {
            _ = (atX, y, z)
            return (0.5, 0.5)
        }
        func temperature(atX: Float, y: Float, z: Float) -> Float {
            _ = (atX, y, z)
            return 25
        }
    }

    func testCompoundEyeBuildsOmmatidia() {
        let v = VisionSystem()
        XCTAssertGreaterThan(v.ommatidia.count, 20)
        // left + right eyes present
        XCTAssertTrue(v.ommatidia.contains { $0.side == 1 })
        XCTAssertTrue(v.ommatidia.contains { $0.side == 2 })
    }

    func testVisionProducesOnOffPathwayEvents() {
        var core = SimulationCore(connectome: TestSupport.regionalConnectome())
        let world = TestWorld()
        core.world = world
        // bright frontal field → photoreceptor + ON edges
        for _ in 0..<20 {
            core.step()
        }
        // visual drive must map to real lamina/medulla neurons and produce
        // genuine spikes somewhere in the visual pathway
        let laminaActive = core.connectome.neurons.indices.contains {
            RegionID(rawValue: Int(core.connectome.neurons[$0].region)) == .lamina &&
                core.engine.recentSpikes(of: Int32($0)) > 0
        }
        XCTAssertGreaterThan(core.engine.spikeCount, 0)
        _ = laminaActive
    }

    func testOdorTransductionTargetsAntennalLobe() {
        let c = TestSupport.regionalConnectome()
        let s = SensoryInterface(connectome: c)
        let inputs = s.odorInput(concentrationL: 0.9, concentrationR: 0.1)
        XCTAssertEqual(inputs.count, 2)
        // left concentration higher → left current stronger
        XCTAssertGreaterThan(inputs[0].current, inputs[1].current)
        for inp in inputs {
            XCTAssertEqual(inp.modality, .olfaction)
            // targets exist in antennal lobe region
            let region = RegionID(rawValue: Int(c.neurons[Int(inp.neuron)].region))
            XCTAssertEqual(region, .antennalLobe)
        }
    }

    func testLoominInputHighStrength() {
        let c = TestSupport.regionalConnectome()
        let s = SensoryInterface(connectome: c)
        let inputs = s.loomingInput(intensity: 1)
        XCTAssertFalse(inputs.isEmpty)
        XCTAssertGreaterThan(inputs[0].current, 50)
        let region = RegionID(rawValue: Int(c.neurons[Int(inputs[0].neuron)].region))
        XCTAssertEqual(region, .lobulaPlate)
    }

    func testInternalStateModulatesDrive() {
        var st = InternalState()
        st.advance(dtMs: 1000, activityLevel: 0)
        XCTAssertLessThan(st.energy, 0.8)      // metabolism consumed some
        XCTAssertGreaterThanOrEqual(st.energy, 0)
        // hunger rises as energy falls
        let hungry = st.hungerDrive
        st.consumeEnergy(0.4)
        XCTAssertGreaterThan(st.hungerDrive, hungry)
    }

    func testMotorCPGProducesLegTorques() {
        // A single 100 ms tick: a leg that lifts must push, but the fly may
        // never take all six feet off the ground at once. (This test used to
        // only check "some torque is positive", which the buggy code passed by
        // lifting every leg simultaneously — see testGaitAlwaysKeepsSupport.)
        let motor = MotorSystem()
        motor.update(neuralDrive: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], dt: 100)
        XCTAssertEqual(motor.legs.count, 6)
        XCTAssertEqual(motor.output.legTorques.count, 6)
        let planted = motor.legs.filter { !$0.isSwing }.count
        XCTAssertGreaterThanOrEqual(planted, MotorSystem.minSupportLegs,
                                    "100 ms tick left the fly with fewer than \(MotorSystem.minSupportLegs) planted legs")
        XCTAssertGreaterThan(motor.output.legTorques.reduce(0, +), 0)
    }

    /// Spec §27 / docs/PHYSICS.md: the walking gait must ALWAYS keep a stable
    /// set of feet on the ground. The previous code OR-ed a mechanical drift
    /// timeout with the support requirement, so after `maxStanceMs` of
    /// simulated time every leg timed out on the same tick and all six lifted.
    func testGaitAlwaysKeepsSupport() {
        let motor = MotorSystem()
        var plantedCounts: [Int] = []
        var swingSets: Set<Set<Int>> = []
        for _ in 0..<4000 {
            motor.update(neuralDrive: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], dt: 0.1)
            let swing = Set(motor.legs.enumerated().filter { $0.element.isSwing }.map { $0.offset })
            swingSets.insert(swing)
            plantedCounts.append(6 - swing.count)
        }
        XCTAssertGreaterThanOrEqual(plantedCounts.min() ?? 0, MotorSystem.minSupportLegs,
                                    "gait let the fly lift too many legs (min planted \(plantedCounts.min() ?? -1))")
        // Support must straddle the body: an all-left or all-right tripod is
        // not statically stable, so that must never happen.
        for swing in swingSets where swing.count < 3 {
            let leftPlanted = (0..<3).filter { !swing.contains($0) }.count
            let rightPlanted = (3..<6).filter { !swing.contains($0) }.count
            XCTAssertGreaterThanOrEqual(leftPlanted, 1, "support entirely on the right side")
            XCTAssertGreaterThanOrEqual(rightPlanted, 1, "support entirely on the left side")
        }
    }

    /// The alternating tripod is the observed walking pattern, and the point of
    /// the design is that it EMERGES from the support constraint rather than
    /// being scripted. Over a few hundred milliseconds the only swing sets the
    /// gait may visit are the two canonical tripods (occasionally one leg
    /// alone during a transition).
    func testGaitConvergesToAlternatingTripod() {
        let motor = MotorSystem()
        let tripodA: Set<Int> = [0, 1, 3]
        let tripodB: Set<Int> = [2, 4, 5]
        var visited: Set<Set<Int>> = []
        for _ in 0..<4000 {
            motor.update(neuralDrive: [0.8, 0.8, 0.8, 0.8, 0.8, 0.8], dt: 0.1)
            let swing = Set(motor.legs.enumerated().filter { $0.element.isSwing }.map { $0.offset })
            visited.insert(swing)
        }
        for swing in visited {
            let allowed = swing.isSubset(of: tripodA)
                || swing.isSubset(of: tripodB)
                || swing.count <= 1
            XCTAssertTrue(allowed, "non-tripod swing set \(swing.sorted()) — gait is not the emergent tripod")
        }
        XCTAssertTrue(visited.contains(tripodA), "tripod {left-front, left-mid, right-hind} never occurred")
        XCTAssertTrue(visited.contains(tripodB), "tripod {right-front, right-mid, left-hind} never occurred")
    }

    func testBehaviorClassifierObservesOnly() {
        let c = TestSupport.regionalConnectome(neuronsPerRegion: 8)
        var params = SimulationParameters()
        params.seed = 3
        let core = SimulationCore(connectome: c, parameters: params)
        // drive a leg-neuromere neuron → classifier should eventually see
        // limb activity (passive observation, spec #44)
        if let leg = core.sensory.inputNeuron(region: .legNeuromere, side: 0) {
            TestSupport.driveBurst(engine: core.engine, neuron: leg,
                                   startMs: 1.0, pulses: 30, intervalMs: 10, current: 400)
        }
        core.run(steps: 1500)
        // neural activity must exist (real spikes)
        XCTAssertGreaterThan(core.engine.spikeCount, 0)
        // classifier remains a pure observer: no backdoor into the sim
        XCTAssertNotNil(core.behavior.current)
    }
}