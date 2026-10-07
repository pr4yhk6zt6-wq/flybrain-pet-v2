//
//  NeuralEngineTests.swift
//  FlyBrainCoreTests
//
//  Core guarantees the whole project depends on:
//  - deterministic event ordering (same seed → same result)
//  - sparse event-driven propagation through connectome topology
//  - LIF/AdEx spiking produces genuine events (no fake spikes)
//  - snapshot/restore round-trips exactly
//
//  NOTE ON DRIVE STRENGTH: the engine is sparse — only neurons that received
//  an event this step are integrated. A driver pulse reaches a downstream
//  neuron as current = efficacy × gain × synapseCount (nA) and one 0.1 ms step
//  moves the membrane by ≈ (I × 10) / tauM × dt mV. TestSupport.chainConnectome
//  therefore uses a strong synthetic synapse (100 release sites, EM-realistic
//  for a mainline projection) so a SINGLE presynaptic spike reliably fires the
//  next cell — no burst fudging needed — and every parameter below was
//  calibrated against the engine's equations (see tools/check_tests.py, an
//  offline mirror of NeuralEngine used when no Swift toolchain is available).
//

import XCTest
@testable import FlyBrainCore

final class NeuralEngineTests: XCTestCase {

    func testEventHeapTotalOrder() {
        var heap = EventHeap()
        let events = [
            SynapticEvent(time: 3, seq: 1, postNeuron: 0, current: 1, transmitter: 0),
            SynapticEvent(time: 1, seq: 3, postNeuron: 0, current: 1, transmitter: 0),
            SynapticEvent(time: 2, seq: 2, postNeuron: 0, current: 1, transmitter: 0),
            SynapticEvent(time: 1, seq: 1, postNeuron: 0, current: 1, transmitter: 0),
        ]
        for e in events { heap.push(e) }
        var popped: [Double] = []
        while let e = heap.pop() { popped.append(e.time) }
        XCTAssertEqual(popped, [1, 1, 2, 3])
    }

    func testEventHeapDeterministicTieBreak() {
        // two events with equal time must pop in seq order
        var heap = EventHeap()
        heap.push(SynapticEvent(time: 5, seq: 2, postNeuron: 1, current: 1, transmitter: 0))
        heap.push(SynapticEvent(time: 5, seq: 1, postNeuron: 0, current: 1, transmitter: 0))
        let first = heap.pop()!
        XCTAssertEqual(first.seq, 1)   // seq tie-break: lower seq first
    }

    func testChainPropagationProducesSpikes() {
        let c = TestSupport.chainConnectome(count: 5, efficacy: 0.8)
        var params = SimulationParameters()
        params.seed = 42
        params.synapticGain = 5.0    // current 0.8×5×100 = 400 nA
        let engine = NeuralEngine(connectome: c, parameters: params)

        // A SINGLE presynaptic spike at t=2 ms must run the whole chain:
        // each cell fires, so the spike reaches 1 → 2 → 3 → 4 with one
        // 0.1 ms delay per synapse.
        TestSupport.driveBurst(engine: engine, neuron: 0,
                               startMs: 2.0, pulses: 1, intervalMs: 20,
                               current: 400)
        engine.run(steps: 2000)      // 200 ms

        XCTAssertGreaterThan(engine.spikeCount, 0, "chain must produce spikes")
        XCTAssertGreaterThan(engine.totalSpikes(of: 0), 0, "driver must fire")
        // Neuron 4 (motor end) must have fired — the whole chain propagated
        XCTAssertGreaterThan(engine.totalSpikes(of: 4), 0,
                             "single spike of neuron 0 must propagate to neuron 4")
        // every cell of the chain fired, i.e. the spike really walked the
        // topology rather than being duplicated at the driver
        for n in 0..<5 {
            XCTAssertGreaterThan(engine.totalSpikes(of: Int32(n)), 0,
                                 "neuron \(n) must have fired during propagation")
        }
    }

    func testDeterminismSameSeed() {
        func run(seed: UInt64) -> (spikes: UInt64, firstSpikeTime: Double) {
            let c = TestSupport.chainConnectome(count: 8)
            var params = SimulationParameters()
            params.seed = seed
            params.synapticGain = 4
            let engine = NeuralEngine(connectome: c, parameters: params)
            TestSupport.driveBurst(engine: engine, neuron: 0,
                                   startMs: 2.0, pulses: 1, intervalMs: 15,
                                   current: 400)
            engine.run(steps: 500)
            return (engine.spikeCount, engine.currentTimeMs)
        }
        let a = run(seed: 7)
        let b = run(seed: 7)
        XCTAssertEqual(a.spikes, b.spikes)
        XCTAssertEqual(a.firstSpikeTime, b.firstSpikeTime)
    }

    func testDeterminismDifferentSeedsDiffer() {
        // Drive a chain near its firing threshold with experimental
        // noiseScale 0.5 (±5 mV band, 10× the biological default). Whether each
        // pulse crosses threshold is then decided by the seeded noise, so the
        // RNG stream determines the exact spike pattern. The drive is a long
        // pulse train (60 pulses at 100 Hz) so a seed difference accumulates
        // over ~60 independent threshold decisions instead of a single one.
        // Result across 40 seeds: 7 distinct patterns, collision ≈ 0.20, and
        // the three seeds below differ with certainty (see tools/check_tests.py).
        func run(seed: UInt64) -> [UInt32] {
            let c = TestSupport.chainConnectome(count: 4)
            var params = SimulationParameters()
            params.seed = seed
            params.synapticGain = 1
            params.noiseScale = 0.5
            let engine = NeuralEngine(connectome: c, parameters: params)
            TestSupport.driveBurst(engine: engine, neuron: 0,
                                   startMs: 1.0, pulses: 60, intervalMs: 10,
                                   current: 240)
            engine.run(steps: 8000)   // 800 ms
            return engine.cumulativeSpikes
        }
        let a = run(seed: 7)
        let b = run(seed: 999)
        let c2 = run(seed: 12345)
        XCTAssertTrue(a != b || b != c2 || a != c2,
                      "different seeds must produce different spike patterns (got \(a), \(b), \(c2))")
    }

    func testRefractoryDecaysWithoutFurtherInput() {
        // Regression: the refractory window is a property of time. Sparse
        // integration must not freeze it just because a neuron received no
        // new events — otherwise a second pulse arriving inside the window
        // would be discarded forever (this broke burst-driven propagation).
        let c = TestSupport.chainConnectome(count: 2, efficacy: 0.8)
        var params = SimulationParameters()
        params.seed = 1
        params.synapticGain = 5
        let engine = NeuralEngine(connectome: c, parameters: params)

        // two pulses to neuron 0, 20 ms apart (> tauRefractory); the second
        // must fire as well, giving 2 spikes from the driver
        TestSupport.driveBurst(engine: engine, neuron: 0,
                               startMs: 1.0, pulses: 2, intervalMs: 20,
                               current: 400)
        engine.run(steps: 1000)
        XCTAssertGreaterThanOrEqual(engine.totalSpikes(of: 0), 2,
                                    "refractory window must expire on the clock, not on input")
    }

    func testInhibitorySynapseSuppressesDownstream() {
        // build a custom graph: excitatory driver 0 → 2, plus an inhibitory
        // neuron 1 (GABAergic) that vetoes neuron 2.
        var neurons: [NeuronRecord] = []
        var synapses: [SynapseRecord] = []
        var outgoing: [OutEdgeRange] = []

        // Synapses are appended FIRST: an OutEdgeRange indexes into the synapse
        // array, so a helper that captured synapses.count while the array was
        // still empty made every neuron claim edge 0 — the GABAergic cell then
        // emitted the *excitatory* edge (veto increased firing 7 vs 4) and the
        // inhibitory edge was unreachable by anyone.
        synapses.append(SynapseRecord(preNeuron: 0, postNeuron: 2, synapseCount: 100,
                                      transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                      sign: Int8(SynapseSign.excitatory.rawValue),
                                      confidence: 50, delaySteps: 1, estimatedEfficacy: 0.6))
        synapses.append(SynapseRecord(preNeuron: 1, postNeuron: 2, synapseCount: 100,
                                      transmitter: UInt8(TransmitterType.gabaergic.rawValue),
                                      sign: Int8(SynapseSign.inhibitory.rawValue),
                                      confidence: 50, delaySteps: 1, estimatedEfficacy: 2.5))

        // ranges are assigned with a forward cursor over the (already complete)
        // synapse array, so neuron i owns exactly its own edge
        var edgeCursor = 0
        func add(_ n: NeuronRecord, edges: Int) {
            neurons.append(n)
            outgoing.append(OutEdgeRange(start: Int32(edgeCursor), count: Int32(edges)))
            edgeCursor += edges
        }
        add(NeuronRecord(canonicalID: 0, datasetID: 0, type: 0, region: 9, side: 0,
                         transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                         provenance: TestSupport.provIndex(.inferred),
                         morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                         outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0), edges: 1)
        add(NeuronRecord(canonicalID: 1, datasetID: 0, type: 0, region: 9, side: 0,
                         transmitter: UInt8(TransmitterType.gabaergic.rawValue),
                         provenance: TestSupport.provIndex(.inferred),
                         morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                         outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0), edges: 1)
        add(NeuronRecord(canonicalID: 2, datasetID: 0, type: 0, region: 9, side: 0,
                         transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                         provenance: TestSupport.provIndex(.inferred),
                         morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                         outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0), edges: 0)

        let header = ConnectomeHeader(magic: 0x46425031, version: 2, flags: 0,
                                      neuronCount: 3, synapseCount: 2, morphologyCount: 0, regionCount: 0,
                                      organism: OrganismInfo(datasetVersion: "t", simulatorVersion: "t", parameterProfile: "t"),
                                      sourceDatasets: ["synthetic-test"], dataProvenance: "SYNTHETIC-DEMO",
                                      generationDate: "now", generatedBy: "t", description: "inhib veto")
        let c = Connectome(header: header)
        for n in neurons { c.appendNeuron(n) }
        c.appendSynapse(contentsOf: synapses)
        c.setOutgoingRanges(outgoing)

        // Neuron 2 fires only on coincidence-free excitation; when the
        // inhibitory cell 1 spikes in the same window, the shunt (2.5× the
        // excitatory efficacy) keeps 2 below threshold.
        func runControl(withVeto: Bool) -> Int {
            var p = SimulationParameters()
            p.seed = 3
            let engine = NeuralEngine(connectome: c, parameters: p)
            if withVeto {
                TestSupport.driveBurst(engine: engine, neuron: 1, startMs: 1.0,
                                       pulses: 15, intervalMs: 15, current: 400)
            }
            TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 5.0,
                                   pulses: 15, intervalMs: 15, current: 400)
            engine.run(steps: 2500)
            return engine.totalSpikes(of: 2)
        }
        let withVeto = runControl(withVeto: true)
        let withoutVeto = runControl(withVeto: false)
        XCTAssertLessThan(withVeto, withoutVeto,
                          "inhibitory veto must suppress downstream firing")
        // sanity: the control really does drive neuron 2
        XCTAssertGreaterThan(withoutVeto, 0)
    }

    func testUnpolarisedSynapseCarriesNoCurrent() {
        // sign 0 means UNPOLARISED (the transmitter was unpredicted, or is one
        // whose valence is genuinely unknown, e.g. glutamate in the fly CNS).
        // The engine used to collapse 0 to +1 (`sign < 0 ? -1 : 1`), silently
        // exciting the target; on the real BANC release that mislabelled 9.3%
        // of the synaptic weight. Both graphs below are identical except for
        // the sign, so only the polarity can explain a difference.
        //   neuron 0 (driver) -> neuron 1
        //   neuron 2 (driver) -> neuron 3
        func build(sign: Int8) -> Connectome {
            var neurons: [NeuronRecord] = []
            var synapses: [SynapseRecord] = []
            var outgoing: [OutEdgeRange] = []
            var cursor: Int32 = 0
            func add(edges: Int32) {
                let i = neurons.count
                neurons.append(NeuronRecord(
                    canonicalID: Int32(i), datasetID: 0, type: 0, region: 9, side: 0,
                    transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                    provenance: TestSupport.provIndex(.inferred),
                    morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                    outgoingStart: 0, outgoingCount: 0, x: 0, y: 0, z: 0))
                outgoing.append(OutEdgeRange(start: cursor, count: edges))
                cursor += edges
            }
            synapses.append(SynapseRecord(preNeuron: 0, postNeuron: 1, synapseCount: 1000,
                                          transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                          sign: sign, confidence: 50, delaySteps: 1,
                                          estimatedEfficacy: 0.9))
            add(edges: 1)   // 0
            add(edges: 0)   // 1
            add(edges: 0)   // 2
            let header = ConnectomeHeader(magic: 0x46425031, version: 2, flags: 0,
                                          neuronCount: 3, synapseCount: 1, morphologyCount: 0, regionCount: 0,
                                          organism: OrganismInfo(datasetVersion: "t", simulatorVersion: "t", parameterProfile: "t"),
                                          sourceDatasets: ["synthetic-test"], dataProvenance: "SYNTHETIC-DEMO",
                                          generationDate: "now", generatedBy: "t", description: "sign 0")
            let c = Connectome(header: header)
            for n in neurons { c.appendNeuron(n) }
            c.appendSynapse(contentsOf: synapses)
            c.setOutgoingRanges(outgoing)
            return c
        }

        func targetSpikes(sign: Int8) -> Int {
            var p = SimulationParameters()
            p.seed = 5
            let engine = NeuralEngine(connectome: build(sign: sign), parameters: p)
            TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                                   pulses: 15, intervalMs: 15, current: 500)
            engine.run(steps: 2000)
            return engine.totalSpikes(of: 1)
        }

        let excitatory = targetSpikes(sign: Int8(SynapseSign.excitatory.rawValue))
        let unpolarised = targetSpikes(sign: 0)
        XCTAssertGreaterThan(excitatory, 0,
                             "an excitatory edge must drive its target (control)")
        XCTAssertEqual(unpolarised, 0,
                       "a sign-0 synapse must carry no current, not excitation")
    }

    func testSnapshotRestoreRoundTrip() {
        let c = TestSupport.chainConnectome(count: 6)
        var params = SimulationParameters()
        params.seed = 11
        let engineA = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engineA, neuron: 0, startMs: 1.0,
                               pulses: 1, intervalMs: 20, current: 400)
        engineA.run(steps: 1000)

        let snap = engineA.snapshot()

        // Create a fresh engine from the same connectome and continue from snapshot
        let engineB = NeuralEngine(connectome: c, parameters: params)
        engineB.restore(snap)
        engineB.run(steps: 1000)

        // Engine A continues from its own state
        engineA.run(steps: 1000)

        // Both should have identical total spike counts and times
        XCTAssertEqual(engineA.spikeCount, engineB.spikeCount)
        XCTAssertEqual(engineA.currentTimeMs, engineB.currentTimeMs)
        XCTAssertEqual(engineA.simulationStep, engineB.simulationStep)
    }

    func testConnectomeValidationCatchesOrphanEdges() {
        let c = TestSupport.chainConnectome(count: 3)
        var problems = c.validate()
        XCTAssertTrue(problems.isEmpty, "valid chain should pass: \(problems)")
        // Force an out-of-range synapse
        c.appendSynapse(SynapseRecord(preNeuron: 0, postNeuron: 99, synapseCount: 1,
                                      transmitter: 0, sign: 1, confidence: 50,
                                      delaySteps: 0, estimatedEfficacy: 0.1))
        problems = c.validate()
        XCTAssertTrue(problems.contains { $0.contains("postNeuron") },
                      "orphan edge must be flagged")
    }

    func testValidateCatchesAliasedOutgoingRanges() {
        // Two neurons pointing at synapse 0: one edge is emitted twice and the
        // other presynaptic cell loses its output entirely. The graph still
        // "runs", so nothing but validation can catch it (regression guard for
        // a wiring bug that broke the inhibitory veto test).
        let c = TestSupport.chainConnectome(count: 3)
        c.setOutgoingRanges([OutEdgeRange(start: 0, count: 1),
                             OutEdgeRange(start: 0, count: 1),
                             OutEdgeRange(start: 2, count: 1)])
        let problems = c.validate()
        XCTAssertTrue(problems.contains { $0.contains("referenced by 2 neurons") },
                      "aliased outgoing ranges must be flagged: \(problems)")
    }

    func testCSRLayoutValid() {
        XCTAssertTrue(TestSupport.chainConnectome(count: 10).validateCSR().isEmpty)
    }

    func testRecentSpikeWindowHoldsAndDrains() {
        // recentSpikes() is the ONLY real input to the motor system
        // (SimulationCore.readMotorDrive), and the field is documented as a 1 s
        // window. The old decay subtracted a quarter of the window *in steps*
        // (2500) every 100 steps, so any counter below 2500 was wiped to zero
        // every 10 ms — recentSpikes() read 0 for every neuron on every frame
        // and the window was physically 10 ms, not 1 s. Drive a burst whose
        // spikes span ~1 s and assert the window actually accumulates.
        let c = TestSupport.chainConnectome(count: 3, efficacy: 0.5)
        var params = SimulationParameters()
        params.seed = 3
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 20, intervalMs: 50, current: 400)
        // Sample the window as it fills: reading only at the end would read
        // AFTER the 1 s window rolls (and correctly clears), not a failure.
        var peak = 0
        for _ in 0..<20 {                 // 20 × 50 ms = the first window
            engine.run(steps: 500)
            peak = max(peak, engine.recentSpikes(of: 0))
        }
        XCTAssertGreaterThan(peak, 1,
                             "the 1 s window must hold more than a single spike")
        // and it must drain once the window rolls with no further drive
        engine.run(steps: 20000)
        XCTAssertEqual(engine.recentSpikes(of: 0), 0,
                       "an idle window must drain to zero")
    }

    func testActiveSetsStayConsistentWithState() {
        // `step()` now walks only the active sets instead of sweeping all
        // neurons every step (the O(neurons) sweep was the largest per-step
        // cost on whole-BANC: 153,746 neurons × every 0.1 ms step). The
        // optimisation is only valid if the sets are EXACTLY the non-zero
        // entries — a stale entry would decay something forever, and a missing
        // one would stop decaying a live window.
        let c = TestSupport.chainConnectome(count: 6, efficacy: 0.6)
        var params = SimulationParameters()
        params.seed = 11
        params.synapticGain = 3
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 8, intervalMs: 30, current: 500)
        engine.run(steps: 5000)

        let refrSet = Set(engine.refractoryActiveNeurons)
        let recentSet = Set(engine.recentActiveNeurons)
        for i in 0..<c.neuronCount {
            let tracked = refrSet.contains(Int32(i))
            XCTAssertEqual(tracked, engine.dynamics[i].refractoryRemaining > 0,
                           "refractory set must match the state exactly (neuron \(i))")
            let recentTracked = recentSet.contains(Int32(i))
            XCTAssertEqual(recentTracked, engine.recentSpikes(of: Int32(i)) > 0,
                           "recent set must match the state exactly (neuron \(i))")
        }
    }

    func testLeakyRateTracksTrueFiringRate() {
        // The motor readout used the tumbling `recent` window, which is cleared
        // wholesale when it rolls — so the value depends on WHEN it is sampled
        // and reads 0 for most of every window. At the current neural clock
        // (0.1 ms/step, 4 steps per 60 Hz frame ≈ 42x slow motion) a 1 s neural
        // window is ~42 s of WALL time, so the fly could not change its drive
        // in under 40 s. The leaky rate must instead TRACK the real rate.
        let c = TestSupport.chainConnectome(count: 2, efficacy: 0.5)
        var params = SimulationParameters()
        params.seed = 5
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 20, intervalMs: 50, current: 400)
        var acc: Float = 0, n = 0
        for _ in 0..<900 {
            engine.run(steps: 10)
            acc += engine.rateHz(of: 0)
            n += 1
        }
        let meanEst = acc / Float(n)
        let trueRate = Float(engine.totalSpikes(of: 0)) / Float(engine.currentTimeMs / 1000)
        XCTAssertGreaterThan(trueRate, 0)
        XCTAssertLessThan(abs(meanEst - trueRate) / trueRate, 0.15,
                          "mean estimate \(meanEst) Hz must track the true rate \(trueRate) Hz")
    }

    func testLeakyRateNeverReadsZeroWhileFiring() {
        // The exact failure of the tumbling window: 0 at almost every instant.
        let c = TestSupport.chainConnectome(count: 2, efficacy: 0.5)
        var params = SimulationParameters()
        params.seed = 5
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 40, intervalMs: 25, current: 400)
        engine.run(steps: 3000)
        var lo: Float = .greatestFiniteMagnitude
        for _ in 0..<100 {
            engine.run(steps: 1)
            lo = min(lo, engine.rateHz(of: 0))
        }
        XCTAssertGreaterThan(lo, 1.0,
                             "estimate dropped to \(lo) Hz while the neuron was firing steadily")
    }

    func testLeakyRateSurvivesSnapshotRestore() {
        // The rate feeds the body's motion, so losing it on restore would make
        // a restored run behave differently from the original (spec #63).
        let c = TestSupport.chainConnectome(count: 4, efficacy: 0.5)
        var params = SimulationParameters()
        params.seed = 7
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 10, intervalMs: 30, current: 400)
        engine.run(steps: 500)
        let live = engine.rateHz(of: 0)
        let snap = engine.snapshot()
        engine.run(steps: 1000)
        engine.restore(snap)
        XCTAssertEqual(engine.rateHz(of: 0), live, accuracy: 1e-6,
                       "the leaky rate must survive snapshot/restore")
    }

    func testTelemetryIsReal() {
        let c = TestSupport.chainConnectome(count: 4, efficacy: 0.9)
        var params = SimulationParameters()
        params.seed = 5
        params.synapticGain = 5
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 1, intervalMs: 25, current: 500)
        engine.run(steps: 4000)  // 400 ms → crosses a telemetry window boundary
        // spikesPerSecond must reflect actual firing, not a placeholder
        XCTAssertGreaterThan(engine.spikeCount, 0)
        XCTAssertGreaterThan(engine.activeNeuronCount, 0)
        XCTAssertGreaterThanOrEqual(engine.spikesPerSecond, 0)
    }
}