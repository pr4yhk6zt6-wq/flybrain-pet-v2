//
//  SensoryInterface.swift
//  FlyBrainCore
//
//  Sensory transduction layer (spec #9, #28, #45): converts world signals into
//  synaptic currents injected into specific connectome neurons.
//
//  World → sensors (vision/olfaction/gustation/mechano) → sensory neurons →
//  connectome. This class is the ONLY place world signals enter the brain,
//  keeping the closed loop clean and inspectable.
//

import Foundation

/// A generated sensory input event ready for the engine.
public struct SensoryInput: Sendable {
    public let neuron: Int32
    public let current: Float      // signed (excitatory/inhibitory)
    public let modality: SensoryModality
    public let strength: Float     // 0..1 normalized intensity
    public let delayMs: Double

    public init(neuron: Int32, current: Float, modality: SensoryModality,
                strength: Float, delayMs: Double = 0) {
        self.neuron = neuron
        self.current = current
        self.modality = modality
        self.strength = strength
        self.delayMs = delayMs
    }
}

public enum SensoryModality: Int, Sendable {
    case vision = 0
    case olfaction = 1
    case gustation = 2
    case mechanosensation = 3
    case proprioception = 4
    case haltere = 5
    case nociception = 6

    public var name: String {
        switch self {
        case .vision: return "vision"
        case .olfaction: return "olfaction"
        case .gustation: return "gustation"
        case .mechanosensation: return "mechanosensation"
        case .proprioception: return "proprioception"
        case .haltere: return "haltere"
        case .nociception: return "nociception"
        }
    }
}

/// Converts sensory events into connectome-targeted currents.
public struct SensoryInterface {
    public let connectome: Connectome

    public init(connectome: Connectome) {
        self.connectome = connectome
    }

    /// Find an input neuron by region + side + optional cell type filter.
    /// Returns first match for the exact side, else any side-0 neuron in the
    /// region (synthetic demo assigns side 0 to most central regions).
    public func inputNeuron(region: RegionID, side: UInt8,
                            transmitter: TransmitterType? = nil) -> Int32? {
        var fallback: Int32? = nil
        // Region lookup via the index rather than a scan of every neuron: this
        // runs per sensory input request, and a whole-BANC connectome is
        // 153,746 neurons.
        for idx in connectome.neuronIndices(in: region) {
            let n = connectome.neurons[Int(idx)]
            if let t = transmitter, n.transmitter != UInt8(t.rawValue) { continue }
            if n.side == side { return idx }
            if n.side == 0 && fallback == nil { fallback = idx }
        }
        if let f = fallback { return f }
        return nil
    }

    /// Inject an odor-concentration input (Left/Right antennal sampling).
    /// Uses antennal-lobe input neurons; RL stronger than LL; concentration
    /// drives current via a saturating curve (spec #13).
    public func odorInput(concentrationL: Float, concentrationR: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let cL = min(max(concentrationL, 0), 1)
        let cR = min(max(concentrationR, 0), 1)
        if let n = inputNeuron(region: .antennalLobe, side: 1) {
            out.append(SensoryInput(neuron: n, current: cL * 40 - 5,
                                    modality: .olfaction, strength: cL))
        }
        if let n = inputNeuron(region: .antennalLobe, side: 2) {
            out.append(SensoryInput(neuron: n, current: cR * 40 - 5,
                                    modality: .olfaction, strength: cR))
        }
        return out
    }

    /// Mechanosensory touch input (e.g. leg contact). Targets VNC sensory
    /// interneurons on the given side (spec #15).
    public func touchInput(side: UInt8, intensity: Float) -> [SensoryInput] {
        guard let n = touchAfferent(side: side) else { return [] }
        return touchInput(neuron: n, intensity: intensity)
    }

    /// Touch input on a specific afferent. The caller that knows WHICH cell the
    /// afferent is (the reafference arm selects by cell class) uses this so the
    /// choice is not re-derived per step.
    public func touchInput(neuron: Int32, intensity: Float) -> [SensoryInput] {
        let inten = min(max(intensity, 0), 1)
        return [SensoryInput(neuron: neuron, current: inten * 50,
                             modality: .mechanosensation, strength: inten)]
    }

    /// The leg afferent a tarsal-load input should land on for `side`.
    ///
    /// MEASURED cell-class labels beat position. `legNeuromere` mixes the
    /// sensory afferents with the motor neurons they report to (on the real
    /// BANC release only 1.9% of its 9,954 neurons are motor; 47.9% are
    /// sensory afferents). Picking the region's first cell therefore picked a
    /// MOTOR neuron in the fixtures, so the "sensory" current entered the
    /// motor readout directly and bypassed the connectome entirely — measured
    /// on `TestSupport.regionalConnectome`, the cut-path control moved its
    /// displacement from 0.986 mm (region fallback) to 0.002 mm (by class).
    ///
    /// Precedence: classed side match, classed side 0, any side match, any
    /// side-0 cell. Assets written before the class byte existed carry no
    /// labels, so this degrades to the old positional result rather than
    /// inventing a label it does not have.
    public func touchAfferent(side: UInt8) -> Int32? {
        var classedSide: Int32? = nil
        var classedCentre: Int32? = nil
        var sideOnly: Int32? = nil
        var anyCentre: Int32? = nil
        for idx in connectome.neuronIndices(in: .legNeuromere) {
            let n = connectome.neurons[Int(idx)]
            if n.isSensoryNeuron {
                if n.side == side, classedSide == nil { classedSide = idx }
                if n.side == 0, classedCentre == nil { classedCentre = idx }
            }
            if n.side == side, sideOnly == nil { sideOnly = idx }
            if n.side == 0, anyCentre == nil { anyCentre = idx }
        }
        if let c = classedSide { return c }
        if let c = classedCentre { return c }
        if let c = sideOnly { return c }
        if let c = anyCentre { return c }
        return inputNeuron(region: .legNeuromere, side: side)
    }

    /// Looming (threat) input → lobula plate / giant-fiber-compatible drive
    /// (spec #23, escape). High-strength negative/positive current.
    public func loomingInput(intensity: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let i = min(max(intensity, 0), 1)
        if let n = inputNeuron(region: .lobulaPlate, side: 0) {
            out.append(SensoryInput(neuron: n, current: i * 80,
                                    modality: .vision, strength: i))
        }
        return out
    }

    /// Gustatory input: probe/taste at labellum → SEZ feeding circuits
    /// (spec #14: approach → probe → taste → evaluate → accept/reject).
    public func gustatoryInput(acceptance: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let a = min(max(acceptance, -1), 1)
        if let n = inputNeuron(region: .subesophagealZone, side: 0) {
            out.append(SensoryInput(neuron: n, current: a * 40,
                                    modality: .gustation, strength: abs(a)))
        }
        return out
    }

    /// Haltere inertial feedback (spec #16): rotational velocity → VNC
    /// stability circuits. Sign encodes rotation side.
    public func haltereInput(rotationRate: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let r = min(max(rotationRate, -1), 1)
        if let n = inputNeuron(region: .haltereNeuropil, side: 0) {
            out.append(SensoryInput(neuron: n, current: r * 30,
                                    modality: .haltere, strength: abs(r)))
        }
        return out
    }

    /// Wing strain (campaniform sensilla at the wing base) driven by the real
    /// stroke state: the sensilla report how hard the wing is being driven and
    /// how much load the stroke is carrying. `intensity` is 0..1.
    public func wingStrainInput(intensity: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let s = min(max(intensity, 0), 1)
        guard s > 0 else { return out }
        if let n = inputNeuron(region: .wingNeuropil, side: 0) {
            out.append(SensoryInput(neuron: n, current: s * 20,
                                    modality: .mechanosensation, strength: s))
        }
        return out
    }
}