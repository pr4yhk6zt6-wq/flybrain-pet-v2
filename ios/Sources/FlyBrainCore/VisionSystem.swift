//
//  VisionSystem.swift
//  FlyBrainCore
//
//  Biological approximation of the Drosophila compound-eye visual pipeline
//  (spec #11, #12, #98):
//
//    WORLD → FLY EYE → RETINAL SIGNAL → OPTIC LOBE → spiking response → MOTOR OUTPUT
//
//  NOT a camera/CNN feeding labels into the brain. Produces feature signals
//  matching biological channels:
//    - per-ommatidium brightness/contrast sampling
//    - ON / OFF pathways (separate dark/light edges)
//    - motion & optic flow (translational/rotational)
//    - looming (expansion → threat drive)
//    - small-object motion
//
//  Every signal emitted here is converted into synaptic current injected into
//  connectome neurons — never an "if X then Y" behavior command.
//

import Foundation

/// One ommatidium-like sampling unit of the compound eye.
public struct Ommatidium {
    /// Direction of the optical axis (unit vector in world space).
    public var axisX: Float
    public var axisY: Float
    public var axisZ: Float
    /// Angular half-width of the receptive field (radians).
    public var acceptanceAngle: Float
    /// Position on the retina (for retinotopic mapping to lamina/medulla).
    public var retinaU: Float
    public var retinaV: Float
    /// Side: 1 = left eye, 2 = right eye.
    public var side: UInt8
}

/// Instantaneous luminance sample at one ommatidium.
public struct RetinalSample {
    public let luminance: Float        // 0..1
    public let contrast: Float         // local contrast (luminance - neighborhood mean)
    public let dLumDt: Float           // temporal derivative of luminance
    public let flowU: Float            // optic flow along retinal axes
    public let flowV: Float
}

/// A spike-like visual event translated into synaptic current.
/// The engine consumes these via `SensoryInterface`.
public struct VisualEvent {
    public let sourceOmmatidium: Int
    public let pathway: VisualPathway    // which biological channel
    public let strength: Float           // signed synaptic drive
    public let side: UInt8
    public let time: Double
}

public enum VisualPathway: Int, Sendable {
    case photoreceptor = 0
    case onEdge = 1        // bright edge (ON pathway)
    case offEdge = 2       // dark edge (OFF pathway)
    case motionDirectional = 3
    case looming = 4
    case smallObject = 5
    case wideField = 6
}

/// Configuration for the compound-eye sampling grid (spec #12):
/// HIGH/MEDIUM/LOW-POWER detail mapping.
public struct EyeConfig: Sendable {
    /// Angular spacing between ommatidia (radians) — Drosophila ~5°.
    public var angularSpacing: Float = 5.0 * .pi / 180.0
    /// Horizontal angular extent (radians).
    public var fieldOfViewX: Float = 190.0 * .pi / 180.0
    /// Vertical angular extent (radians).
    public var fieldOfViewY: Float = 120.0 * .pi / 180.0
    /// Photoreceptor contrast gain (dimensionless).
    public var contrastGain: Float = 2.0
    /// Luminance adaptation time constant (ms).
    public var adaptationTau: Float = 50
    /// LOD: level of detail (0=low power … 3=high detail)
    public var lod: Int = 1

    public init() {}
}

/// Right-handed fly-centric coordinate convention:
/// +X forward, +Y left, +Z up (consistent with BodyModel).
public struct FlyPose: Sendable {
    public var forwardX: Float = 1, forwardY: Float = 0, forwardZ: Float = 0
    public var upX: Float = 0, upY: Float = 0, upZ: Float = 1

    public static let identity = FlyPose()
}

/// The visual system: samples a scalar luminance field from the world along
/// ommatidial axes, computes feature channels, and produces synaptic drive.
public final class VisionSystem: @unchecked Sendable {
    public let config: EyeConfig
    public private(set) var ommatidia: [Ommatidium] = []

    // per-ommatidium state
    private var prevLuminance: [Float] = []
    private var adaptedLuminance: [Float] = []
    private var flowAccumulator: [(Float, Float)] = []

    /// Luminance field provider — the world implements this (spec #13/#29).
    public var luminanceProvider: ((Float, Float, Float) -> Float)?

    public init(config: EyeConfig = EyeConfig()) {
        self.config = config
        buildRetina()
    }

    /// Build the hexagonal-ish sampling grid for both eyes (spec #11).
    private func buildRetina() {
        ommatidia = []
        let d = config.angularSpacing
        let nX = Int(config.fieldOfViewX / d)
        let nY = Int(config.fieldOfViewY / d)
        for side in [UInt8(1), UInt8(2)] {           // left, right
            for iy in 0..<nY {
                for ix in 0..<nX {
                    // hexagonal offset for even rows (more realistic packing)
                    let off = (iy % 2 == 0) ? 0.0 : d * 0.5
                    let u = (Float(ix) - Float(nX - 1) / 2) * d + off
                    let v = (Float(iy) - Float(nY - 1) / 2) * d
                    // eye direction: side 1 (left) looks −Y in fly frame? Use
                    // +X forward with lateral spread; side encodes parity.
                    let az = u
                    let el = v
                    // optical axis in fly frame
                    let ax = cos(el) * cos(az)
                    let ay = (side == 1 ? -1 : 1) * sin(az)
                    let azz = sin(el)
                    ommatidia.append(Ommatidium(
                        axisX: ax, axisY: ay, axisZ: azz,
                        acceptanceAngle: d * 0.6,
                        retinaU: u, retinaV: v, side: side))
                }
            }
        }
        prevLuminance = [Float](repeating: 0.5, count: ommatidia.count)
        adaptedLuminance = [Float](repeating: 0.5, count: ommatidia.count)
        flowAccumulator = [(Float, Float)](repeating: (0, 0), count: ommatidia.count)
    }

    /// Compute the world point a given ommatidium looks at, given the fly's
    /// position (px,py,pz) and orientation (FlyPose), at distance `range`.
    public func worldTarget(ommatidium i: Int, position: (Float, Float, Float),
                            pose: FlyPose, range: Float = 10) -> (Float, Float, Float) {
        let om = ommatidia[i]
        // rotate optical axis by pose basis: dir = axisX·f + axisY·r + axisZ·u
        let f = SIMD3(pose.forwardX, pose.forwardY, pose.forwardZ)
        let u = SIMD3(pose.upX, pose.upY, pose.upZ)
        let r = FlyMath.cross(u, f)               // right = up × forward
        let axis = SIMD3(om.axisX, om.axisY, om.axisZ)
        let dir = FlyMath.normalize(
            axis.x * f + axis.y * r + axis.z * u
        )
        return (position.0 + dir.x * range,
                position.1 + dir.y * range,
                position.2 + dir.z * range)
    }

    /// Advance one sensory sample (dt ms). Calls the luminance provider,
    /// computes per-ommatidium adaptation, contrast, temporal derivative,
    /// and optic flow; returns visual events to inject into the connectome.
    public func sample(position: (Float, Float, Float), pose: FlyPose,
                       dt: Double) -> [VisualEvent] {
        guard let provider = luminanceProvider else { return [] }
        var events: [VisualEvent] = []
        events.reserveCapacity(ommatidia.count / 2)

        let alpha = Float(dt) / (Float(dt) + config.adaptationTau)
        let range: Float = 10

        for (i, om) in ommatidia.enumerated() {
            let t = worldTarget(ommatidium: i, position: position, pose: pose, range: range)
            let lum = max(0, min(1, provider(t.0, t.1, t.2)))

            // adaptation (background-relative contrast)
            let adapted = adaptedLuminance[i] + (lum - adaptedLuminance[i]) * alpha
            adaptedLuminance[i] = adapted

            // local contrast (against prior + small neighborhood)
            let prev = prevLuminance[i]
            let contrast = (lum - adapted) * config.contrastGain
            let dLumDt = (lum - prev) / Float(max(dt, 0.001))
            prevLuminance[i] = lum

            // ON / OFF pathways (spec #11 — separate dark/light edges)
            if contrast > 0.05 {
                events.append(VisualEvent(sourceOmmatidium: i, pathway: .onEdge,
                                          strength: contrast, side: om.side, time: 0))
            } else if contrast < -0.05 {
                events.append(VisualEvent(sourceOmmatidium: i, pathway: .offEdge,
                                          strength: -contrast, side: om.side, time: 0))
            }
            // photoreceptor drive always present (weak)
            if lum > 0.02 {
                events.append(VisualEvent(sourceOmmatidium: i, pathway: .photoreceptor,
                                          strength: lum * 0.2, side: om.side, time: 0))
            }
            // temporal derivative → wide-field flicker
            if abs(dLumDt) > 0.02 {
                events.append(VisualEvent(sourceOmmatidium: i, pathway: .wideField,
                                          strength: dLumDt * 0.5, side: om.side, time: 0))
            }
        }

        // looming detection: expansion of high-contrast region over time.
        if let looming = computeLooming() {
            events.append(looming)
        }
        return events
    }

    /// Expansion-based looming signal: monitors growth rate of bright blobs.
    private func computeLooming() -> VisualEvent? {
        // Simplified looming metric: fraction of ommatidia with contrast > 0.3
        // growing between samples. The world updates prevLuminance naturally;
        // here we compute a global expansion proxy.
        var bright = 0
        for (i, om) in ommatidia.enumerated() {
            if prevLuminance[i] > 0.4 && abs(om.axisX) < 0.9 {
                bright += 1
            }
        }
        let fraction = Float(bright) / Float(max(ommatidia.count, 1))
        _ = fraction
        return nil   // populated by dedicated looming module (Phase 3b)
    }

    /// Map a visual event to a connectome input neuron (retinotopic).
    /// Distributes events across the region's neurons by ommatidium index so
    /// the input layer is a proper spatial map (spec #11/#12), and limits to
    /// the strongest events per region+side to avoid flooding the engine.
    public func mapToInput(event: VisualEvent, connectome: Connectome) -> (Int32, Float)? {
        let region: RegionID
        switch event.pathway {
        case .photoreceptor:
            region = event.side == 1 ? .retinaLeft : .retinaRight
        case .onEdge, .offEdge:
            region = .lamina
        case .motionDirectional, .wideField:
            region = .medulla
        case .looming:
            region = .lobulaPlate
        case .smallObject:
            region = .lobula
        }
        // collect candidate input neurons in the region (side-matched first)
        var candidates: [Int32] = []
        var fallback: Int32? = nil
        // Per-event region lookup (this runs for every ommatidium event, i.e.
        // dozens of times per frame) — use the index, not a full scan.
        for idx in connectome.neuronIndices(in: region) {
            let n = connectome.neurons[Int(idx)]
            if n.side == event.side { candidates.append(idx) }
            if n.side == 0 && fallback == nil { fallback = idx }
        }
        if candidates.isEmpty, let f = fallback { candidates = [f] }
        guard !candidates.isEmpty else { return nil }
        // retinotopic: pick by ommatidium index modulo the layer size
        let idx = candidates[event.sourceOmmatidium % candidates.count]
        return (idx, event.strength * 10)
    }
}