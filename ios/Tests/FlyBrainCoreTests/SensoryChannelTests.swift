//
//  SensoryChannelTests.swift
//  FlyBrainCoreTests
//
//  Every sensory channel must land on a cell the motor readout does NOT sum,
//  and must land on SOME cell at all.
//
//  These are the two mirror-image failures the shipped-asset probe found on the
//  real BANC ingest (tools/probe_shipped_asset_channels.py):
//
//    SHORTED — the selection rule picked a cell the readout sums, so a sensory
//              current became motor command without touching the connectome;
//    DEAD    — the rule asked for side 0, and not one of the release's 153,746
//              cells carries side 0, so six of seven channels resolved to
//              nothing and the fly was deaf rather than short-circuited.
//
//  Fixing SHORTED by preferring a side match caused DEAD, so both are asserted
//  on the same fixtures.
//

import XCTest
@testable import FlyBrainCore

final class SensoryChannelTests: XCTestCase {

    /// A region whose cells ALL have a side (never 0), mixing motor-labelled and
    /// sensory-labelled cells — the shape the BANC release actually has.
    private func lateralisedConnectome() -> Connectome {
        let header = ConnectomeHeader(
            magic: 0x46425031, version: 2, flags: 0,
            neuronCount: 6, synapseCount: 0,
            morphologyCount: 0, regionCount: 0,
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "TestSupport",
            description: "lateralised sensory region")
        let c = Connectome(header: header)
        // Two motor cells (left, right) then two sensory afferents (left, right).
        let spec: [(side: UInt8, flags: UInt8, type: Int)] = [
            (1, NeuronFlags.motor, 1), (2, NeuronFlags.motor, 2),
            (1, NeuronFlags.sensory, 3), (2, NeuronFlags.sensory, 4),
            (1, NeuronFlags.sensory, 5), (2, NeuronFlags.sensory, 6),
        ]
        for (i, s) in spec.enumerated() {
            c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: UInt16(s.type),
                region: UInt8(RegionID.legNeuromere.rawValue), side: s.side,
                transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                provenance: 0, flags: s.flags, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0,
                outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0))
        }
        c.setOutgoingRanges(Array(repeating: OutEdgeRange(start: 0, count: 0),
                                  count: spec.count))
        return c
    }

    /// A side the region does not have must still resolve to a real afferent.
    ///
    /// This is the DEAD case: the release has no side-0 cell anywhere, so a
    /// rule that only falls back to side 0 returns nil for every channel that
    /// asks for side 0.
    func testAChannelAskingWithoutASideStillFindsAnAfferent() {
        let c = lateralisedConnectome()
        let s = SensoryInterface(connectome: c)
        // Every cell of this fixture carries a side (1 or 2) and none carries 0,
        // which is the shape of the real release.
        XCTAssertFalse(c.neurons.contains { $0.side == 0 },
                       "the fixture must have no centre-line cell, or this test "
                       + "does not reproduce the DEAD case")
        for region in [RegionID.legNeuromere] {
            let n = s.inputAfferent(region: region)
            XCTAssertNotNil(n, "\(region) resolved to nothing: the channel is dead "
                            + "on an asset whose cells all carry a side")
            guard let n else { continue }
            XCTAssertTrue(c.neurons[Int(n)].isSensoryNeuron,
                          "the afferent must be a sensory-labelled cell")
            XCTAssertFalse(c.neurons[Int(n)].isMotorNeuron,
                           "an afferent must never be a cell the readout sums")
        }
    }

    /// The side argument must still discriminate: left and right antennal
    /// sampling are different cells, or the fly cannot tell which antenna
    /// smells the odour.
    func testTwoSidesOfOneRegionDoNotCollapseToOneNeuron() {
        let c = lateralisedConnectome()
        let s = SensoryInterface(connectome: c)
        let left = s.inputNeuron(region: .legNeuromere, side: 1)
        let right = s.inputNeuron(region: .legNeuromere, side: 2)
        XCTAssertNotNil(left)
        XCTAssertNotNil(right)
        XCTAssertNotEqual(left, right,
                          "both sides resolved to the same cell, so the side "
                          + "argument no longer means anything")
    }

    /// The exclusion must be about the READOUT's set, not about a hard-coded
    /// region: a motor-only region has no afferent, and the honest answer is
    /// nil rather than injecting into the readout.
    func testAMotorOnlyRegionReportsNoAfferentRatherThanTheReadout() {
        let header = ConnectomeHeader(
            magic: 0x46425031, version: 2, flags: 0,
            neuronCount: 2, synapseCount: 0,
            morphologyCount: 0, regionCount: 0,
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "TestSupport",
            description: "motor-only region")
        let c = Connectome(header: header)
        for i in 0..<2 {
            c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: UInt16(i),
                region: UInt8(RegionID.wingNeuropil.rawValue), side: 1,
                transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                provenance: 0, flags: NeuronFlags.motor, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0,
                outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0))
        }
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0),
                             OutEdgeRange(start: 0, count: 0)])
        let s = SensoryInterface(connectome: c)
        XCTAssertNil(s.inputAfferent(region: .wingNeuropil),
                     "a motor-only neuropil has no afferent; returning the "
                     + "readout cell instead is the short circuit")
        XCTAssertTrue(s.wingStrainInput(intensity: 1).isEmpty,
                      "the channel must be silent, not inject into the readout")
    }

    /// An asset with no class byte anywhere must behave exactly as before:
    /// positional selection, still a real cell, no invention of a label. This is
    /// the case the fixtures exercise, so it must not regress while the real
    /// release's case is being fixed.
    func testAssetsWithoutClassBytesKeepPositionalSelection() {
        let c = TestSupport.regionalConnectome(neuronsPerRegion: 10, cellClasses: false)
        let s = SensoryInterface(connectome: c)
        let bySide = s.inputNeuron(region: .legNeuromere, side: 1)
        let afferent = s.inputAfferent(region: .legNeuromere)
        XCTAssertNotNil(bySide)
        XCTAssertNotNil(afferent)
        XCTAssertEqual(c.neurons[Int(afferent!)].flags, 0,
                       "an unlabelled asset must not have a label invented for it")
        XCTAssertTrue(c.neuronIndices(in: .legNeuromere).contains(afferent!),
                      "the positional fallback must return a cell of the region")
    }
}