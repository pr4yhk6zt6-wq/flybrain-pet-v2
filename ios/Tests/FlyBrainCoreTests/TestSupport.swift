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
    static func chainConnectome(count: Int, efficacy: Float = 0.5,
                                synapseCount: UInt16 = 2) -> Connectome {
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
            magic: 0x46425031, version: 1, flags: 0,
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
                    type: UInt8(pid % 3),
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
            magic: 0x46425031, version: 1, flags: 0,
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
}