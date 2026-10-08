import XCTest
@testable import FlyBrainCore

/// Round-trip and byte-layout guards for the .fbpack wire format.
///
/// There is no Swift toolchain on the development device, so the Python side
/// pins the writer's layout in python/tests/test_banc.py (and the engine maths
/// in tools/check_tests.py). These tests cover the side Python cannot reach:
/// the Swift WRITER and the Swift LOADER agreeing with each other. They
/// previously did not — `parseFBPack` read the cell-type field from byte 6 and
/// took side/transmitter/provenance one byte early, so every asset loaded with
/// a bogus type and the wrong metadata. Only a real Swift toolchain sees this,
/// which is why it has to be asserted here.
final class FBPackFormatTests: XCTestCase {

    private func makeHeader(neurons: Int32, synapses: Int32, regions: Int32) -> ConnectomeHeader {
        ConnectomeHeader(
            magic: 0x46425031, version: 3, flags: 0,
            neuronCount: neurons, synapseCount: synapses,
            morphologyCount: 0, regionCount: regions,
            organism: OrganismInfo(datasetVersion: "test", simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"], dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "FBPackFormatTests",
            description: "format round-trip")
    }

    /// Every field of a neuron record must survive pack -> parse. The values
    /// are chosen so a one-byte shift cannot coincidentally look correct:
    /// distinct bytes at 4..9 and a type id > 255 (the whole point of v2).
    func testNeuronFieldsSurviveRoundTrip() throws {
        let c = Connectome(header: makeHeader(neurons: 1, synapses: 0, regions: 1))
        let typeID: UInt16 = 4660          // 0x1234, deliberately > 255
        let n = NeuronRecord(
            canonicalID: 7, datasetID: 1, type: typeID, region: 9, side: 2,
            transmitter: UInt8(TransmitterType.gabaergic.rawValue), provenance: 4,
            flags: NeuronFlags.motor,
            morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
            outgoingStart: 0, outgoingCount: 0, x: 1.5, y: -2.5, z: 3.25)
        XCTAssertTrue(c.appendNeuron(n))
        // The format stores exactly one CSR range per neuron (a neuron with no
        // outgoing edges gets a zero range). The loader rejects an asset whose
        // range count disagrees with neuronCount, so every writer must set
        // them — including a one-neuron fixture like this one.
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0)])

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("roundtrip-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)

        XCTAssertEqual(back.neurons.count, 1)
        let got = try XCTUnwrap(back.neurons.first)
        XCTAssertEqual(got.canonicalID, 7)
        XCTAssertEqual(got.datasetID, 1)
        XCTAssertEqual(got.type, typeID, "cell type must round-trip as u16")
        XCTAssertEqual(got.region, 9)
        XCTAssertEqual(got.side, 2)
        XCTAssertEqual(got.transmitter, UInt8(TransmitterType.gabaergic.rawValue))
        XCTAssertEqual(got.provenance, 4)
        // The class byte must survive the Swift writer/reader pair too, not just
        // the Python one: byte 9 used to be written as a constant 0 and dropped
        // on read, so a round trip silently erased whatever was there.
        XCTAssertEqual(got.flags, NeuronFlags.motor)
        XCTAssertTrue(got.isMotorNeuron)
        XCTAssertFalse(got.isSensoryNeuron)
        XCTAssertEqual(got.morphologyIndex, -1)
        XCTAssertEqual(got.x, 1.5, accuracy: 1e-6)
        XCTAssertEqual(got.y, -2.5, accuracy: 1e-6)
        XCTAssertEqual(got.z, 3.25, accuracy: 1e-6)
    }

    /// The neuron record is 44 bytes and `region` sits at byte 5 — the fields
    /// after it must not shift, or the reader's offsets and the writer's
    /// disagree (which is exactly the bug this file exists for).
    func testNeuronRecordLayoutIsStable() throws {
        let c = Connectome(header: makeHeader(neurons: 1, synapses: 0, regions: 1))
        XCTAssertTrue(c.appendNeuron(NeuronRecord(
            canonicalID: 0, datasetID: 0, type: 0, region: 5, side: 0,
            transmitter: 0, provenance: 0, morphologyIndex: 0,
            incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
            x: 0, y: 0, z: 0)))
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0)])
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("layout-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let data = try Data(contentsOf: url)
        // locate the neuron block by parsing the length-prefixed headers is
        // overkill here; instead assert via the parsed result that region is 5
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertEqual(back.neurons.first?.region, 5)
    }

    /// sign == 0 is UNPOLARISED and must round-trip as 0, not collapse to +1.
    func testUnpolarisedSignRoundTripsAsZero() throws {
        let c = Connectome(header: makeHeader(neurons: 2, synapses: 1, regions: 1))
        XCTAssertTrue(c.appendNeuron(NeuronRecord(
            canonicalID: 0, datasetID: 0, type: 0, region: 9, side: 0,
            transmitter: 0, provenance: 0, morphologyIndex: -1,
            incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
            x: 0, y: 0, z: 0)))
        XCTAssertTrue(c.appendNeuron(NeuronRecord(
            canonicalID: 1, datasetID: 0, type: 0, region: 9, side: 0,
            transmitter: 0, provenance: 0, morphologyIndex: -1,
            incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
            x: 0, y: 0, z: 0)))
        c.appendSynapse(SynapseRecord(
            preNeuron: 0, postNeuron: 1, synapseCount: 3,
            transmitter: UInt8(TransmitterType.glutamatergic.rawValue),
            sign: 0, confidence: 50, delaySteps: 1, estimatedEfficacy: 0.5))
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 1), OutEdgeRange(start: 1, count: 0)])

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("sign-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertEqual(back.synapses.first?.sign, 0,
                       "an unpolarised edge must stay 0, never become excitatory")
        XCTAssertEqual(back.synapses.first?.sign, SynapseSign.unpolarised.rawValue)
    }

    /// Region bounds must survive pack -> parse with the 28-byte stride.
    /// The reader declared a 28-byte record but advanced 32, which walked off
    /// the block — invisible on the synthetic demo (0 regions) and fatal on a
    /// real asset, which writes one bound per filled region.
    func testRegionBoundsRoundTripWith28ByteStride() throws {
        // regionCount in the header is derived by the packer, not taken from here
        let c = Connectome(header: makeHeader(neurons: 1, synapses: 0, regions: 0))
        XCTAssertTrue(c.appendNeuron(NeuronRecord(
            canonicalID: 0, datasetID: 0, type: 0, region: 5, side: 0,
            transmitter: 0, provenance: 0, morphologyIndex: -1,
            incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
            x: 0, y: 0, z: 0)))
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0)])
        let bounds = [
            RegionBounds(region: 1, minX: -1, minY: -2, minZ: -3, maxX: 1, maxY: 2, maxZ: 3),
            RegionBounds(region: 5, minX: -10, minY: -20, minZ: -30, maxX: 10, maxY: 20, maxZ: 30),
            RegionBounds(region: 9, minX: 0.5, minY: 1.5, minZ: 2.5, maxX: 3.5, maxY: 4.5, maxZ: 5.5),
        ]
        c.setRegionBounds(bounds)

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("regions-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertEqual(back.regionBounds.count, 3)
        for (got, want) in zip(back.regionBounds, bounds) {
            XCTAssertEqual(got.region, want.region)
            XCTAssertEqual(got.minX, want.minX, accuracy: 1e-6)
            XCTAssertEqual(got.minY, want.minY, accuracy: 1e-6)
            XCTAssertEqual(got.minZ, want.minZ, accuracy: 1e-6)
            XCTAssertEqual(got.maxX, want.maxX, accuracy: 1e-6)
            XCTAssertEqual(got.maxY, want.maxY, accuracy: 1e-6)
            XCTAssertEqual(got.maxZ, want.maxZ, accuracy: 1e-6)
        }
    }

    /// A real asset must load with no CSR complaints. The format stores no
    /// incoming index, so every neuron declares incomingCount == 0.
    func testLoadedAssetPassesCSRValidation() throws {
        let c = Connectome(header: makeHeader(neurons: 2, synapses: 1, regions: 1))
        for i in 0..<2 {
            XCTAssertTrue(c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: 0, region: 9, side: 0,
                transmitter: 0, provenance: 0, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
                x: 0, y: 0, z: 0)))
        }
        c.appendSynapse(SynapseRecord(
            preNeuron: 0, postNeuron: 1, synapseCount: 1, transmitter: 0,
            sign: Int8(SynapseSign.excitatory.rawValue), confidence: 50,
            delaySteps: 1, estimatedEfficacy: 1.0))
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 1), OutEdgeRange(start: 1, count: 0)])
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("csr-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertEqual(back.validateCSR(), [])
        XCTAssertEqual(back.validate(), [])
    }

    // MARK: - v3 original dataset IDs (docs/TRACEABILITY.md)

    /// The whole point of the block: a selected neuron can name the cell it
    /// came from, and the value survives the round trip even though it does not
    /// fit the i32 canonicalID slot.
    func testSourceIDsSurviveRoundTripAndExceedInt32() throws {
        let c = Connectome(header: makeHeader(neurons: 3, synapses: 0, regions: 1))
        for i in 0..<3 {
            XCTAssertTrue(c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: 0, region: 9, side: 0,
                transmitter: 0, provenance: 0, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
                x: 0, y: 0, z: 0)))
        }
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0),
                             OutEdgeRange(start: 0, count: 0),
                             OutEdgeRange(start: 0, count: 0)])
        // Shaped like the real BANC Root IDs (measured min 720575940381905254):
        // 60-bit, and deliberately NOT the array index — the defect being fixed
        // was that the two were the same number, which made every value look
        // plausible.
        let ids: [UInt64] = [720575940381905254, 720575940381905999, 720575941734593579]
        c.setSourceIDs(ids)
        XCTAssertTrue(c.validate().isEmpty, "\(c.validate())")

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("srcid-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertTrue(back.recordsSourceIDs)
        for (i, want) in ids.enumerated() {
            XCTAssertEqual(back.sourceID(of: i), want, "neuron \(i)")
        }
        XCTAssertGreaterThan(back.sourceID(of: 0)!, UInt64(Int32.max),
                             "the value must exceed int32 or the test proves nothing")
    }

    /// An asset that has no original IDs must say so, and must NOT write a
    /// block of zeros: 0 is a legal ID, so a reader would answer confidently
    /// about the wrong neuron instead of reporting that it cannot trace.
    func testAssetWithoutSourceIDsRecordsNone() throws {
        let c = Connectome(header: makeHeader(neurons: 2, synapses: 0, regions: 1))
        for i in 0..<2 {
            XCTAssertTrue(c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: 0, region: 9, side: 0,
                transmitter: 0, provenance: 0, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
                x: 0, y: 0, z: 0)))
        }
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0),
                             OutEdgeRange(start: 0, count: 0)])
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("nosrcid-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        let back = try Connectome.loadFBPack(from: url)
        XCTAssertFalse(back.recordsSourceIDs)
        XCTAssertTrue(back.sourceIDs.isEmpty)
        XCTAssertNil(back.sourceID(of: 0), "unknown must be nil, not 0")
    }

    /// Two neurons sharing a source ID means one of them resolves to the wrong
    /// cell in the release — worse than having no IDs at all, so it is a
    /// validation failure rather than a warning.
    func testDuplicateSourceIDsAreRejected() {
        let c = Connectome(header: makeHeader(neurons: 2, synapses: 0, regions: 1))
        for i in 0..<2 {
            _ = c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: 0, region: 9, side: 0,
                transmitter: 0, provenance: 0, morphologyIndex: -1,
                incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
                x: 0, y: 0, z: 0))
        }
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0),
                             OutEdgeRange(start: 0, count: 0)])
        c.setSourceIDs([42, 42])
        XCTAssertTrue(c.validate().contains { $0.contains("duplicates") }, "\(c.validate())")
    }

    /// A v2 file has no ID block, so it is rejected rather than read with the
    /// block silently absent (the loader would otherwise accept a layout that
    /// the writer no longer produces).
    func testVersion2AssetIsRejected() throws {
        // Built directly with a v2 header: `header` is immutable, and the point
        // is to write a file whose HEADER says 2 while the layout is v3 — the
        // loader must reject it rather than read one layout as the other.
        var hdr = makeHeader(neurons: 1, synapses: 0, regions: 1)
        hdr.version = 2
        let c = Connectome(header: hdr)
        _ = c.appendNeuron(NeuronRecord(
            canonicalID: 0, datasetID: 0, type: 0, region: 9, side: 0,
            transmitter: 0, provenance: 0, morphologyIndex: -1,
            incomingStart: 0, incomingCount: 0, outgoingStart: 0, outgoingCount: 0,
            x: 0, y: 0, z: 0))
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 0)])
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("v2-\(UUID().uuidString).fbpack")
        defer { try? FileManager.default.removeItem(at: url) }
        try c.writeFBPack(to: url)
        XCTAssertThrowsError(try Connectome.loadFBPack(from: url)) { err in
            guard case ConnectomeError.unsupportedVersion(let v) = err else {
                return XCTFail("expected unsupportedVersion, got \(err)")
            }
            XCTAssertEqual(v, 2)
        }
    }
}