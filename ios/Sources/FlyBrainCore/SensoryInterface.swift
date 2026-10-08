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
    ///
    /// A cell the MOTOR READOUT sums is never chosen while a non-motor
    /// candidate exists. This is a general rule, not a tarsal-load special
    /// case: every sensory channel funnels through here, and injecting into a
    /// summed cell feeds the readout directly, so the animal moves because the
    /// channel is firing rather than because a path carries the signal. In the
    /// offloop fixtures it picked a motor neuron in both `legNeuromere` and
    /// `wingNeuropil`; on assets with no class byte the exclusion is inert and
    /// the positional result stands. `tools/probe_channel_targets.py` gates
    /// every channel on it.
    ///
    /// SIDE-FIRST, THEN AFFERENCES. Measured on the committed BANC asset
    /// (`tools/measure_motor_pool_coverage.py`), not one of its 153,746 cells
    /// carries side 0: the release only says left or right. A rule that
    /// searched side-matches first and only then looked past them therefore
    /// found NOTHING for every channel that asks for side 0 — a DEAD channel,
    /// which is the mirror image of the shorted one this rule was written to
    /// prevent, and it looks exactly like "the fly is not moving". Preferring
    /// the labelled afferents first, and only then the side match, keeps a real
    /// cell on an asset that has sides and on one that does not.
    public func inputNeuron(region: RegionID, side: UInt8,
                            transmitter: TransmitterType? = nil) -> Int32? {
        return selectInputNeuron(region: region, side: side, transmitter: transmitter)
    }

    /// The afferent a sensory channel should enter on, ignoring side.
    /// `inputNeuron(region:side:)` with `side: 0` resolves to nothing on a
    /// connectome whose cells all have a side, so channels that describe a
    /// whole-body or mid-line signal ask through this instead.
    public func inputAfferent(region: RegionID,
                              transmitter: TransmitterType? = nil) -> Int32? {
        return selectInputNeuron(region: region, side: 0, transmitter: transmitter)
    }

    /// One selection rule, used by every channel above.
    ///
    /// The pass ORDER is the behaviour, and each pass exists because of a
    /// measured defect — two of them are mirror images of each other:
    ///
    ///   1. a SENSORY-labelled cell on the requested side;
    ///   2. any SENSORY-labelled cell. Passes 1-2 are the DEAD fix: the BANC
    ///      release gives every one of its 153,746 cells a side of 1 or 2 and
    ///      none a side of 0, so a rule whose first move was "match the side,
    ///      else side 0" returned nil for every channel that asks for side 0 —
    ///      six of seven channels in the shipped asset resolved to NOTHING and
    ///      the fly was deaf. Pass 1 also keeps odour left and odour right on
    ///      different cells;
    ///   3. a labelled cell that is not the readout: requested side, then side
    ///      0, then any. The exclusion is the SHORTED fix — landing on a cell
    ///      `readMotorDrive` sums turns a sensory current into motor command
    ///      without touching the connectome;
    ///   4. positional: requested side, then side 0, among non-readout cells;
    ///   5. the region's first non-readout cell.
    ///
    /// Passes 4-5 are where an asset with no class byte anywhere lands, exactly
    /// as it did before this rule existed. The exclusion in 3-5 is by the
    /// MEASURED class byte — the same bit `readMotorDrive` sums — so on a
    /// labelled asset the two agree about which cells are motor, and on an
    /// unlabelled one both fall back to region identity.
    private func selectInputNeuron(region: RegionID, side: UInt8,
                                   transmitter: TransmitterType?) -> Int32? {
        // Region lookup via the index rather than a scan of every neuron: this
        // runs per sensory input request, and a whole-BANC connectome is
        // 153,746 neurons.
        let indices = connectome.neuronIndices(in: region)
        if indices.isEmpty { return nil }
        let byClass = indices.contains { connectome.neurons[Int($0)].isMotorNeuron }

        func transmitterMatches(_ idx: Int32, _ t: TransmitterType?) -> Bool {
            guard let t else { return true }
            return connectome.neurons[Int(idx)].transmitter == UInt8(t.rawValue)
        }
        func acceptable(_ idx: Int32) -> Bool {
            let n = connectome.neurons[Int(idx)]
            if !transmitterMatches(idx, transmitter) { return false }
            if byClass && n.isMotorNeuron { return false }
            return true
        }

        // 1. the sensory-labelled cell on the requested side.
        for idx in indices where connectome.neurons[Int(idx)].isSensoryNeuron {
            if connectome.neurons[Int(idx)].side != side { continue }
            if !transmitterMatches(idx, transmitter) { continue }
            return idx
        }
        // 2. any sensory-labelled cell. This is the pass that rescues a request for
        //    a side the region does not have — side 0 on an asset whose cells are
        //    all left/right, which is every cell of the BANC release. Without it
        //    six of the seven channels land on nothing at all.
        for idx in indices where connectome.neurons[Int(idx)].isSensoryNeuron {
            if !transmitterMatches(idx, transmitter) { continue }
            return idx
        }
        // 3. a labelled cell that is not the readout.
        if indices.contains(where: { connectome.neurons[Int($0)].flags != 0 }) {
            for wanted in [side, UInt8(0)] {
                for idx in indices {
                    let n = connectome.neurons[Int(idx)]
                    if n.flags == 0 || n.isMotorNeuron { continue }
                    if n.side == wanted, transmitterMatches(idx, transmitter) {
                        return idx
                    }
                }
            }
            for idx in indices where acceptable(idx)
                && connectome.neurons[Int(idx)].flags != 0 {
                return idx
            }
        }
        // 4. positional: the wanted side, then side 0 — where an asset with no class
        //    byte lands, unchanged.
        for wanted in [side, UInt8(0)] {
            for idx in indices where acceptable(idx) {
                if connectome.neurons[Int(idx)].side == wanted { return idx }
            }
        }
        // 5. the region's first non-readout cell.
        for idx in indices where acceptable(idx) { return idx }
        // Every cell here is a motor cell: no afferent exists to carry the
        // input, so the honest answer is that this channel has no target on
        // this asset. Injecting into the readout instead is the one outcome
        // that would silently fake a circuit.
        //
        // Reachable exactly when `byClass` is true and EVERY cell of the region is
        // motor. No shipped asset hits it any more: the synthetic demo used to
        // be the example (`wingNeuropil` and `legNeuromere` were motor-only),
        // and making those mixed is what gave the channels somewhere to land.
        // It is covered by `SensoryChannelTests`
        // .testAMotorOnlyRegionReportsNoAfferentRatherThanTheReadout, which
        // builds such a region by hand; `tools/probe_shipped_asset_channels.py`
        // still asserts the DEAD verdict if an asset ever produces one.
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
    /// side-0 cell, then `inputAfferent` (classed cell on any side). Assets
    /// written before the class byte existed carry no labels, so this degrades
    /// to the positional result rather than inventing a label it does not
    /// have — and on those assets the region's first cell is the only thing
    /// left to pick, which is what the fixtures assert.
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
        return inputAfferent(region: .legNeuromere)
    }

    /// Looming (threat) input → lobula plate / giant-fiber-compatible drive
    /// (spec #23, escape). High-strength negative/positive current.
    public func loomingInput(intensity: Float) -> [SensoryInput] {
        var out: [SensoryInput] = []
        let i = min(max(intensity, 0), 1)
        if let n = inputAfferent(region: .lobulaPlate) {
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
        if let n = inputAfferent(region: .subesophagealZone) {
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
        if let n = inputAfferent(region: .haltereNeuropil) {
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
        if let n = inputAfferent(region: .wingNeuropil) {
            out.append(SensoryInput(neuron: n, current: s * 20,
                                    modality: .mechanosensation, strength: s))
        }
        return out
    }
}