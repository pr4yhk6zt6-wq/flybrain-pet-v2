//
//  Types.swift
//  FlyBrainCore
//
//  Core enums, provenance, and metadata value types.
//  Platform-free (Foundation only) so it can be unit-tested on any OS.
//

import Foundation

/// Provenance classification for every biological attribute.
/// Mirror of the pipeline taxonomy in python/flybrain/pid.py
public enum Provenance: String, Codable, Sendable, CaseIterable {
    case measured       // "MEASURED"
    case reconstructed  // "RECONSTRUCTED"
    case inferred       // "INFERRED"
    case predicted      // "PREDICTED"
    case approximated   // "APPROXIMATED"
    case unknown        // "UNKNOWN"

    public var displayName: String { rawValue }
}

/// Which connectome dataset a neuron was sourced from (spec #4, #118).
public enum DatasetID: Int, Codable, Sendable, CaseIterable {
    case synthetic = 0
    case banc = 1        // BANC — brain + VNC, adult female
    case flywire = 2     // FAFB / FlyWire
    case fictrac = 3
    case other = 4

    public var name: String {
        switch self {
        case .synthetic: return "SYNTHETIC-DEMO"
        case .banc:      return "BANC"
        case .flywire:   return "FAFB/FlyWire"
        case .fictrac:   return "FicTrac"
        case .other:     return "OTHER"
        }
    }
}

/// Adult female default organism flag (spec #1). Kept explicit so a male
/// connectome can be loaded later as a separate biological dataset.
public struct OrganismInfo: Codable, Sendable {
    public var species: String          // "Drosophila melanogaster"
    public var sex: String              // "female" (default)
    public var lifeStage: String        // "adult"
    public var datasetVersion: String
    public var simulatorVersion: String
    public var parameterProfile: String // e.g. "female-adult-default-v1"

    public init(species: String = "Drosophila melanogaster",
                sex: String = "female",
                lifeStage: String = "adult",
                datasetVersion: String,
                simulatorVersion: String,
                parameterProfile: String) {
        self.species = species
        self.sex = sex
        self.lifeStage = lifeStage
        self.datasetVersion = datasetVersion
        self.simulatorVersion = simulatorVersion
        self.parameterProfile = parameterProfile
    }
}

/// Brain regions and body-part neuropils (spec #10).
public enum RegionID: Int, Codable, Sendable, CaseIterable {
    case unknown = 0
    case retinaLeft = 1
    case retinaRight = 2
    case lamina = 3
    case medulla = 4
    case lobula = 5
    case lobulaPlate = 6
    case opticLobe = 7
    case antennalLobe = 8
    case mushroomBody = 9
    case lateralHorn = 10
    case centralComplex = 11
    case superiorBrain = 12
    case subesophagealZone = 13          // SEZ
    case cervicalConnective = 14
    case ventralNerveCord = 15           // VNC
    case legNeuromere = 16
    case wingNeuropil = 17
    case haltereNeuropil = 18
    case abdominalNeuromere = 19
    case endocrineVisceral = 20

    public var name: String {
        switch self {
        case .unknown: return "unknown"
        case .retinaLeft: return "retina-L"
        case .retinaRight: return "retina-R"
        case .lamina: return "lamina"
        case .medulla: return "medulla"
        case .lobula: return "lobula"
        case .lobulaPlate: return "lobula-plate"
        case .opticLobe: return "optic-lobe"
        case .antennalLobe: return "antennal-lobe"
        case .mushroomBody: return "mushroom-body"
        case .lateralHorn: return "lateral-horn"
        case .centralComplex: return "central-complex"
        case .superiorBrain: return "superior-brain"
        case .subesophagealZone: return "SEZ"
        case .cervicalConnective: return "cervical-connective"
        case .ventralNerveCord: return "VNC"
        case .legNeuromere: return "leg-neuromere"
        case .wingNeuropil: return "wing-neuropil"
        case .haltereNeuropil: return "haltere-neuropil"
        case .abdominalNeuromere: return "abdominal-neuromere"
        case .endocrineVisceral: return "endocrine-visceral"
        }
    }
}

/// Neurotransmitter classes (spec #8). Sign is NOT implied by the class:
/// a transmitter's effect is stored per-synapse via `SynapseSign`.
public enum TransmitterType: Int, Codable, Sendable, CaseIterable {
    case unknown = 0
    case cholinergic     // ACh — usually excitatory in flies
    case gabaergic       // GABA — usually inhibitory
    case glutamatergic   // Glu — context-dependent
    case dopaminergic    // DA — reward/arousal modulation
    case serotonergic    // 5-HT
    case octopaminergic  // OA — arousal/escape
    case tyraminergic
    case peptidergic
    case histaminergic

    public var name: String {
        switch self {
        case .unknown: return "unknown"
        case .cholinergic: return "acetylcholine"
        case .gabaergic: return "GABA"
        case .glutamatergic: return "glutamate"
        case .dopaminergic: return "dopamine"
        case .serotonergic: return "serotonin"
        case .octopaminergic: return "octopamine"
        case .tyraminergic: return "tyramine"
        case .peptidergic: return "peptide"
        case .histaminergic: return "histamine"
        }
    }
}

/// Synaptic sign. IMPORTANT (spec #7): an anatomical connection is NOT a
/// physiological weight. `sign`/`estimatedEfficacy` are INFERRED values.
///
/// `modulatory` (0) is not "excitatory with a small effect": it means the edge
/// carries NO fast current and MotorSystem/NeuralEngine skip it entirely. It is
/// used for the cases where the transmitter label cannot decide valence —
/// glutamate (iGluR excitatory vs GluClα inhibitory in the fly CNS), the
/// GPCR-only amines (DA/SER/OCT/TYR), and every neuron whose transmitter the
/// release failed to predict. On banc-888 that is 59.4% of synaptic weight, so
/// reading 0 as excitation (the old `sign < 0 ? -1 : 1`) asserted 2.2M
/// excitatory synapses that were never measured.
public enum SynapseSign: Int8, Codable, Sendable {
    case excitatory = 1
    case inhibitory = -1
    case unpolarised = 0

    /// Legacy spelling kept so existing call sites/tests compile.
    public static var modulatory: SynapseSign { .unpolarised }
}

/// Neuron-level state snapshot for inspection/telemetry (spec #17, #35).
public struct NeuronState: Sendable {
    public let neuronID: Int32
    public let voltage: Float
    public let firingRate: Float
    public let recentSpikeCount: Int
    public var isActive: Bool { recentSpikeCount > 0 }
}

/// Behavioral classification — NEVER fed back into the simulation (spec #43/44).
/// This is a passive observer only.
public enum BehaviorClass: String, Sendable {
    case walking, turning, flight, grooming, feeding, escape, resting, exploring, proboscis, unknown

    public var name: String { rawValue }
}