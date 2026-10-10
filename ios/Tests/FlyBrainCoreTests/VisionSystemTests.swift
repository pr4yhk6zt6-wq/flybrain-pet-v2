//
//  VisionSystemTests.swift
//  FlyBrainCoreTests
//
//  Phase 3 — the looming channel (spec #11, #23; VALIDATION scenario 04
//  "sudden looming → escape"). These tests exist because the file header and
//  PLAN.md both claimed looming was implemented while `computeLooming()`
//  returned nil: the claim had no test, so nothing could catch it.
//
//  The distinction the tests enforce is between a body that is PRESENT and a
//  body that is APPROACHING. A static scene, however cluttered, produces
//  nothing; only expansion does. That is the biology (Klapoetke et al. 2017,
//  Nature 542:469, LPLC2 / radial motion opponency): a loom detector that
//  fires at "something is there" is not a loom detector.
//

import XCTest
@testable import FlyBrainCore

final class VisionSystemTests: XCTestCase {

    /// A dark sky with a body on a collision course, approaching at `speed`.
    private func approachingWorld(speed: Float, startDistance: Float = 6,
                                  radius: Float = 0.6) -> (World, MovingObject) {
        let w = World()
        w.backgroundLuminance = 0.6
        w.ambientLight = 0.4
        let body = w.addMovingObject(MovingObject(
            position: SIMD3<Float>(startDistance, 0, 0),
            velocity: SIMD3<Float>(-speed, 0, 0),
            radius: radius))
        return (w, body)
    }

    private func darkWorld() -> World {
        let w = World()
        w.backgroundLuminance = 0.6
        w.ambientLight = 0.4
        return w
    }

    private func pose() -> FlyPose { FlyPose() }

    // MARK: - The producer

    func testStaticSceneProducesNoLoom() {
        // Control: a world with everything except motion. The body is LARGE
        // and NEAR, so it covers well over `loomMinCoverage` and a detector
        // that fires on "something is there" would fire here. A control too
        // small to reach the coverage floor would prove nothing — it would be
        // silent for the wrong reason.
        let w = darkWorld()
        w.addMovingObject(MovingObject(position: SIMD3<Float>(3.0, 0.8, 0),
                                       velocity: .zero,
                                       radius: 1.5))
        let v = VisionSystem()
        v.luminanceProvider = { x, y, z in w.luminance(atX: x, y: y, z: z) }
        v.rayProvider = { o, d in w.raycast(origin: o, direction: d) }
        var looms = 0
        var peakCoverage: Float = 0
        for _ in 0..<400 {
            w.step(dtSeconds: 0.001)
            peakCoverage = max(peakCoverage, v.coverage)
            looms += v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)
                .filter { $0.pathway == .looming }.count
        }
        XCTAssertGreaterThan(peakCoverage, v.loomMinCoverage,
                             "the control body must actually cover the eye, "
                             + "or its silence means nothing")
        XCTAssertEqual(looms, 0, "a motionless body must never read as a loom")
    }

    func testApproachingBodyProducesLoom() {
        let (w, _) = approachingWorld(speed: 30)
        let v = VisionSystem()
        v.luminanceProvider = { x, y, z in w.luminance(atX: x, y: y, z: z) }
        v.rayProvider = { o, d in w.raycast(origin: o, direction: d) }
        var peak: Float = 0
        for _ in 0..<400 {
            w.step(dtSeconds: 0.001)
            for ev in v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)
            where ev.pathway == .looming {
                peak = max(peak, ev.strength)
            }
        }
        XCTAssertGreaterThan(peak, 0, "an approaching body must produce a loom")
        XCTAssertLessThanOrEqual(peak, 1)
    }

    func testRecedingBodyProducesNoLoom() {
        // The mirror image of the test above: expansion, not presence, is the
        // signal. A body moving away shrinks and must stay silent.
        let w = darkWorld()
        w.addMovingObject(MovingObject(position: SIMD3<Float>(1.5, 0, 0),
                                       velocity: SIMD3<Float>(20, 0, 0),
                                       radius: 0.6))
        let v = VisionSystem()
        v.luminanceProvider = { x, y, z in w.luminance(atX: x, y: y, z: z) }
        v.rayProvider = { o, d in w.raycast(origin: o, direction: d) }
        var looms = 0
        for _ in 0..<400 {
            w.step(dtSeconds: 0.001)
            looms += v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)
                .filter { $0.pathway == .looming }.count
        }
        XCTAssertEqual(looms, 0, "a receding body must not read as a loom")
    }

    func testStaticWallNeverReadsAsMovingBody() {
        // An obstacle occludes the background but cannot approach. The ray hit
        // it reports must be marked non-moving, or a wall would loom forever.
        let w = darkWorld()
        w.addObstacle(Obstacle(position: SIMD3<Float>(3, 0.8, 0),
                               size: SIMD3<Float>(4, 4, 4)))
        let hit = w.raycast(origin: SIMD3(0, 0.8, 0), direction: SIMD3(1, 0, 0))
        XCTAssertNotNil(hit, "the ray must strike the wall")
        XCTAssertFalse(hit!.isMovingBody,
                       "a static obstacle is never a moving body")
    }

    func testRaySeesBodyNearerThanTheLuminanceSampleRange() {
        // The reason the ray exists at all. The luminance sampler reads one
        // point at a fixed range; a body nearer than that range is stepped over
        // and the sky behind it is read, so a body filling the visual field
        // would look exactly like an empty one.
        let w = darkWorld()
        w.addMovingObject(MovingObject(position: SIMD3<Float>(2, 0.8, 0),
                                       velocity: .zero,
                                       radius: 0.5, luminance: 0))
        let hit = w.raycast(origin: SIMD3(0, 0.8, 0), direction: SIMD3(1, 0, 0))
        XCTAssertNotNil(hit)
        XCTAssertTrue(hit!.isMovingBody)
        XCTAssertLessThan(hit!.distance, 10, "the body must be hit before the "
                          + "fixed sample range, or the eye reads through it")
    }

    // MARK: - The eye must not invent a stimulus it has not seen

    func testFirstFrameIsSilentAgainstADarkWorld() {
        // The adaptation baselines used to start at a hardcoded 0.5. A fly
        // spawned into a dark, motionless world then reported contrast
        // (0 - 0.5) * 2 = -1.0 from every one of the 1824 ommatidia on its
        // first frame — a full-strength OFF edge that no part of the scene
        // caused. Measured on the shipped asset, that phantom flash drove
        // 1,384 spikes on a scene with no stimulus in it
        // (`tools/probe_rest_activity.py`); `testTheConnectomeIsSilentWithNo
        // Stimulus` is what caught it, from the other end.
        //
        // The fix seeds the baselines from the first sample, so the first frame
        // carries no edge. This test asserts the frame, not the spike count:
        // a silent frame is the cause, and the spike count is downstream.
        let w = World()
        w.backgroundLuminance = 0        // a dark scene
        w.ambientLight = 0
        let v = VisionSystem()
        v.luminanceProvider = { x, y, z in w.luminance(atX: x, y: y, z: z) }
        let first = v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)
        XCTAssertTrue(first.filter { $0.pathway == .onEdge || $0.pathway == .offEdge }.isEmpty,
                      "the seeding frame cannot be an edge: there is no before")
        // (In this scene the frame is empty altogether — nothing is lit and
        // nothing moved. That is correct, and the control below is what keeps
        // "silent" from being indistinguishable from "dead".)
    }

    func testTheSeedingFrameDoesNotDeafenLaterFrames() {
        // The control for the test above. "Silent first frame" is trivially
        // satisfied by never emitting anything, so a world that CHANGES must
        // still produce edges on the frame after the seed.
        let w = World()
        w.backgroundLuminance = 0
        w.ambientLight = 0
        let v = VisionSystem()
        v.luminanceProvider = { x, y, z in w.luminance(atX: x, y: y, z: z) }
        _ = v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)   // seed on dark
        // The scene brightens. `backgroundLuminance` is what every ommatidium
        // reads at the fixed sample range, so this is a step change the eye can
        // actually see — a point light 10 mm away would not clear the threshold
        // at all (illuminance falls as 1/(1+d²)) and the test would pass by
        // reading nothing.
        w.backgroundLuminance = 0.4
        var edges = 0
        for _ in 0..<20 {
            edges += v.sample(position: (0, 0.8, 0), pose: pose(), dt: 4)
                .filter { $0.pathway == .onEdge || $0.pathway == .offEdge }.count
        }
        XCTAssertGreaterThan(edges, 0,
                             "a scene that brightens must read as an edge; the "
                             + "seeding frame must not have made the eye blind")
    }

    // MARK: - The body actually moves

    func testWorldStepIntegratesMovingBody() {
        let w = darkWorld()
        w.addMovingObject(MovingObject(position: SIMD3<Float>(5, 0.8, 0),
                                       velocity: SIMD3<Float>(-10, 0, 0),
                                       radius: 0.6))
        XCTAssertEqual(w.movingObjects[0].position.x, 5, accuracy: 1e-5)
        w.step(dtSeconds: 0.5)
        // `addMovingObject` returns a COPY of the struct, so the world's own
        // array is the only place the integrated position exists — asserting
        // on a returned value would have tested a stale copy.
        XCTAssertEqual(w.movingObjects[0].position.x, 0, accuracy: 1e-4)
        XCTAssertEqual(w.time, 0.5, accuracy: 1e-6)
        // A body that never moves can never loom: this is the thing that makes
        // `step(dtSeconds:)` load-bearing rather than decorative.
    }

    // MARK: - The route to escape (spec #23)

    func testLoomingEventRoutesToTheEscapeChannelAtEscapeStrength() {
        let c = TestSupport.regionalConnectome()
        let s = SensoryInterface(connectome: c)
        let inputs = s.loomingInput(intensity: 1)
        XCTAssertFalse(inputs.isEmpty)
        // The generic visual path scales to 10×strength then divides by 80 in
        // the readout, i.e. 1/8 of the dedicated channel. A threat has to reach
        // the lobula plate at escape strength, not as one more edge.
        XCTAssertGreaterThan(inputs[0].current, 50)
        let region = RegionID(rawValue: Int(c.neurons[Int(inputs[0].neuron)].region))
        XCTAssertEqual(region, .lobulaPlate,
                       "the loom drive must land in the looming target region")
    }

    func testLoomDrivesEscapeThroughTheClosedLoop() {
        // End to end: a body on a collision course must drive the looming
        // target region through the real connectome. The fixture is built by
        // hand because `closedLoopConnectome` has no lobula plate at all — its
        // regions stop at the medulla, so a loom could not reach it, and a test
        // asserting "the plate fired" there would be asserting nothing.
        //
        // retinaLeft -> lobulaPlate -> legNeuromere: the same shape the 3D
        // world description implies (eye → looming target → escape motor).
        func loomConnectome(efficacy: Float = 3) -> Connectome {
            let regions: [(RegionID, Int)] = [
                (.retinaLeft, 2), (.retinaRight, 2), (.lamina, 2),
                (.lobulaPlate, 2), (.medulla, 2), (.legNeuromere, 2),
            ]
            // Collect the edge SET first and emit grouped by presynaptic
            // neuron: the CSR format needs each neuron's outgoing edges to be
            // one contiguous run, or a range points at another neuron's edges.
            var layout: [(region: RegionID, side: UInt8, ids: [Int32])] = []
            var pid: Int32 = 0
            for (region, count) in regions {
                let side: UInt8 = region == .retinaLeft ? 1
                    : (region == .retinaRight ? 2 : 0)
                var mine: [Int32] = []
                for _ in 0..<count { mine.append(pid); pid += 1 }
                layout.append((region, side, mine))
            }
            func ids(_ r: RegionID) -> [Int32] {
                layout.first { $0.region == r }?.ids ?? []
            }
            var edges: [(Int32, Int32)] = []
            // EVERY plate neuron projects to the leg pool. Wiring only the last
            // one made the fixture sensitive to which plate cell
            // `selectInputNeuron` happens to pick — it walks the region index in
            // ascending order and injects into the FIRST, so a single edge from
            // the second left the escalated drive with nowhere to go (this
            // failed in CI as "the escape route must be silent for a still
            // scene", with plate>0 but leg==still). The route must not depend on
            // an arbitrary index.
            for plate in ids(.lobulaPlate) {
                if let t = ids(.legNeuromere).first { edges.append((plate, t)) }
            }
            // leg pool self-sustaining
            let legs = ids(.legNeuromere)
            if legs.count > 1 { edges.append((legs[0], legs[1])); edges.append((legs[1], legs[0])) }

            let c = Connectome(header: ConnectomeHeader(
                magic: 0x46425031, version: 2, flags: 0,
                neuronCount: pid, synapseCount: Int32(edges.count),
                morphologyCount: 0, regionCount: 0,
                organism: OrganismInfo(datasetVersion: "test",
                                       simulatorVersion: "test",
                                       parameterProfile: "test"),
                sourceDatasets: ["synthetic-test"],
                dataProvenance: "SYNTHETIC-DEMO",
                generationDate: "now", generatedBy: "VisionSystemTests",
                description: "loom route"))
            var outgoing: [OutEdgeRange] = []
            for from in Int32(0)..<pid {
                let mine = edges.filter { $0.0 == from }
                let start = Int32(c.synapses.count)
                outgoing.append(OutEdgeRange(start: start, count: Int32(mine.count)))
                for e in mine {
                    c.appendSynapse(SynapseRecord(
                        preNeuron: e.0, postNeuron: e.1, synapseCount: 100,
                        transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                        sign: Int8(SynapseSign.excitatory.rawValue),
                        confidence: 50, delaySteps: 1, estimatedEfficacy: efficacy))
                }
            }
            // Neurons carry the CSR ranges, so they are appended after the
            // edge layout is known.
            for entry in layout {
                for id in entry.ids {
                    c.appendNeuron(NeuronRecord(
                        canonicalID: id, datasetID: 0, type: UInt16(id % 3),
                        region: UInt8(entry.region.rawValue), side: entry.side,
                        transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                        provenance: 0, morphologyIndex: -1,
                        incomingStart: 0, incomingCount: 0,
                        outgoingStart: outgoing[Int(id)].start,
                        outgoingCount: outgoing[Int(id)].count,
                        x: Float(id), y: 0, z: 0))
                }
            }
            c.setOutgoingRanges(outgoing)
            return c
        }

        func run(approaching: Bool, steps: Int) -> (plate: UInt64, leg: UInt64) {
            let core = SimulationCore(connectome: loomConnectome())
            let w = darkWorld()
            // The body starts in front and closes on the fly's spawn point.
            w.addMovingObject(MovingObject(
                position: SIMD3<Float>(4, 0.8, 0),
                velocity: SIMD3<Float>(approaching ? -12 : 0, 0, 0),
                radius: 0.6))
            core.setScene(w)
            core.run(steps: 300)          // settle
            core.run(steps: steps)
            func spikes(_ region: RegionID) -> UInt64 {
                core.connectome.neuronIndices(in: region).reduce(UInt64(0)) {
                    $0 + UInt64(core.engine.cumulativeSpikes[Int($1)])
                }
            }
            return (spikes(.lobulaPlate), spikes(.legNeuromere))
        }
        let loom = run(approaching: true, steps: 2000)
        let still = run(approaching: false, steps: 2000)

        // The two claims, kept separate because they can fail separately: the
        // loomer must produce a loom, and the loom must reach the motor pool.
        // Whole-body displacement is deliberately NOT asserted here — whether
        // the fly then walks is BodyDynamics/MotorSystem's job and is gated in
        // WorldTests; folding it in would make a failure point at the wrong file.
        XCTAssertGreaterThan(loom.plate, 0,
                             "an approach must drive the looming target region")
        XCTAssertGreaterThan(loom.leg, 0,
                             "the loom must reach the leg motor pool (escape route)")
        // The control is what makes the two assertions above mean anything: a
        // static scene gets no loom, so its plate and leg pools stay quiet.
        XCTAssertGreaterThan(loom.plate, still.plate,
                             "the loom must drive the plate harder than a still scene")
        XCTAssertGreaterThan(loom.leg, still.leg,
                             "the escape route must be silent for a still scene")
    }
}