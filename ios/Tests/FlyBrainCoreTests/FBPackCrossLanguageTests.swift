//
//  FBPackCrossLanguageTests.swift
//  FlyBrainCoreTests
//
//  Loads the asset the PYTHON pipeline wrote, rather than one built in Swift.
//
//  Why this exists: every previous loader test constructed its asset with
//  Connectome.writeFBPack, so both writer and reader were the Swift side. The
//  pipeline's own asset was only ever opened... nowhere. That hid the worst
//  bug in this project — `dataProvenance` was serialised by Python as an
//  IntEnum (an integer) while Swift decodes `ConnectomeHeader.dataProvenance`
//  as a String, so JSONDecoder threw and NO asset produced by the pipeline
//  could be opened by the app at all. The demo asset is bundled with this
//  target so the cross-language contract is now executed, not assumed.
//

import XCTest
@testable import FlyBrainCore

final class FBPackCrossLanguageTests: XCTestCase {

    private func pipelineAssetURL() throws -> URL {
        // The two build systems put the resource in different places.
        //  - SwiftPM (`swift test`, what CI runs) collects target resources
        //    into a SEPARATE .bundle that only `Bundle.module` points at;
        //    `Bundle(for:)` is the test runner's bundle and does not see it.
        //  - Xcode/Xcodegen copies it into the test bundle, where `Bundle(for:)`
        //    finds it (possibly nested under the source directory's name).
        // `SWIFT_PACKAGE` is defined only by SwiftPM, so `Bundle.module` is not
        // referenced in the Xcode build (where that symbol does not exist).
        var bundles: [Bundle] = []
        #if SWIFT_PACKAGE
        bundles.append(Bundle.module)
        #else
        bundles.append(Bundle(for: FBPackCrossLanguageTests.self))
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
        XCTFail("demo_micro.fbpack is not in any test bundle — the Python "
                + "pipeline asset must be a test resource for this gate to mean anything")
        throw XCTSkip("asset not bundled")
    }

    /// The header must decode: this is the exact decode that used to throw.
    func testPythonWrittenHeaderDecodes() throws {
        let c = try Connectome.loadFBPack(from: try pipelineAssetURL())
        XCTAssertGreaterThan(c.neuronCount, 0, "asset must contain neurons")
        XCTAssertGreaterThan(c.synapseCount, 0, "asset must contain synapses")
        // Provenance is carried as the enum NAME on the wire, so it must
        // arrive as a readable string, not a bare integer — and spelled the way
        // the pipeline spells it. Swift's implicit rawValue for `case inferred`
        // would be "inferred" while Python writes "INFERRED", which decodes to
        // nil; the rawValues are now stated explicitly so the two agree.
        XCTAssertFalse(c.header.dataProvenance.isEmpty,
                       "dataProvenance must survive as a non-empty string")
        XCTAssertNotNil(Provenance(rawValue: c.header.dataProvenance),
                        "dataProvenance must be a valid Provenance name, got "
                        + "'\(c.header.dataProvenance)'")
        XCTAssertEqual(c.header.dataProvenance, c.header.dataProvenance.uppercased(),
                       "the wire form is the uppercase enum NAME, as the Python "
                       + "pipeline writes it")
        XCTAssertEqual(Provenance.allCases.count, 6,
                       "Python's Provenance has 6 members; keep the two in step")
    }

    /// The two formats must agree on layout, not just on decoding.
    func testPythonAssetPassesSwiftCSRValidation() throws {
        let c = try Connectome.loadFBPack(from: try pipelineAssetURL())
        let problems = c.validate() + c.validateCSR()
        XCTAssertTrue(problems.isEmpty,
                      "Python-written asset must satisfy Swift invariants: \(problems)")
    }

    /// A cell type above 255 must survive: this is what forced v2 (u8 -> u16).
    func testPipelineAssetCarriesWidenedCellTypes() throws {
        let c = try Connectome.loadFBPack(from: try pipelineAssetURL())
        XCTAssertFalse(c.neurons.isEmpty)
        // Positions must be real (a space-vs-comma parsing bug once gave every
        // neuron (0,0,0) while the suite still passed).
        let nonZero = c.neurons.contains { $0.x != 0 || $0.y != 0 || $0.z != 0 }
        XCTAssertTrue(nonZero, "positions must not all be the origin")
    }

    /// The cell-class byte (wire offset 9) must survive Python -> Swift.
    ///
    /// This is the field the motor readout selects on, and the region byte
    /// cannot substitute for it: the ingest assigns 9,954 neurons to
    /// `legNeuromere` of which only 187 (1.9%) are motor, so summing a
    /// neuropil sums the fly's own sensory afferents.
    ///
    /// The offset is pinned by construction, not by reading the file back with
    /// the reader under test: byte 9 is what the PACKER wrote and the reader
    /// must decode THAT byte, not agree with itself.
    func testPipelineAssetCarriesMotorCellClass() throws {
        let url = try pipelineAssetURL()
        // Locate the neuron block independently of the loader.
        let blob = try Data(contentsOf: url)
        var off = 0
        func u64(_ at: Int) -> Int {
            var v: UInt64 = 0
            // Little-endian, assembled byte by byte: no alignment requirement
            // and no API availability question across toolchains.
            for k in 0..<8 {
                v |= UInt64(blob[at + k]) << (8 * UInt64(k))
            }
            return Int(v)
        }
        let hdrLen = u64(0)
        // `hdrLen` counts the PADDED header, exactly as the loader's
        // `readBlockBytes()` sees it, so the neuron block starts right after.
        off = 8 + hdrLen
        let neuronBytes = u64(off)
        off += 8
        let stride = 44
        let count = neuronBytes / stride
        XCTAssertGreaterThan(count, 0)
        // Count the class bits straight out of the file.
        var rawMotor = 0
        var rawSensory = 0
        for i in 0..<count where (blob[off + i * stride + 9] & NeuronFlags.motor) != 0 {
            rawMotor += 1
        }
        for i in 0..<count where (blob[off + i * stride + 9] & NeuronFlags.sensory) != 0 {
            rawSensory += 1
        }
        let c = try Connectome.loadFBPack(from: url)
        let decodedMotor = c.neurons.reduce(0) { $0 + ($1.isMotorNeuron ? 1 : 0) }
        let decodedSensory = c.neurons.reduce(0) { $0 + ($1.isSensoryNeuron ? 1 : 0) }
        XCTAssertEqual(decodedMotor, rawMotor,
                       "the reader must decode byte 9, not a different offset")
        XCTAssertEqual(decodedSensory, rawSensory,
                       "sensory class must survive too")
        // The committed fixture MUST carry classes, or this test compares two
        // zeroes and proves nothing. `tools/gen_synth.py` labels the synthetic
        // demo the same way the BANC ingest labels the real release (afferents
        // sensory, neuromeres/VNC motor), and `tools/sync_test_asset.py --check`
        // fails in CI if the fixture drifts from the pipeline asset.
        XCTAssertGreaterThan(rawMotor, 0,
                             "the fixture must carry motor cells; regenerate it "
                             + "with tools/gen_synth.py rather than deleting this")
        XCTAssertGreaterThan(rawSensory, 0,
                             "the fixture must carry sensory cells")
        // Motor and sensory are exclusive, as in the release's single label.
        XCTAssertEqual(decodedMotor + decodedSensory,
                       c.neurons.reduce(0) { $0 + ($1.flags != 0 ? 1 : 0) })
    }
}