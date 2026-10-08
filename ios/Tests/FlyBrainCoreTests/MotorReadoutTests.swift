//
//  MotorReadoutTests.swift
//  FlyBrainCoreTests
//
//  The motor readout must select cells by CLASS, not by neuropil.
//

import XCTest
@testable import FlyBrainCore

final class MotorReadoutTests: XCTestCase {

    /// Drive every neuron in the connectome hard, then take one step, and
    /// report what the readout summed.
    ///
    /// Injection goes through the public engine API, so the test exercises the
    /// same path the simulation does rather than a parallel test-only one.
    private func readoutAfterDriving(_ c: Connectome,
                                     steps: Int = 120) -> (total: Float, labelled: Float, classified: Bool) {
        let core = SimulationCore(connectome: c)
        for i in 0..<steps {
            let t = Double(i) * core.parameters.dt
            for pid in 0..<Int32(c.neuronCount) {
                core.engine.injectCurrent(into: pid, current: 60, at: t)
            }
            core.step()
        }
        return (core.motorDriveTotalForTesting,
                core.motorDriveFromLabelledOnlyForTesting,
                core.motorClassified)
    }

    /// Two neurons in the SAME neuropil: one motor-labelled, one sensory.
    /// A readout keyed on region sums BOTH; a readout keyed on class sums one.
    func testReadoutSumsMotorCellsNotEveryNeuronInTheNeuropil() {
        let c = TestSupport.pairedClassConnectome(motorFlags: NeuronFlags.motor,
                                                  sensoryFlags: NeuronFlags.sensory)
        let r = readoutAfterDriving(c)
        XCTAssertTrue(r.classified, "an asset with cell classes must be read by class")
        XCTAssertGreaterThan(r.total, 0, "the motor neuron was driven but nothing was read")
        XCTAssertEqual(r.labelled, r.total, accuracy: 1e-3,
                       "every unit summed must come from a cell carrying the motor label; "
                       + "a mismatch means the sensory neighbour was included")
    }

    /// The fallback must be honest: an asset with no cell class keeps working
    /// exactly as before (region-only) and must SAY so, rather than reporting
    /// itself as class-selected.
    func testAssetsWithoutCellClassUseRegionFallbackAndSaySo() {
        let c = TestSupport.pairedClassConnectome(motorFlags: 0, sensoryFlags: 0)
        let r = readoutAfterDriving(c)
        XCTAssertFalse(r.classified,
                       "an asset with no cell class must not claim class selection")
        XCTAssertGreaterThan(r.total, 0, "the fallback must still drive the body")
        XCTAssertEqual(r.labelled, 0,
                       "with no labels there is nothing to attribute to a motor cell")
    }

    /// The two modes must be genuinely different — otherwise the class label
    /// would be decorative and the test above would pass for the wrong reason.
    func testClassSelectionReadsLessThanRegionOnlyWhenANeighbourIsSensory() {
        let classed = readoutAfterDriving(
            TestSupport.pairedClassConnectome(motorFlags: NeuronFlags.motor,
                                              sensoryFlags: NeuronFlags.sensory))
        let fallback = readoutAfterDriving(
            TestSupport.pairedClassConnectome(motorFlags: 0, sensoryFlags: 0))
        XCTAssertLessThan(classed.total, fallback.total * 0.75,
                          "class selection must exclude the sensory neighbour: "
                          + "region-only sums both cells (\(fallback.total)), "
                          + "class selection sums one (\(classed.total))")
    }

    /// The class label must not change the command DELIVERED to the body in a
    /// way that is invisible: whatever the readout sums, it has to reach
    /// `neuralDrive`, or the fix would be cosmetic.
    func testReadoutReachesTheMotorCommand() {
        let c = TestSupport.pairedClassConnectome(motorFlags: NeuronFlags.motor,
                                                  sensoryFlags: NeuronFlags.sensory)
        let core = SimulationCore(connectome: c)
        for i in 0..<120 {
            let t = Double(i) * core.parameters.dt
            core.engine.injectCurrent(into: 0, current: 60, at: t)
            core.engine.injectCurrent(into: 1, current: 60, at: t)
            core.step()
        }
        XCTAssertTrue(core.neuralDrive.contains { $0 > 0 },
                      "the readout produced a drive but the motor command stayed 0")
    }
}