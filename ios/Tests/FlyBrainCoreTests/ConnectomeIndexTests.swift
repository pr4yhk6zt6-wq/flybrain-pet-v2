import XCTest
@testable import FlyBrainCore

/// The region index exists so hot paths stop scanning every neuron. A stale or
/// wrong index would silently mis-route sensory input, so it must agree with a
/// direct scan and must be invalidated when neurons are appended.
final class ConnectomeIndexTests: XCTestCase {

    func testRegionIndexMatchesDirectScan() {
        let c = TestSupport.regionalConnectome()
        let probe: [RegionID] = [.lamina, .medulla, .mushroomBody, .legNeuromere,
                                 .haltereNeuropil, .subesophagealZone]
        for region in probe {
            let indexed = c.neuronIndices(in: region)
            let scanned = c.neurons.indices
                .filter { RegionID(rawValue: Int(c.neurons[$0].region)) == region }
                .map(Int32.init)
            XCTAssertEqual(indexed, scanned,
                           "index must match a direct scan for \(region)")
        }
    }

    func testRegionIndexInvalidatesOnAppend() {
        let c = TestSupport.regionalConnectome()
        let before = c.neuronIndices(in: .lamina).count   // also builds the cache
        let n = NeuronRecord(
            canonicalID: 9999, datasetID: 0, type: 1,
            region: UInt8(RegionID.lamina.rawValue), side: 1,
            transmitter: UInt8(TransmitterType.cholinergic.rawValue),
            provenance: TestSupport.provIndex(.measured),
            morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
            outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0)
        XCTAssertTrue(c.appendNeuron(n))
        XCTAssertEqual(c.neuronIndices(in: .lamina).count, before + 1,
                       "appending a neuron must invalidate the cached index")
    }

    func testUnknownRegionReturnsEmpty() {
        let c = TestSupport.regionalConnectome()
        XCTAssertTrue(c.neuronIndices(in: .unknown).isEmpty)
    }
}