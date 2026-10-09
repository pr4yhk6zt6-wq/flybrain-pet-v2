//
//  NeuronInspectionTests.swift
//  FlyBrainCoreTests
//
//  Covers the tap-to-inspect path: the pick must return the neuron actually
//  under the finger, and the readout must report the asset's own bytes rather
//  than a plausible-looking substitute.
//
//  Every fixture below goes through the REAL mutators (`Connectome.init(header:)`,
//  `appendNeuron`, `setOutgoingRanges`, `setSourceIDs`). The first version of
//  this file named `setNeuronsForTesting`, `NeuronInspection.pick` and
//  `NeuronInspection.inspect`, none of which exist — it was written from what
//  the API "should" look like. Building on the real ones means a rename breaks
//  this file at compile time in CI instead of being discovered by hand.
//

import XCTest
import simd
@testable import FlyBrainCore

final class NeuronInspectionTests: XCTestCase {

    // MARK: - Fixtures

    private func header(neurons: Int, regions: Int = 1) -> ConnectomeHeader {
        ConnectomeHeader(
            magic: 0x46425031, version: 3, flags: 0,
            neuronCount: Int32(neurons), synapseCount: 0,
            morphologyCount: 0, regionCount: Int32(regions),
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: Provenance.reconstructed.rawValue,
            generationDate: "now", generatedBy: "NeuronInspectionTests",
            description: "inspection fixture")
    }

    /// `count` neurons on the diagonal (i, i, i), one region, class bytes as given.
    ///
    /// The diagonal is not cosmetic. `camera()` puts the eye on +x at yaw = 0,
    /// because `RenderCamera.basis(forUp: +y)` resolves `east` to +x and the
    /// view direction at yaw = 0 is `east` — so the view axis is -x. A row along
    /// x is therefore a row ALONG THE VIEW AXIS: measured, all four of its
    /// neurons project to the same NDC point (distance 0.000e+00 apart), and
    /// "aim at neuron i, expect neuron i" would be decided by depth, not by
    /// position. `tools/probe_neuron_pick_geometry.py` measures both layouts
    /// with the camera's own matrix code; the diagonal's consecutive NDC gaps
    /// are 0.2876, 0.3595, 0.4623 at aspect 1.
    private func rowConnectome(count: Int = 4, flags: [UInt8]? = nil) -> Connectome {
        let c = Connectome(header: header(neurons: count))
        for i in 0..<count {
            _ = c.appendNeuron(NeuronRecord(
                canonicalID: Int32(i), datasetID: UInt8(DatasetID.banc.rawValue),
                type: UInt16(i),
                region: UInt8(RegionID.centralComplex.rawValue), side: 1,
                transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                provenance: TestSupport.provIndex(.reconstructed),
                flags: flags?[i] ?? 0,
                morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                outgoingStart: 0, outgoingCount: 0,
                x: Float(i), y: Float(i), z: Float(i)))
        }
        c.setOutgoingRanges((0..<count).map { _ in OutEdgeRange(start: 0, count: 0) })
        return c
    }

    private func model(_ c: Connectome) -> ConnectomeRenderModel {
        ConnectomeRenderModel(connectome: c)
    }

    /// Camera looking straight at the origin, one metre out along +x. With the
    /// default `up` the optic axis is -x (NOT -z): `basis(forUp: +y)` returns
    /// east = +x, and at yaw = 0 the view direction is that east vector, so a
    /// point at (x, y, z) projects to ndc.x proportional to y and ndc.y to z —
    /// measured in `tools/probe_neuron_pick_geometry.py`, which reports the eye
    /// as (10, 0, 0). An earlier version of this fixture assumed a -z view and
    /// placed its neurons along x, i.e. down the optic axis.
    private func camera(distance: Float = 10) -> RenderCamera {
        RenderCamera(target: SIMD3(0, 0, 0), distance: distance,
                     yaw: 0, pitch: 0, up: SIMD3(0, 1, 0),
                     verticalFovRadians: 1.0, nearPlane: 0.1, farPlane: 1000)
    }

    /// Aim at instance `i` and return its own ndc position. Uses `XCTUnwrap`
    /// rather than substituting (0,0) for a nil projection: a test that quietly
    /// aimed at the screen centre whenever a neuron failed to project would
    /// make every "nearest" assertion below meaningless.
    private func ndcOf(_ i: Int, in m: ConnectomeRenderModel,
                       cam: RenderCamera, aspect: Float) throws -> SIMD2<Float> {
        let inst = m.instances[i]
        let p = try XCTUnwrap(
            cam.viewProjection(aspect: aspect).project(SIMD3(inst.x, inst.y, inst.z)),
            "instance \(i) did not project — the fixture or the camera moved")
        return SIMD2(p.x, p.y)
    }

    // MARK: - Pick: the hit is the neuron that projects to that point

    func testPickFindsTheNeuronProjectedUnderTheCursor() throws {
        let c = rowConnectome(count: 4)
        let m = model(c)
        let cam = camera()

        for aspect in [Float(1), 1.5, 0.6] {
            for i in 0..<4 {
                let ndc = try ndcOf(i, in: m, cam: cam, aspect: aspect)
                let hit = c.neuron(nearestNDC: ndc, camera: cam, model: m,
                                   aspect: aspect, maxNDCDistance: 0.1)
                XCTAssertEqual(hit?.index, i,
                               "aspect \(aspect): aimed at \(i), picked \(String(describing: hit?.index))")
            }
        }
    }

    /// The pick must report the SAME fields the asset holds for that index —
    /// including the v3 original ID and the class label.
    func testPickCarriesTheAssetsOwnFieldsForThatIndex() throws {
        let c = rowConnectome(count: 4, flags: [NeuronFlags.motor,
                                                NeuronFlags.sensory, 0, 0])
        c.setSourceIDs([720575940381905254, 720575940381905255,
                        720575940381905256, 720575940381905257])
        let m = model(c)
        let cam = camera()

        let hit = try XCTUnwrap(c.neuron(nearestNDC: try ndcOf(1, in: m, cam: cam, aspect: 1),
                                        camera: cam, model: m, aspect: 1,
                                        maxNDCDistance: 0.1))
        XCTAssertEqual(hit.index, 1)
        XCTAssertEqual(hit.sourceID, 720575940381905255,
                       "the v3 original ID did not survive the pick")
        XCTAssertEqual(hit.cellType, 1)
        XCTAssertTrue(hit.isSensory)
        XCTAssertFalse(hit.isMotor)
        XCTAssertEqual(hit.classText, "sensory")
        XCTAssertEqual(hit.position, SIMD3(1, 1, 1))
    }

    /// A tap far from every neuron must return nil. A picker that always
    /// answers makes "I tapped the wing" indistinguishable from "I tapped
    /// empty space", which is the whole point of hit-testing.
    ///
    /// The tap is placed midway between neurons 0 and 1 — measured by
    /// `probe_neuron_pick_geometry.py` to be 0.1438 NDC from each — with a
    /// tolerance below that, so the nearest neuron is genuinely out of reach.
    /// The old version tapped (0.5, 0) in NDC, a point defined by the screen
    /// rather than by the fixture.
    func testPickReturnsNilAwayFromEveryNeuron() throws {
        let c = rowConnectome(count: 4)
        let m = model(c)
        let cam = camera()
        let a = try ndcOf(0, in: m, cam: cam, aspect: 1)
        let b = try ndcOf(1, in: m, cam: cam, aspect: 1)
        let mid = SIMD2<Float>((a.x + b.x) / 2, (a.y + b.y) / 2)
        XCTAssertNil(c.neuron(nearestNDC: mid, camera: cam, model: m,
                              aspect: 1, maxNDCDistance: 0.14),
                     "a tap 0.1438 NDC from the nearest neuron was accepted at 0.14")
        // …and the same tap IS answered just above that distance, so the nil
        // above is the tolerance working rather than the picker being broken.
        XCTAssertEqual(c.neuron(nearestNDC: mid, camera: cam, model: m,
                                aspect: 1, maxNDCDistance: 0.15)?.index, 0,
                       "the same tap must be answered once it is within reach")
    }

    /// The tolerance must mean what it says: the pick must stop answering at
    /// the NDC distance requested, not somewhere near it.
    func testToleranceIsTheNDCDistanceItClaims() throws {
        let c = rowConnectome(count: 4)
        let m = model(c)
        let cam = camera()
        let on = try ndcOf(1, in: m, cam: cam, aspect: 1)
        let limit: Float = 0.05

        var lastHit: Int? = nil
        var firstMissStep = Float.greatestFiniteMagnitude
        for step in stride(from: Float(0), through: 0.2, by: 0.002) {
            let hit = c.neuron(nearestNDC: SIMD2(on.x + step, on.y),
                               camera: cam, model: m, aspect: 1,
                               maxNDCDistance: limit)
            if let h = hit { lastHit = h.index } else { firstMissStep = step; break }
        }
        XCTAssertEqual(lastHit, 1, "the pick wandered to another neuron while stepping away")
        // stepping along one axis, so the cutoff should land on `limit` itself
        XCTAssertEqual(firstMissStep, limit, accuracy: 0.006,
                       "tolerance cut off at \(firstMissStep) NDC, not the \(limit) asked for")
    }

    /// A neuron behind the eye has no finite projection on the correct side of the
    /// camera and must never be picked. Without the frustum check it projects to
    /// the SAME NDC point as a neuron at the target, because the divide by a
    /// negative w mirrors it across the eye plane — so it would be selected
    /// instead of the cell actually in front.
    ///
    /// `camera()` sits on +x looking down -x, so "behind" means x > 10. The
    /// first version of this test used (0, 0, 500): measured in
    /// `probe_neuron_pick_geometry.py`, that is 10 units IN FRONT of the eye and
    /// merely far off-axis (ndc.x ≈ -91.5, ndc.z = 0.9901 — inside [0, 1]), so
    /// the picker rejected it by distance and the frustum check never ran. The
    /// test passed while its comment described a mechanism that was not under
    /// test.
    func testNeuronBehindTheCameraIsNeverPicked() throws {
        let c = rowConnectome(count: 2)
        _ = c.appendNeuron(NeuronRecord(
            canonicalID: 99, datasetID: UInt8(DatasetID.banc.rawValue), type: 0,
            region: UInt8(RegionID.centralComplex.rawValue), side: 0,
            transmitter: UInt8(TransmitterType.cholinergic.rawValue),
            provenance: TestSupport.provIndex(.reconstructed),
            flags: 0, morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
            outgoingStart: 0, outgoingCount: 0,
            x: 500, y: 0, z: 0))        // 490 units behind the eye at x = 10
        c.setOutgoingRanges((0..<3).map { _ in OutEdgeRange(start: 0, count: 0) })
        let m = model(c)
        let cam = camera(distance: 10)

        // The tap in the middle of the screen is where that neuron mirrors to,
        // and it is also where a real neuron (index 0, at the origin) sits — so
        // the assertion is that the FRONT cell wins, not merely that 99 loses.
        for x in stride(from: Float(0.02), through: 0.98, by: 0.06) {
            for y in stride(from: Float(0.02), through: 0.98, by: 0.06) {
                let ndc = SIMD2(x * 2 - 1, y * 2 - 1)
                let hit = c.neuron(nearestNDC: ndc, camera: cam, model: m,
                                   aspect: 1, maxNDCDistance: 2)
                XCTAssertNotEqual(hit?.index, 99,
                                  "picked the neuron behind the camera at ndc (\(ndc.x), \(ndc.y))")
            }
        }
        // Directly at the mirrored point, the cell in front must be the answer.
        XCTAssertEqual(c.neuron(nearestNDC: SIMD2(0, 0), camera: cam, model: m,
                                aspect: 1, maxNDCDistance: 2)?.index, 0,
                       "the cell behind the camera took the hit from the one in front")
    }

    /// An unusable aspect ratio must yield nil, not a division by ~0.
    func testDegenerateAspectReturnsNil() throws {
        let c = rowConnectome(count: 2)
        let m = model(c)
        let cam = camera()
        for bad in [Float(0), -1, .nan, .infinity] {
            XCTAssertNil(c.neuron(nearestNDC: SIMD2(0, 0), camera: cam, model: m,
                                  aspect: bad, maxNDCDistance: 0.5),
                         "aspect \(bad) produced a hit")
        }
    }

    // MARK: - Inspect: the panel reports the asset's own bytes

    func testInspectionReportsStoredClassByteNotARecomputedOne() throws {
        let c = rowConnectome(count: 4, flags: [NeuronFlags.motor,
                                                NeuronFlags.sensory,
                                                NeuronFlags.motor | NeuronFlags.sensory,
                                                0])
        let m = model(c)
        let texts = (0..<4).map { c.inspection(of: $0)!.classText }
        XCTAssertEqual(texts, ["motor", "sensory", "motor + sensory", "unlabelled"])
    }

    /// The class byte and the render instance's packed class bits are two
    /// encodings of the same fact (byte 9 vs `packed` bits 13..14). They must
    /// never disagree — a divergence would colour a cell as motor while the
    /// readout called it unlabelled, and both would look self-consistent.
    func testStoredClassByteAgreesWithTheRenderInstanceBits() throws {
        let flags: [UInt8] = [NeuronFlags.motor, NeuronFlags.sensory,
                              NeuronFlags.motor | NeuronFlags.sensory, 0]
        let c = rowConnectome(count: 4, flags: flags)
        let m = model(c)
        for i in 0..<4 {
            XCTAssertEqual(m.instances[i].isMotor, c.neurons[i].isMotorNeuron,
                           "instance \(i) motor bit disagrees with the stored byte")
            XCTAssertEqual(m.instances[i].isSensory, c.neurons[i].isSensoryNeuron,
                           "instance \(i) sensory bit disagrees with the stored byte")
        }
    }

    /// An asset with no class byte at all must SAY so. Reporting it as
    /// "unlabelled" states something the asset never said — and that is exactly
    /// the silent-downgrade shape this whole distinction exists to prevent.
    func testAssetWithoutAClassByteSaysSoRatherThanUnlabelled() throws {
        let c = rowConnectome(count: 1, flags: [0])
        let m = model(c)
        XCTAssertFalse(c.hasCellClasses,
                       "a fixture with flags == 0 everywhere must not claim cell classes")
        let text = c.inspection(of: 0)!.classText
        XCTAssertNotEqual(text, "unlabelled",
                          "an asset with no class byte was reported as 'unlabelled'")
        XCTAssertTrue(text.contains("no class byte"),
                      "expected an explicit statement of the gap, got \(text)")
    }

    /// The v3 original ID must come from the block, and where the block is
    /// absent the answer is nil — never 0, and never the array index. Zero is a
    /// legal release ID, so printing it would be a lie that looks like data.
    func testOriginalIDIsNilWhenTheAssetHasNoBlock() throws {
        let withIDs = rowConnectome(count: 2)
        withIDs.setSourceIDs([720575940381905254, 720575940381905255])
        let m1 = model(withIDs)
        XCTAssertEqual(withIDs.inspection(of: 1)?.sourceID, 720575940381905255)
        XCTAssertEqual(withIDs.inspection(of: 0)?.sourceID, 720575940381905254)

        let bare = rowConnectome(count: 2)
        let m2 = model(bare)
        XCTAssertNil(bare.inspection(of: 1)?.sourceID,
                     "an asset with no ID block must report nil, not a substitute")
        XCTAssertFalse(bare.inspection(of: 0)!.isTraceable)
        XCTAssertTrue(withIDs.inspection(of: 0)!.isTraceable)
    }

    /// The text shown for an un-ID'd neuron must not look like an ID.
    func testTraceabilityTextDistinguishesMissingFromPresent() throws {
        let withIDs = rowConnectome(count: 1)
        withIDs.setSourceIDs([720575940381905254])
        let m1 = model(withIDs)
        let present = withIDs.inspection(of: 0)!.traceabilityText
        XCTAssertTrue(present.contains("720575940381905254"), "got \(present)")

        let bare = rowConnectome(count: 1)
        let m2 = model(bare)
        let missing = bare.inspection(of: 0)!.traceabilityText
        XCTAssertFalse(missing.contains("720575940381905254"))
        XCTAssertTrue(missing.contains("no original ID"), "got \(missing)")
        XCTAssertFalse(missing.contains(" #0"), "a missing ID was rendered as 0: \(missing)")
    }

    /// Out-of-range indices must return nil rather than trapping: a reload can
    /// swap in a smaller connectome while the picker still holds an old index.
    func testOutOfRangeIndexesReturnNil() throws {
        let c = rowConnectome(count: 2)
        let m = model(c)
        XCTAssertNil(c.inspection(of: -1))
        XCTAssertNil(c.inspection(of: 2))
        XCTAssertNil(c.inspection(of: 10_000))
    }

    /// The inspection's index must address the same neuron in the model and in
    /// the connectome, or the panel describes a different cell than the dot the
    /// user touched.
    func testInspectionIndexAgreesBetweenModelAndConnectome() throws {
        let c = rowConnectome(count: 3)
        let m = model(c)
        for i in 0..<3 {
            let info = try XCTUnwrap(c.inspection(of: i))
            XCTAssertEqual(info.index, i)
            XCTAssertEqual(info.index, Int(m.instances[i].index))
            XCTAssertEqual(info.position, SIMD3(c.neurons[i].x, c.neurons[i].y,
                                                c.neurons[i].z))
            XCTAssertEqual(info.region, c.neurons[i].region)
            XCTAssertEqual(info.outgoingEdgeCount, c.outgoingRange(of: i).count)
        }
    }

    /// The class byte is the release's own `Super Class` label. It is NOT a
    /// region-derived guess, and the ingest does not derive it from anything
    /// else (checked: `python/flybrain/banc.py` writes `flags` from
    /// `neuron_flags(super_class)` alone, while `provenance` is the constant
    /// `RECONSTRUCTED` for every BANC neuron — so the two are free to disagree,
    /// and neither may be used to predict the other).
    ///
    /// This test states that as a negation: a fixture whose neurons are all in
    /// ONE region can still carry every class combination. If someone later
    /// decides to colour cells by region and re-derive the class, this fails.
    func testClassByteIsNotDerivedFromRegion() throws {
        let all = [NeuronFlags.motor, NeuronFlags.sensory,
                   NeuronFlags.motor | NeuronFlags.sensory, 0]
        let c = rowConnectome(count: 4, flags: all)
        let regions = Set(c.neurons.map { $0.region })
        XCTAssertEqual(regions.count, 1,
                       "fixture must sit in a single region for this to mean anything")
        let texts = (0..<4).map { try? c.inspection(of: $0)?.classText }
        XCTAssertEqual(texts.count, Set(texts.map { $0 ?? "" }).count,
                       "four neurons in one region must not collapse to one class")
    }
}