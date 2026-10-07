//
//  TestSupport.swift
//  FlyBrainCoreTests
//
//  Helpers to build small deterministic synthetic connectomes for tests.
//

import Foundation
@testable import FlyBrainCore

enum TestSupport {
    /// Index of a provenance case in allCases (storage format for UInt8).
    static func provIndex(_ p: Provenance) -> UInt8 {
        UInt8(Provenance.allCases.firstIndex(of: p) ?? 0)
    }

    /// Build a tiny connectome: n neurons forming a chain 0→1→2→…→n-1
    /// plus a feedback loop last→first. All cholinergic excitatory.
    /// A high release-site count (100) makes a single presynaptic spike
    /// reliably fire the next neuron: current = efficacy×gain×100 nA, and one
    /// 0.1 ms step moves the membrane by ≈ (I×10)/tauM×dt mV — 8 mV for the
    /// default test gain, comfortably above the 10 mV rest→threshold gap once
    /// the 8 mV inhibitory shunt is accounted for. EM-realistic for a
    /// mainline projection; keeps propagation tests deterministic instead of
    /// relying on bursts or lucky timing.
    static func chainConnectome(count: Int, efficacy: Float = 0.5,
                                synapseCount: UInt16 = 100) -> Connectome {
        var neurons: [NeuronRecord] = []
        var synapses: [SynapseRecord] = []
        var outgoing: [OutEdgeRange] = []

        for i in 0..<count {
            let start = Int32(synapses.count)
            let c = (i < count - 1) ? 1 : 0   // forward edge (last has feedback instead)
            var edges = c
            if i == count - 1 { edges = 1 }   // feedback loop
            neurons.append(NeuronRecord(
                canonicalID: Int32(i), datasetID: 0, type: 0, region: UInt8(RegionID.centralComplex.rawValue),
                side: 0, transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                provenance: provIndex(.inferred),
                morphologyIndex: -1, incomingStart: 0, incomingCount: 0,
                outgoingStart: start, outgoingCount: Int32(edges),
                x: Float(i), y: 0, z: 0))
            if i < count - 1 {
                synapses.append(SynapseRecord(preNeuron: Int32(i), postNeuron: Int32(i + 1),
                                              synapseCount: synapseCount,
                                              transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                              sign: Int8(SynapseSign.excitatory.rawValue),
                                              confidence: 50, delaySteps: 1,
                                              estimatedEfficacy: efficacy))
            } else if i == count - 1 {
                synapses.append(SynapseRecord(preNeuron: Int32(i), postNeuron: 0,
                                              synapseCount: synapseCount,
                                              transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                              sign: Int8(SynapseSign.excitatory.rawValue),
                                              confidence: 50, delaySteps: 1,
                                              estimatedEfficacy: efficacy))
            }
            outgoing.append(OutEdgeRange(start: start, count: Int32(edges)))
        }
        let header = ConnectomeHeader(
            magic: 0x46425031, version: 2, flags: 0,
            neuronCount: Int32(count), synapseCount: Int32(synapses.count),
            morphologyCount: 0, regionCount: 0,
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "TestSupport", description: "chain")
        let c = Connectome(header: header)
        for n in neurons { c.appendNeuron(n) }
        c.appendSynapse(contentsOf: synapses)
        c.setOutgoingRanges(outgoing)
        return c
    }
}

extension Connectome {
    func appendSynapse(contentsOf syns: [SynapseRecord]) {
        for s in syns { appendSynapse(s) }
    }
}

// MARK: - Burst drive helper (realistic repeated synaptic drive)

extension TestSupport {
    /// Inject a train of current pulses (a burst) into a neuron — repeated
    /// drive like real presynaptic input, so chains propagate (spec #6/#28).
    static func driveBurst(engine: NeuralEngine, neuron: Int32,
                           startMs: Double, pulses: Int, intervalMs: Double,
                           current: Float) {
        for i in 0..<pulses {
            engine.injectCurrent(into: neuron, current: current,
                                 at: startMs + Double(i) * intervalMs)
        }
    }
}

// MARK: - Region-aware test connectome

extension TestSupport {
    /// Build a connectome with neurons grouped into the real biological
    /// regions (spec #10), so sensory mapping and behavior classification
    /// tests hit real region targeting.
    static func regionalConnectome(neuronsPerRegion: Int = 10,
                                   efficacy: Float = 0.5) -> Connectome {
        let regions: [RegionID] = [
            .retinaLeft, .retinaRight, .lamina, .medulla, .lobula, .lobulaPlate,
            .opticLobe, .antennalLobe, .mushroomBody, .lateralHorn,
            .centralComplex, .superiorBrain, .subesophagealZone,
            .cervicalConnective, .ventralNerveCord, .legNeuromere,
            .wingNeuropil, .haltereNeuropil, .abdominalNeuromere,
            .endocrineVisceral,
        ]
        var neurons: [NeuronRecord] = []
        var synapses: [SynapseRecord] = []
        var outgoing: [OutEdgeRange] = []

        var pid = 0
        for region in regions {
            for _ in 0..<neuronsPerRegion {
                neurons.append(NeuronRecord(
                    canonicalID: Int32(pid),
                    datasetID: 0,
                    // `type` is UInt16 since v2 (the real release has 11,566
                    // cell types, so a u8 vocabulary would alias them).
                    type: UInt16(pid % 3),
                    region: UInt8(region.rawValue),
                    side: (region == .retinaLeft || region == .retinaRight) ? UInt8(region == .retinaLeft ? 1 : 2) : 0,
                    transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                    provenance: provIndex(.inferred),
                    morphologyIndex: -1,
                    incomingStart: 0, incomingCount: 0,
                    outgoingStart: 0, outgoingCount: 0,
                    x: Float(pid), y: 0, z: 0))
                outgoing.append(OutEdgeRange(start: 0, count: 0))
                pid += 1
            }
        }

        // a few chain synapses to allow propagation (neuron i → i+1),
        // skipping region boundaries so each region stays contiguous.
        for idx in 0..<(pid - 1) {
            if idx % neuronsPerRegion == neuronsPerRegion - 1 { continue } // region boundary
            let start = Int32(synapses.count)
            synapses.append(SynapseRecord(preNeuron: Int32(idx), postNeuron: Int32(idx + 1),
                                          synapseCount: 3,
                                          transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                                          sign: Int8(SynapseSign.excitatory.rawValue),
                                          confidence: 50, delaySteps: 1,
                                          estimatedEfficacy: efficacy))
            outgoing[idx] = OutEdgeRange(start: start, count: 1)
        }

        let header = ConnectomeHeader(
            magic: 0x46425031, version: 2, flags: 0,
            neuronCount: Int32(pid), synapseCount: Int32(synapses.count),
            morphologyCount: 0, regionCount: 0,
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "TestSupport", description: "regional")
        let c = Connectome(header: header)
        for n in neurons { c.appendNeuron(n) }
        c.appendSynapse(contentsOf: synapses)
        c.setOutgoingRanges(outgoing)
        return c
    }

    /// A connectome that actually closes the arc: retina/antennal input →
    /// brain → leg/wing motor pools, with the pool→pool recurrence the
    /// synthetic stand-in has.
    ///
    /// `regionalConnectome` deliberately skips every region boundary (`idx %
    /// neuronsPerRegion == neuronsPerRegion - 1 → continue`), so each region is
    /// an isolated chain and no excitation can leave the sensory region. That is
    /// fine for the tests that only exercise region targeting, but it means the
    /// sensory→motor path does not exist there: a test asserting the animal
    /// "moved" against that fixture is asserting nothing, which is what
    /// `WorldTests.testSceneIntegratesIntoClosedLoop` was doing.
    static func closedLoopConnectome(efficacy: Float = 2) -> Connectome {
        let regions: [RegionID] = [
            .retinaLeft, .retinaRight, .lamina, .antennalLobe,
            .medulla, .mushroomBody, .centralComplex,
            .legNeuromere, .wingNeuropil,
        ]
        let sources: Set<RegionID> = [.retinaLeft, .retinaRight, .lamina, .antennalLobe]
        let motors: Set<RegionID> = [.legNeuromere, .wingNeuropil]

        var neurons: [NeuronRecord] = []
        var indexOfRegion: [RegionID: [Int32]] = [:]
        var pid: Int32 = 0
        for region in regions {
            // side 1/2 on the retinae so `inputNeuron(region:side:)` resolves
            // the way it does on real data; 0 elsewhere.
            let side: UInt8 = region == .retinaLeft ? 1 : (region == .retinaRight ? 2 : 0)
            var ids: [Int32] = []
            for _ in 0..<4 {
                neurons.append(NeuronRecord(
                    canonicalID: pid, datasetID: 0, type: UInt16(pid % 3),
                    region: UInt8(region.rawValue), side: side,
                    transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                    provenance: 0, morphologyIndex: -1,
                    incomingStart: 0, incomingCount: 0,
                    outgoingStart: 0, outgoingCount: 0,
                    x: Float(pid), y: 0, z: 0))
                ids.append(pid)
                pid += 1
            }
            indexOfRegion[region] = ids
        }

        var synapses: [SynapseRecord] = []
        var outgoing = [OutEdgeRange](repeating: OutEdgeRange(start: 0, count: 0),
                                      count: Int(pid))
        // `efficacy` is the per-synapse value on the wire; the engine's edge current
        // is `estimatedEfficacy * synapticGain(1.0) * synapseCount(100)`, and a
        // spike's depolarisation is `current * 10 * dt / tauM`
        // (0.1 ms / 10 ms = 1e-3), so a single synaptic spike delivers
        // `efficacy * 0.1` mV. With a resting-to-threshold gap of ~10 mV, an
        // efficacy below ~1.5 cannot make a partner fire at all and no chain
        // propagates: the sensory neurons fire, nothing downstream does, and an
        // assertion that the fly "moved" passes only because it never moved.
        // 2.0 was calibrated in mirrors (tools/sim_closed_loop.py) — 1.5 is the
        // measured onset of propagation in this 4-neuron-per-region fixture.
        func wire(_ from: Int32, _ to: Int32) {
            let start = Int32(synapses.count)
            synapses.append(SynapseRecord(
                preNeuron: from, postNeuron: to, synapseCount: 100,
                transmitter: UInt8(TransmitterType.cholinergic.rawValue),
                sign: Int8(SynapseSign.excitatory.rawValue),
                confidence: 50, delaySteps: 1,
                estimatedEfficacy: efficacy))
            outgoing[Int(from)] = OutEdgeRange(start: start, count: 1)
        }

        // intra-region relay chain (i → i+1). A region with no internal relay
        // cannot carry a signal from its input neuron to its output neuron, so
        // the input would land on a neuron that has nothing to send.
        for region in regions {
            guard let ids = indexOfRegion[region], ids.count > 1 else { continue }
            for i in 0..<(ids.count - 1) { wire(ids[i], ids[i + 1]) }
        }
        // sensory -> brain (two stages, so the path is not a single hop)
        for s in sources {
            guard let from = indexOfRegion[s]?.last,
                  let mid = indexOfRegion[.medulla]?.first,
                  let next = indexOfRegion[.medulla]?.last,
                  let to = indexOfRegion[.centralComplex]?.first else { continue }
            wire(from, mid)
            wire(next, to)
        }
        // brain -> motor pools (last brain neuron drives the pool)
        for b in [RegionID.medulla, .centralComplex] {
            guard let from = indexOfRegion[b]?.last else { continue }
            for m in motors {
                if let to = indexOfRegion[m]?.first { wire(from, to) }
            }
        }
        // motor pools are self-sustaining once driven (as in the stand-in)
        for m in motors {
            guard let ids = indexOfRegion[m] else { continue }
            wire(ids[0], ids[1])
            wire(ids[1], ids[0])
        }

        let header = ConnectomeHeader(
            magic: 0x46425031, version: 2, flags: 0,
            neuronCount: pid, synapseCount: Int32(synapses.count),
            morphologyCount: 0, regionCount: 0,
            organism: OrganismInfo(datasetVersion: "test",
                                   simulatorVersion: "test",
                                   parameterProfile: "test"),
            sourceDatasets: ["synthetic-test"],
            dataProvenance: "SYNTHETIC-DEMO",
            generationDate: "now", generatedBy: "TestSupport", description: "closed-loop")
        let c = Connectome(header: header)
        for n in neurons { c.appendNeuron(n) }
        c.appendSynapse(contentsOf: synapses)
        c.setOutgoingRanges(outgoing)
        return c
    }
}