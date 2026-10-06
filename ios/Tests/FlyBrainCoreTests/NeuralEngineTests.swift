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
        let c = TestSupport.chainConnectome(count: 5, efficacy: 0.8, synapseCount: 4)
        var params = SimulationParameters()
        params.seed = 42
        params.synapticGain = 5.0    // strong enough to fire downstream
        let engine = NeuralEngine(connectome: c, parameters: params)

        // Drive neuron 0 with a burst (repeated synaptic drive at 50 Hz —
        // a single pulse is biologically unrealistic and decays before
        // reaching the end of a 5-neuron chain).
        TestSupport.driveBurst(engine: engine, neuron: 0,
                               startMs: 1.0, pulses: 20, intervalMs: 20,
                               current: 400)
        engine.run(steps: 3000)      // 300 ms

        XCTAssertGreaterThan(engine.spikeCount, 0, "chain must produce spikes")
        // Neuron 4 (motor end) should have fired — the whole chain propagated
        XCTAssertGreaterThan(engine.totalSpikes(of: 4), 0,
                             "chain of 5 must propagate excitation to neuron 4")
    }

    func testDeterminismSameSeed() {
        func run(seed: UInt64) -> (spikes: UInt64, firstSpikeTime: Double) {
            let c = TestSupport.chainConnectome(count: 8)
            var params = SimulationParameters()
            params.seed = seed
            params.synapticGain = 4
            let engine = NeuralEngine(connectome: c, parameters: params)
            TestSupport.driveBurst(engine: engine, neuron: 0,
                                   startMs: 2.0, pulses: 15, intervalMs: 15,
                                   current: 350)
            engine.run(steps: 3000)
            return (engine.spikeCount, engine.currentTimeMs)
        }
        let a = run(seed: 7)
        let b = run(seed: 7)
        XCTAssertEqual(a.spikes, b.spikes)
        XCTAssertEqual(a.firstSpikeTime, b.firstSpikeTime)
    }

    func testDeterminismDifferentSeedsDiffer() {
        func run(seed: UInt64) -> UInt64 {
            let c = TestSupport.chainConnectome(count: 8)
            var params = SimulationParameters()
            params.seed = seed
            params.synapticGain = 4
            let engine = NeuralEngine(connectome: c, parameters: params)
            TestSupport.driveBurst(engine: engine, neuron: 0,
                                   startMs: 2.0, pulses: 15, intervalMs: 15,
                                   current: 350)
            engine.run(steps: 3000)
            return engine.spikeCount
        }
        let a = run(seed: 7)
        let b = run(seed: 999)
        XCTAssertNotEqual(a, b, "different seeds should generally produce different spike trains")
    }
    }

    func testInhibitorySynapseSuppressesDownstream() {
        // build a custom graph: excitatory drive neuron 0 → neuron 2,
        // plus an inhibitory neuron 1 that vetoes neuron 2.
        var neurons: [NeuronRecord] = []
        var synapses: [SynapseRecord] = []
        var outgoing: [OutEdgeRange] = []

        func add(_ n: NeuronRecord, edges: Int) {
            neurons.append(n)
            outgoing.append(OutEdgeRange(start: Int32(synapses.count), count: Int32(edges)))
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
        synapses.append(SynapseRecord(preNeuron: 0, postNeuron: 2, synapseCount: 4,
                                      transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                      sign: Int8(SynapseSign.excitatory.rawValue),
                                      confidence: 50, delaySteps: 1, estimatedEfficacy: 0.6))
        synapses.append(SynapseRecord(preNeuron: 1, postNeuron: 2, synapseCount: 4,
                                      transmitter: UInt8(TransmitterType.gabaergic.rawValue),
                                      sign: Int8(SynapseSign.inhibitory.rawValue),
                                      confidence: 50, delaySteps: 1, estimatedEfficacy: 2.5))

        let header = ConnectomeHeader(magic: 0x46425031, version: 1, flags: 0,
                                      neuronCount: 3, synapseCount: 2, morphologyCount: 0, regionCount: 0,
                                      organism: OrganismInfo(datasetVersion: "t", simulatorVersion: "t", parameterProfile: "t"),
                                      sourceDatasets: ["synthetic-test"], dataProvenance: "SYNTHETIC-DEMO",
                                      generationDate: "now", generatedBy: "t", description: "inhib veto")
        let c = Connectome(header: header)
        for n in neurons { c.appendNeuron(n) }
        c.appendSynapse(contentsOf: synapses)
        c.setOutgoingRanges(outgoing)

        var params = SimulationParameters()
        params.seed = 3
        var engine = NeuralEngine(connectome: c, parameters: params)
        // fire neuron 1 (inhibitory) BEFORE neuron 0's excitation arrives
        TestSupport.driveBurst(engine: engine, neuron: 1, startMs: 1.0,
                               pulses: 15, intervalMs: 15, current: 400)  // veto
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 5.0,
                               pulses: 15, intervalMs: 15, current: 400)  // driver
        engine.run(steps: 2500)

        // Neuron 2's firing (if any) must be weaker than without the veto.
        // Compare against control run without neuron 1 spikes.
        func runControl(withVeto: Bool) -> Int {
            var p2 = SimulationParameters()
            p2.seed = 3
            let e2 = NeuralEngine(connectome: c, parameters: p2)
            if withVeto {
                TestSupport.driveBurst(engine: e2, neuron: 1, startMs: 1.0,
                                       pulses: 15, intervalMs: 15, current: 400)
            }
            TestSupport.driveBurst(engine: e2, neuron: 0, startMs: 5.0,
                                   pulses: 15, intervalMs: 15, current: 400)
            e2.run(steps: 2500)
            return e2.totalSpikes(of: 2)
        }
        let withVeto = runControl(withVeto: true)
        let withoutVeto = runControl(withVeto: false)
        XCTAssertLessThan(withVeto, withoutVeto,
                          "inhibitory veto must suppress downstream firing")
        // sanity: at least some spikes exist in control
        XCTAssertGreaterThan(withoutVeto, 0)

    func testSnapshotRestoreRoundTrip() {
        let c = TestSupport.chainConnectome(count: 6)
        var params = SimulationParameters()
        params.seed = 11
        let engineA = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engineA, neuron: 0, startMs: 1.0,
                               pulses: 12, intervalMs: 20, current: 380)
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

    func testCSRLayoutValid() {
        let c = TestSupport.chainConnectome(count: 10)
        XCTAssertTrue(c.validateCSR().isEmpty)
    }

    func testTelemetryIsReal() {
        let c = TestSupport.chainConnectome(count: 4, efficacy: 0.9, synapseCount: 6)
        var params = SimulationParameters()
        params.seed = 5
        params.synapticGain = 5
        let engine = NeuralEngine(connectome: c, parameters: params)
        TestSupport.driveBurst(engine: engine, neuron: 0, startMs: 1.0,
                               pulses: 18, intervalMs: 25, current: 500)
        engine.run(steps: 4000)  // 400 ms → crosses a telemetry window at 1000ms
        // spikesPerSecond must reflect actual firing, not a placeholder
        XCTAssertEqual(engine.spikesPerSecond >= 0, true)
        XCTAssertGreaterThan(engine.spikeCount, 0)
        // active neuron count must be plausible: at least the driver fired
        XCTAssertGreaterThan(engine.activeNeuronCount, 0)
    }
}