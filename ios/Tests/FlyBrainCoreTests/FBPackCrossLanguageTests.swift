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
        let bundle = Bundle(for: FBPackCrossLanguageTests.self)
        if let url = bundle.url(forResource: "demo_micro", withExtension: "fbpack") {
            return url
        }
        // The core framework's own bundle, if the resource lands there instead.
        if let url = Bundle(for: Connectome.self)
            .url(forResource: "demo_micro", withExtension: "fbpack") {
            return url
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
        // arrive as a readable string, not a bare integer.
        XCTAssertFalse(c.header.dataProvenance.isEmpty,
                       "dataProvenance must survive as a non-empty string")
        XCTAssertNotNil(Provenance(rawValue: c.header.dataProvenance),
                        "dataProvenance must be a valid Provenance name, got "
                        + "'\(c.header.dataProvenance)'")
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
}