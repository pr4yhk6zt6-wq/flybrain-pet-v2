import XCTest
@testable import FlyBrainCore

/// The render model is the only part of the drawing path that can be tested
/// without a GPU, so it carries the tests that would otherwise be "verify it
/// looks right on a device": that the packed word survives every enum value,
/// that the buffer layout is what the shader is told it is, and that nothing
/// on screen is invented.
final class ConnectomeRenderModelTests: XCTestCase {

    // MARK: - Packed word

    /// Measured on `Types.swift`: RegionID has 21 cases with raw values 0...20.
    /// Four bits hold 0...15, so an 8-bit region field packed as 4 bits would
    /// map `endocrineVisceral` (20) onto `medulla` (4) with no error anywhere —
    /// two anatomically unrelated regions sharing one colour. This test exists
    /// because that is exactly the mistake that was in the first draft.
    func testEveryRegionSurvivesThePackedWord() {
        // Measured in Types.swift: 21 cases, highest raw value 20. The field
        // is 5 bits, so at 32 cases it would start wrapping and two
        // anatomically unrelated regions would share a colour silently.
        XCTAssertEqual(RegionID.allCases.count, 21,
                       "RegionID changed size: the packed field is 5 bits, so "
                       + "re-derive its width before trusting this test")
        XCTAssertEqual(Int(RegionID.allCases.map(\.rawValue).max() ?? 0), 20,
                       "the highest RegionID raw value moved past the 5-bit "
                       + "field")
        for r in RegionID.allCases {
            let inst = NeuronInstance(x: 0, y: 0, z: 0,
                                      region: UInt8(r.rawValue), side: 0,
                                      provenance: 0, isMotor: false,
                                      isSensory: false, typeIndex: 0, index: 0)
            XCTAssertEqual(Int(inst.region), r.rawValue,
                           "region \(r) did not survive the round trip")
        }
    }

    func testEveryProvenanceIndexSurvivesThePackedWord() {
        for (i, p) in Provenance.allCases.enumerated() {
            let inst = NeuronInstance(x: 0, y: 0, z: 0, region: 0, side: 0,
                                      provenance: TestSupport.provIndex(p),
                                      isMotor: false, isSensory: false,
                                      typeIndex: 0, index: 0)
            XCTAssertEqual(Int(inst.provenance), i,
                           "provenance \(p) (index \(i)) did not survive")
        }
    }

    func testAllSidesAndClassBitsSurvive() {
        for side in UInt8(0)...UInt8(2) {
            for motor in [false, true] {
                for sensory in [false, true] {
                    let inst = NeuronInstance(x: 0, y: 0, z: 0, region: 0,
                                              side: side, provenance: 0,
                                              isMotor: motor, isSensory: sensory,
                                              typeIndex: 0, index: 0)
                    XCTAssertEqual(inst.side, side)
                    XCTAssertEqual(inst.isMotor, motor)
                    XCTAssertEqual(inst.isSensory, sensory)
                }
            }
        }
    }

    /// A type index beyond the u16 vocabulary must not bleed into the fields
    /// packed beside it. The shipped asset spans 0...11,565, so u16 is the
    /// right width, but the packer takes a UInt16 and the test passes the
    /// largest value it can hold.
    func testTypeIndexDoesNotBleedIntoNeighbouringFields() {
        let inst = NeuronInstance(x: 0, y: 0, z: 0, region: 20, side: 2,
                                  provenance: 5, isMotor: true, isSensory: true,
                                  typeIndex: UInt16.max, index: UInt32.max)
        XCTAssertEqual(Int(inst.region), 20)
        XCTAssertEqual(inst.side, 2)
        XCTAssertEqual(Int(inst.provenance), 5)
        XCTAssertTrue(inst.isMotor)
        XCTAssertTrue(inst.isSensory)
        XCTAssertEqual(inst.typeIndex, UInt32(UInt16.max))
        XCTAssertEqual(inst.index, UInt32.max)
    }

    /// The shader is told the stride and the member offsets. A Swift struct of
    /// 4-byte members only is used precisely so those numbers are the obvious
    /// ones; assert them, because a silent mismatch here draws garbage with no
    /// error on either side and there is no GPU in CI to notice.
    func testBufferLayoutIsWhatTheShaderIsTold() throws {
        XCTAssertEqual(MemoryLayout<NeuronInstance>.stride, 32,
                       "NeuronInstance stride changed: update the shader struct "
                       + "and this test together")
        XCTAssertEqual(MemoryLayout<NeuronInstance>.size, 32)
        XCTAssertEqual(MemoryLayout<NeuronActivity>.stride, 16,
                       "NeuronActivity stride changed: update the shader struct")
        XCTAssertEqual(MemoryLayout<NeuronActivity>.size, 16)
        // Every member is 4 bytes, so offsets are 4 bytes apart with no padding.
        // The offsets are MEASURED, and compared against the table that the
        // comment next to the struct documents — so this fails if either Swift's
        // layout changes or the comment drifts from it.
        let measured = measuredOffsets()
        for (name, documented) in [("x", 0), ("y", 4), ("z", 8), ("packed", 12),
                                   ("typeIndex", 16), ("index", 20),
                                   ("_pad0", 24), ("_pad1", 28)] {
            let actual = try XCTUnwrap(measured[name], "no member named \(name)")
            XCTAssertEqual(actual, documented,
                           "\(name) is at byte \(actual), but the struct's "
                           + "size/offset table — which the Metal shader is "
                           + "written against — says \(documented)")
        }
    }

    /// The two reserved words must be zero, and the reason is not cosmetic.
    ///
    /// `NeuronInstance` deliberately has no transmitter field, and the packed
    /// word's bits above 15 are free — so the obvious way to add
    /// neurotransmitter colouring later is to drop the transmitter code into
    /// `_pad1`, whose default makes that edit compile without touching `init`.
    /// The Metal struct declares both words reserved, so a shader written
    /// against this layout would silently read a word it never expected. If
    /// transmitter colouring is wanted, give it a named field and move the
    /// stride table with it; until then this asserts the spare words are spare.
    func testReservedWordsStayZero() {
        let n = NeuronInstance(x: 1, y: 2, z: 3, region: 0, side: 0,
                               provenance: 0, isMotor: false, isSensory: false,
                               typeIndex: 7, index: 9)
        XCTAssertEqual(n._pad0, 0)
        XCTAssertEqual(n._pad1, 0)
    }

    // MARK: - Model construction

    func testModelCarriesEveryNeuronAndItsRealCoordinates() {
        let c = TestSupport.chainConnectome(count: 8)
        let m = ConnectomeRenderModel(connectome: c)
        XCTAssertEqual(m.neuronCount, 8)
        XCTAssertEqual(m.synapseCount, c.synapses.count)
        // chainConnectome places neuron i at x = i. Nothing in the model may
        // transform, jitter or reorder that.
        for (i, inst) in m.instances.enumerated() {
            XCTAssertEqual(inst.x, Float(i), "neuron \(i) moved")
            XCTAssertEqual(inst.index, UInt32(i))
            XCTAssertEqual(inst.region, UInt8(c.neurons[i].region))
        }
    }

    func testBoundsAreTheNeuronsOwnExtentNotAConstant() {
        let c = TestSupport.chainConnectome(count: 8)
        let m = ConnectomeRenderModel(connectome: c)
        XCTAssertEqual(m.bounds.minX, 0)
        XCTAssertEqual(m.bounds.maxX, 7)
        XCTAssertGreaterThan(m.bounds.radius, 0)
    }

    func testEmptyConnectomeProducesAnEmptyModelRatherThanACrash() {
        let c = TestSupport.chainConnectome(count: 0)
        let m = ConnectomeRenderModel(connectome: c)
        XCTAssertEqual(m.neuronCount, 0)
        XCTAssertTrue(m.bounds.isEmpty)
        XCTAssertEqual(m.bounds.radius, 0)
    }

    /// The asset's class byte is what tells the user whether they are looking at
    /// MEASURED cell classes or at a region-only guess. The model reports the
    /// number of classified cells so the legend can say which, instead of
    /// colouring everything as though it were classified.
    func testClassifiedCountMatchesTheFlagsActuallyPresent() {
        let c = TestSupport.pairedClassConnectome(motorFlags: 0, sensoryFlags: 0)
        XCTAssertEqual(ConnectomeRenderModel(connectome: c).classifiedCount, 0,
                       "an asset with no class byte must report zero classified")

        let c2 = TestSupport.pairedClassConnectome(motorFlags: 0b1,
                                                   sensoryFlags: 0b10)
        let m2 = ConnectomeRenderModel(connectome: c2)
        XCTAssertGreaterThan(m2.classifiedCount, 0)
        XCTAssertLessThanOrEqual(m2.classifiedCount, c2.neurons.count)
    }

    // MARK: - Activity: nothing on screen may be invented

    func testIntensityIsZeroForZeroAndStrictlyIncreasing() {
        XCTAssertEqual(RenderActivity.intensity(rateHz: 0), 0,
                       "a silent neuron must look silent")
        XCTAssertEqual(RenderActivity.intensity(rateHz: -1), 0)
        let samples: [Float] = [0.5, 1, 10, 50, 99, 100]
        var last: Float = -1
        for r in samples {
            let v = RenderActivity.intensity(rateHz: r)
            XCTAssertGreaterThan(v, last, "not monotonic at \(r) Hz")
            last = v
        }
        XCTAssertEqual(RenderActivity.intensity(rateHz: 100), 1, accuracy: 1e-6)
        XCTAssertEqual(RenderActivity.intensity(rateHz: 1000), 1,
                       "above the reference it clamps rather than exceeding 1")
    }

    /// The snapshot must contain nothing that is not firing. A decorative
    /// baseline or an idle shimmer would appear here as entries with rate 0.
    func testSnapshotContainsOnlyNeuronsTheEngineReportsAsActive() {
        let c = TestSupport.chainConnectome(count: 12)
        let engine = NeuralEngine(connectome: c)
        let quiet = RenderActivity.snapshot(engine: engine)
        XCTAssertTrue(quiet.isEmpty,
                      "a fresh engine is silent, so the frame must be empty; "
                      + "got \(quiet.count) entries")

        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 0,
                               pulses: 6, intervalMs: 1, current: 40)
        for _ in 0..<200 { engine.step() }
        let active = RenderActivity.snapshot(engine: engine)
        let activeSet = Set(engine.firingActiveNeurons)
        for a in active {
            XCTAssertGreaterThan(a.rateHz, 0, "reported a zero-rate neuron")
            XCTAssertTrue(activeSet.contains(Int32(a.index)),
                          "reported a neuron the engine does not list as active")
        }
    }

    /// The renderer is a pure view: reading a frame must not advance the
    /// simulation or disturb the engine's own accounting.
    func testSnapshotDoesNotDisturbTheEngine() {
        let c = TestSupport.chainConnectome(count: 12)
        let engine = NeuralEngine(connectome: c)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 0,
                               pulses: 6, intervalMs: 1, current: 40)
        for _ in 0..<50 { engine.step() }
        let before = engine.currentTimeMs
        let rateBefore = engine.rateHz(of: 0)
        _ = RenderActivity.snapshot(engine: engine)
        _ = RenderActivity.snapshot(engine: engine)
        XCTAssertEqual(engine.currentTimeMs, before)
        XCTAssertEqual(engine.rateHz(of: 0), rateBefore)
    }
}

/// Real member offsets of a Swift struct, measured at runtime.
///
/// A previous version of this helper returned a hard-coded table of offsets, so
/// the "layout" test was asserting those numbers against themselves — it would
/// have passed with the fields in any order and would never have caught padding.
/// That is the defect this file exists to prevent, so measure instead:
/// `MemoryLayout.offset(of:)` is a real API and takes the key path directly.
/// `MemoryLayout.offset(of:)` returns `Int?` (nil only for a member the runtime
/// cannot locate, which cannot happen for a stored property of a fixed-layout
/// struct). The optionals are unwrapped in the test with `XCTUnwrap`, so a nil
/// is a failure rather than a missing key.
private func measuredOffsets() -> [String: Int?] {
    var offsets: [String: Int?] = [:]
    offsets["x"] = MemoryLayout<NeuronInstance>.offset(of: \.x)
    offsets["y"] = MemoryLayout<NeuronInstance>.offset(of: \.y)
    offsets["z"] = MemoryLayout<NeuronInstance>.offset(of: \.z)
    offsets["packed"] = MemoryLayout<NeuronInstance>.offset(of: \.packed)
    offsets["typeIndex"] = MemoryLayout<NeuronInstance>.offset(of: \.typeIndex)
    offsets["index"] = MemoryLayout<NeuronInstance>.offset(of: \.index)
    offsets["_pad0"] = MemoryLayout<NeuronInstance>.offset(of: \._pad0)
    offsets["_pad1"] = MemoryLayout<NeuronInstance>.offset(of: \._pad1)
    return offsets
}