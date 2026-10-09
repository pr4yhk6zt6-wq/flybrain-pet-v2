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
//    - wide-field flicker (the temporal derivative feeding the medulla)
//    - looming (expansion → threat drive), the escape channel
//
//  NOT yet emitted here, though the `VisualPathway` enum and `mapToInput`
//  already reserve targets for them (do not claim otherwise until they are
//  implemented — that gap is exactly how this file's looming claim went
//  unfulfilled while looking complete):
//    - motionDirectional (T4/T5 direction-selective optic flow)
//    - smallObject (lobula small-target motion, Lévy-flight-like search)
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

/// The first opaque surface a ray meets, as the world reports it.
///
/// The eye cannot detect an approach from a single luminance sample at a fixed
/// distance: the ray passes straight through a body that is nearer than the
/// sample range and lands on the background behind it, so a body filling the
/// whole visual field reports exactly the same value as empty sky. Answering
/// "what did this ray hit, and was it a moving body" is what makes expansion
/// measurable at all.
public struct RayHit: Sendable {
    /// Distance from the ray origin to the surface.
    public let distance: Float
    /// The surface belongs to a moving body — the only thing that can loom.
    public let isMovingBody: Bool
    /// Luminance of the surface itself (0 = black silhouette).
    public let luminance: Float

    public init(distance: Float, isMovingBody: Bool, luminance: Float) {
        self.distance = distance
        self.isMovingBody = isMovingBody
        self.luminance = luminance
    }
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

    /// Where a ray from the eye first meets an opaque surface. Optional; when
    /// absent the eye sees only the luminance field at a fixed range, which is
    /// a static scene by construction and cannot produce looming.
    ///
    /// This exists because sampling a single point at a FIXED distance cannot
    /// see an approaching body: the ray passes through it and lands on the
    /// background, so a body that fills the whole visual field reports exactly
    /// the same luminance as an empty sky. The eye has to ask what the ray hit,
    /// not what the world looks like at one arbitrary depth.
    public var rayProvider: ((SIMD3<Float>, SIMD3<Float>) -> RayHit?)?

    /// Fraction of the eye covered by a moving body, one sample ago. Looming
    /// is the GROWTH of this number, so it must survive between samples.
    private var previousCoverage: Float = 0
    private(set) var coverage: Float = 0
    /// Slow estimate of the scene's baseline occlusion (a static wall in the
    /// way, say). Subtracting it keeps a constant occluder from reading as a
    /// loom, the same way the visual system adapts to background luminance.
    private var baselineCoverage: Float = 0
    /// False until the first loom sample, so the first sample cannot look like
    /// an expansion from zero.
    private var loomInitialised = false

    // MARK: - Loom model

    /// Expansion rate (fraction of the eye covered per second) at which the
    /// loom drive saturates.
    ///
    /// Thresholding the EXPANSION RATE is equivalent to thresholding TIME TO
    /// CONTACT: a body closing at constant speed covers a fraction that grows
    /// roughly as `1/tau`, so `rate > r` is `tau < 1/r`. That fixed
    /// angular-size / fixed-time-to-contact trigger is what loom-selective
    /// neurons report — Klapoetke et al. 2017, *Nature* 542:469
    /// ("Ultra-selective looming detection from radial motion opponency",
    /// PMID 29120418) identifies LPLC2 in the lobula plate as the Drosophila
    /// detector, and the peak response of such cells tracks a fixed time
    /// before collision against the R/v ratio.
    ///
    /// INFERRED constant: the literature fixes the FUNCTION (a threshold on
    /// expansion, i.e. on time to contact), not this particular number.
    public var loomExpansionReference: Float = 0.7      // per second
    /// Smallest covered fraction that can count as a loom. A speck expanding
    /// fast is still a speck, so the expansion has to be carried on a real
    /// area of retina before it reads as a threat.
    public var loomMinCoverage: Float = 0.03
    /// Time constant of the slow estimate of static occlusion (ms). A wall
    /// that is simply *there* must not read as an approach.
    public var loomBaselineTauMs: Float = 3000

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
        let dir = rayDirection(i, pose: pose)
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
        let eye = SIMD3<Float>(position.0, position.1, position.2)

        // A moving body is only visible if the eye asks what the ray HIT. The
        // luminance field alone cannot show it: sampling at a fixed range steps
        // over a nearer body and reads the background behind it.
        var covered = 0

        for (i, om) in ommatidia.enumerated() {
            let t = worldTarget(ommatidium: i, position: position, pose: pose, range: range)
            var lum = max(0, min(1, provider(t.0, t.1, t.2)))

            // Ray cast from the eye along the ommatidial axis. If it hits a
            // body that is closer than the fixed sample range, that body IS
            // what this ommatidium sees, so it both sets the luminance and
            // counts toward how much of the eye the body covers.
            if let hit = rayProvider?(eye, rayDirection(i, pose: pose)) {
                if hit.distance < range {
                    lum = max(0, min(1, hit.luminance))
                    if hit.isMovingBody { covered += 1 }
                }
            }

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

        // looming detection: expansion of the covered retinal area over time.
        if let looming = computeLooming(coveredFraction: Float(covered) / Float(max(ommatidia.count, 1)),
                                        dtMs: Float(dt)) {
            events.append(looming)
        }
        return events
    }

    /// Unit direction of an ommatidium's optical axis in world space under `pose`.
    private func rayDirection(_ i: Int, pose: FlyPose) -> SIMD3<Float> {
        let om = ommatidia[i]
        let f = SIMD3(pose.forwardX, pose.forwardY, pose.forwardZ)
        let u = SIMD3(pose.upX, pose.upY, pose.upZ)
        let r = FlyMath.cross(u, f)               // right = up × forward
        let axis = SIMD3(om.axisX, om.axisY, om.axisZ)
        return FlyMath.normalize(axis.x * f + axis.y * r + axis.z * u)
    }

    /// Expansion-based looming signal (spec #11, #23).
    ///
    /// Looming is the GROWTH of the retinal area covered by a moving body, not
    /// a contrast threshold: a body on a collision course spreads radially
    /// over the eye, and the rate of that spread is what the lobula plate
    /// reports. A body that is merely present — or a static wall — covers a
    /// constant fraction and must produce nothing, so a slow baseline estimate
    /// of static occlusion is subtracted before the rate is taken.
    ///
    /// Returns nil unless the covered area is real (`loomMinCoverage`) and
    /// actually growing. This is the difference between seeing a threat and
    /// seeing an insect sitting still: only expansion is a loom.
    private func computeLooming(coveredFraction: Float, dtMs: Float) -> VisualEvent? {
        // One sample of history is needed before a rate exists.
        guard loomInitialised else {
            loomInitialised = true
            baselineCoverage = coveredFraction
            previousCoverage = coveredFraction
            coverage = coveredFraction
            return nil
        }
        coverage = coveredFraction

        // Slow baseline of static occlusion (a wall in the way): adapts at the
        // loom time constant so a constant occluder settles out and does not
        // read as an approach.
        let beta = Float(min(1, Double(dtMs) / Double(max(loomBaselineTauMs, 1))))
        baselineCoverage += (coveredFraction - baselineCoverage) * beta

        let dtSec = max(dtMs / 1000, 0.0001)
        let expansion = (coveredFraction - previousCoverage) / dtSec
        previousCoverage = coveredFraction

        // Only expansion above the static baseline counts. A body that stops
        // growing (or recedes) yields no drive.
        let signal = max(coveredFraction - baselineCoverage, 0)
        guard signal >= loomMinCoverage, expansion > 0 else { return nil }
        let drive = min(expansion / max(loomExpansionReference, 0.0001), 1)
        guard drive > 0 else { return nil }
        return VisualEvent(sourceOmmatidium: 0, pathway: .looming,
                           strength: drive, side: 0, time: 0)
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