//
//  World.swift
//  FlyBrainCore
//
//  3D world (spec #29, #30): every object exposes sensory properties —
//  the world is the source of stimulation, never the source of commands.
//
//    LIGHT → visual intensity + wavelength approximation        [implemented]
//    OBSTACLE / WALL → visual (occludes rays) + solid AABB      [implemented]
//    MOVING OBJECT → retinal expansion (looming)                [implemented]
//    ODOR SOURCE → odor concentration field                     [implemented]
//
//  Partial / not yet an entity in its own right (the header used to list these
//  as if they existed; only the odor half is real today):
//    FOOD → odor only (no taste/texture/energy term yet)
//    WATER → odor only (no contact/hydration term yet)
//
//  Implements WorldProvider so SimulationCore can sample luminance/odor/
//  temperature from real world state.
//

import Foundation

/// A point light source.
public struct LightSource: Sendable {
    public var position: SIMD3<Float>
    public var intensity: Float      // 0..1
    public var color: SIMD3<Float>   // RGB wavelength approximation

    public init(position: SIMD3<Float>, intensity: Float,
                color: SIMD3<Float> = SIMD3(1, 1, 1)) {
        self.position = position
        self.intensity = min(max(intensity, 0), 1)
        self.color = color
    }

    /// Illuminance at a world point (inverse-square with soft falloff).
    public func illuminance(at p: SIMD3<Float>) -> Float {
        let d = FlyMath.length(p - position)
        guard d > 0.01 else { return intensity }
        // ambient-ish soft falloff: I / (1 + d²)
        return intensity / (1 + d * d)
    }
}

/// An odor source (food, fermentation, aversive substance — spec #13).
public struct OdorSource: Sendable {
    public enum Kind: Int, Sendable {
        case food = 0
        case water = 1
        case fermentation = 2
        case aversive = 3
        case pheromone = 4
    }

    public var position: SIMD3<Float>
    public var kind: Kind
    public var emissionRate: Float      // source strength
    public var diffusionConstant: Float // spatial spread
    /// How much food is here, in reserve units (1.0 = one full meal).
    ///
    /// Food is finitely available, which is what makes a feeding loop a loop
    /// rather than a tap: a source that never depletes lets the fly feed
    /// forever, so `energy` becomes monotone and hunger is unreachable again.
    /// This is what ingestion decrements — and the amount is REMOVED, so the
    /// energy the fly gains is bounded by what it actually ate.
    ///
    /// Water does not deplete: drinking a puddle is not a finite-resource
    /// problem at the scale of one fly, and the app exposes `dropWater` as a
    /// standing source.
    public var reserve: Float
    /// Patch radius (mm): food is a patch with extent, not a mathematical point.
    ///
    /// This is not cosmetic. The fly stands ~0.8 mm up with its tarsi at the
    /// body position and its extended labellum ~1.2 mm FORWARD of them
    /// (`BodyModel`: head at +0.6, proboscis 0.9 at full extension). A point
    /// source can therefore be under the feet or at the mouth but never both, so
    /// with a zero-radius source the bootstrap the whole gustatory pathway rests
    /// on — taste it with your feet, extend, confirm with your labellum — cannot
    /// happen at one spot. Real food is a patch; giving it a radius is the
    /// smaller lie, and it is stated here rather than hidden in a constant.
    ///
    /// Radius default is 1.1 mm so the patch spans the 1.23 mm from the tarsi to
    /// the extended labellum (`tools/mirror_feeding_loop.py` fails if it stops
    /// doing so). A ~2.2 mm food blob is unremarkable next to a 2.5-3 mm fly,
    /// and it is what makes the pathway's bootstrap physically possible: the
    /// same patch has to be touchable by the feet AND the mouth, which no
    /// zero-radius source can be.
    public var radius: Float

    public init(position: SIMD3<Float>, kind: Kind, emissionRate: Float = 1,
                diffusionConstant: Float = 1, reserve: Float = 1,
                radius: Float = 1.1) {
        self.position = position
        self.kind = kind
        self.emissionRate = emissionRate
        self.diffusionConstant = diffusionConstant
        self.reserve = max(reserve, 0)
        self.radius = max(radius, 0)
    }

    /// Distance from `p` to the nearest point of the patch (0 when inside).
    ///
    /// The contact senses measure this, not centre distance: standing on the
    /// edge of a food patch is standing ON the food, and the labellum touching
    /// the edge is touching it.
    public func distanceToPatch(from p: SIMD3<Float>) -> Float {
        max(FlyMath.length(p - position) - radius, 0)
    }

    /// Whether this source can be swallowed at all. An aversive substance
    /// carries taste but is not food; a pheromone is a courtship signal. Only a
    /// phagostimulant has anything to ingest, so `reserve` and `emissionRate`
    /// are not silently treated as nutrition for every kind.
    public var isNutritive: Bool {
        switch kind {
        case .food, .fermentation: return true
        case .water, .aversive, .pheromone: return false
        }
    }

    /// Concentration at a point (gaussian-ish falloff; bilateral sampling
    /// happens per antenna in SensoryInterface).
    ///
    /// Scaled by the remaining reserve: a depleted source stops smelling, so
    /// the fly's own feeding changes what it can sense next. Without this the
    /// odor field would keep advertising a source that has nothing left.
    public func concentration(at p: SIMD3<Float>) -> Float {
        let d = FlyMath.length(p - position)
        return emissionRate * reserve * exp(-d * d / (2 * diffusionConstant))
    }

    /// Contact reach of the labellum's sensilla (mm).
    ///
    /// This is a CONTACT sense's reach, not a smell radius: ~0.25 mm is the
    /// scale of a labellum sensillum. It is declared once and used by BOTH the
    /// taste sense and ingestion, because a fly that could swallow a source it
    /// could not taste (or vice versa) would have two different mouths.
    public static let contactReach: Float = 0.25

    /// Taste acceptance when the proboscis tip touches `at`.
    ///
    /// This is a CONTACT sense and is modelled as one: the falloff length is a
    /// fixed ~0.25 mm — the scale of a labellum sensillum's reach — not the
    /// authored `diffusionConstant`, which describes how far the volatile
    /// plume carries. Using the odor radius here would let the fly taste food
    /// it is nowhere near, which is the reduction that makes "taste" a second
    /// odor channel.
    ///
    /// Sign comes from the substance, not from a behavior:
    ///   food / fermentation → + (phagostimulant; both are sugar/yeast substrates)
    ///   aversive            → − (bitter / deterrent)
    ///   water / pheromone   → 0 (drives drinking / courtship, not feeding)
    /// `pheromone` is deliberately excluded: it is a courtship signal, and a
    /// fly that "tastes" a mate is a category error.
    ///
    /// Note there is no `texture` term here, and that is a deliberate limitation
    /// rather than a claim that texture is imaginary. The substrate's texture IS
    /// a real food cue in *Drosophila* — Li & Montell, *Neuron* 2022
    /// (PMID 36386873) show labellar mechanosensilla report food grittiness via
    /// bristle deflection, and flies reject gritty food. So texture is a genuine
    /// sense this model does not have: it lives on the mechanosensory channel
    /// (`SensoryInterface.touchAfferent`) and would need a per-source
    /// `grit`/hardness field to be represented. It is NOT represented, and
    /// saying so is better than adding a `texture: Float` that nothing reads —
    /// the failure mode this project keeps finding. Mechanosensation of the
    /// substrate that IS modelled arrives through the tarsal load channel.
    public func tasteAcceptance(at p: SIMD3<Float>) -> Float {
        let valence: Float
        switch kind {
        case .food, .fermentation: valence = 1
        case .aversive: valence = -1
        case .water, .pheromone: return 0
        }
        // A depleted source has nothing left to taste; otherwise the fly would
        // keep finding food at a spot it has already eaten bare.
        // Distance is to the patch EDGE (see `distanceToPatch`): the labellum
        // touching the rim of a food patch is touching food.
        let d = distanceToPatch(from: p)
        let falloff = max(1 - d / Self.contactReach, 0)
        return valence * emissionRate * reserve * falloff * falloff
    }
}

/// A physical object the fly can touch / bump into (vision + tactile).
public struct Obstacle: Sendable {
    public var position: SIMD3<Float>
    public var size: SIMD3<Float>
    public var isSolid: Bool = true

    public init(position: SIMD3<Float>, size: SIMD3<Float>, isSolid: Bool = true) {
        self.position = position
        self.size = size
        self.isSolid = isSolid
    }
    public func distance(to p: SIMD3<Float>) -> Float {
        let half = size * 0.5
        let dx = max(abs(p.x - position.x) - half.x, 0)
        let dy = max(abs(p.y - position.y) - half.y, 0)
        let dz = max(abs(p.z - position.z) - half.z, 0)
        return FlyMath.length(SIMD3(dx, dy, dz))
    }

    /// Occupancy test (for collision, spec #30).
    public func contains(_ p: SIMD3<Float>) -> Bool {
        let half = size * 0.5
        return abs(p.x - position.x) <= half.x
            && abs(p.y - position.y) <= half.y
            && abs(p.z - position.z) <= half.z
    }
}

/// A moving object — the fourth world entity this file's header has always
/// promised (`MOVING OBJECT → visual motion + looming`) but never defined.
///
/// It is a sphere rather than a box because the visual system only needs one
/// geometric property from it: how much of the eye it covers from a given
/// viewpoint. For a sphere that is the angular subtense `2·asin(r/d)`, which
/// grows without bound as `d → r` — the definition of a loom. A box would
/// need a viewing-angle-dependent silhouette to say the same thing.
///
/// The object does not "tell" the fly anything: it exposes a dark body, and
/// the eye discovers the expansion by seeing more ommatidia covered over time.
public struct MovingObject: Sendable {
    public var position: SIMD3<Float>
    /// Velocity in world units per second. Set this toward the fly's position
    /// to author an approach; nothing here aims it for you.
    public var velocity: SIMD3<Float>
    public var radius: Float
    /// Luminance of the body itself (0 = black silhouette, 1 = white).
    /// Flies detect both polarities, so this is authorable, not fixed.
    public var luminance: Float

    public init(position: SIMD3<Float>, velocity: SIMD3<Float>,
                radius: Float = 0.6, luminance: Float = 0) {
        self.position = position
        self.velocity = velocity
        self.radius = max(radius, 0.001)
        self.luminance = min(max(luminance, 0), 1)
    }

    /// True when `p` is inside the body.
    public func contains(_ p: SIMD3<Float>) -> Bool {
        FlyMath.length(p - position) <= radius
    }

    /// Angular subtense (full angle, radians) seen from `eye`, or 0 if the eye
    /// is inside the body. This is the quantity a loom makes grow.
    public func angularSubtense(from eye: SIMD3<Float>) -> Float {
        let d = FlyMath.length(position - eye)
        guard d > radius else { return 0 }
        return 2 * asin(min(radius / d, 1))
    }

    /// Time until contact at the current closing speed (-1 = receding).
    /// `tau` in the literature is the optical version of this; here it is the
    /// geometric quantity the eye's expansion rate approximates.
    public func timeToContact(from eye: SIMD3<Float>) -> Float {
        let rel = eye - position
        let d = FlyMath.length(rel)
        guard d > 0.0001 else { return 0 }
        let closing = FlyMath.dot(velocity, rel) / d   // + toward the eye
        guard closing > 0.0001 else { return -1 }
        return max((d - radius) / closing, 0)
    }
}

/// Full world scene — implements WorldProvider for the closed loop.
public final class World: WorldProvider, @unchecked Sendable {
    public private(set) var lights: [LightSource] = []
    public private(set) var odorSources: [OdorSource] = []
    public private(set) var obstacles: [Obstacle] = []
    /// Bodies that move. Only these can generate looming: a static scene has a
    /// constant retinal image, however cluttered.
    public private(set) var movingObjects: [MovingObject] = []
    /// Simulated seconds elapsed, advanced by `step`.
    public private(set) var time: Double = 0
    /// Ground plane y=0 luminance/texture approximation.
    public var ambientLight: Float = 0.3
    public var groundTemperatureC: Float = 25
    /// Global drone: extra background luminance (was "world sky").
    public var backgroundLuminance: Float = 0.4

    public init() {}

    // MARK: - Authoring (player interaction, spec #42)

    @discardableResult
    public func addLight(_ l: LightSource) -> LightSource { lights.append(l); return l }

    @discardableResult
    public func addOdorSource(_ o: OdorSource) -> OdorSource { odorSources.append(o); return o }

    @discardableResult
    public func addObstacle(_ o: Obstacle) -> Obstacle { obstacles.append(o); return o }

    @discardableResult
    public func addMovingObject(_ m: MovingObject) -> MovingObject {
        movingObjects.append(m); return m
    }

    public func removeAllMovingObjects() { movingObjects.removeAll() }

    /// Advance world time and integrate the moving bodies. Called once per
    /// simulation step by SimulationCore, before the eye samples — otherwise
    /// the retina would compare two samples of the same instant.
    public func step(dtSeconds: Double) {
        let dt = Float(max(dtSeconds, 0))
        time += Double(dt)
        for i in movingObjects.indices {
            movingObjects[i].position += movingObjects[i].velocity * dt
        }
    }

    // MARK: - Ingestion (spec #29: the world supplies matter, not decisions)

    /// Remove up to `amount` of edible reserve from the nearest source that can
    /// reach `p`, and return what was ACTUALLY taken (0 if there is nothing
    /// edible in reach).
    ///
    /// This is the world half of feeding, and it returns a measured quantity
    /// rather than a boolean, so the energy the fly gains is the amount that
    /// left the food. A "fed = true" flag would make the meal a constant and
    /// the loop would no longer conserve anything.
    ///
    /// Only a `isNutritive` source with reserve left yields anything, and only
    /// within the same contact reach the taste sense uses — the fly cannot
    /// swallow a source it could not touch.
    @discardableResult
    public func ingest(at p: SIMD3<Float>, amount: Float) -> Float {
        guard amount > 0 else { return 0 }
        var best = -1
        var bestDist = Float.greatestFiniteMagnitude
        for (i, s) in odorSources.enumerated() where s.isNutritive && s.reserve > 0 {
            let d = s.distanceToPatch(from: p)
            guard d <= OdorSource.contactReach else { continue }
            if d < bestDist { bestDist = d; best = i }
        }
        guard best >= 0 else { return 0 }
        let taken = min(amount, odorSources[best].reserve)
        odorSources[best].reserve -= taken
        return taken
    }

    /// How much edible reserve is within contact reach of `p` (for telemetry).
    public func edibleReserve(at p: SIMD3<Float>) -> Float {
        var total: Float = 0
        for s in odorSources where s.isNutritive && s.reserve > 0 {
            if s.distanceToPatch(from: p) <= OdorSource.contactReach {
                total += s.reserve
            }
        }
        return total
    }

    public func removeAllOdorSources() { odorSources.removeAll() }

    /// Toggle the light set (player interaction — environment only, spec #42).
    public func removeAllLights() { lights.removeAll() }

    // MARK: - WorldProvider (compositionally implements the protocol)

    public func luminance(atX: Float, y: Float, z: Float) -> Float {
        var lum = backgroundLuminance + ambientLight * 0.2
        let p = SIMD3(atX, y, z)
        for light in lights {
            lum += light.illuminance(at: p) * 0.8
        }
        // obstacle shadow: light blocked by solid boxes
        for o in obstacles where o.isSolid {
            let toLight = lights.contains { $0.position.y > p.y && o.contains(p) }
            if toLight { lum *= 0.3 }
        }
        // A moving body emits its own luminance and OCCLUDES the background,
        // so a point inside it reports the body rather than the sky behind it.
        // Without this the ray at the sampled point passed straight through
        // the object and the eye could not see an approaching body at all.
        if let body = movingObjects.first(where: { $0.contains(p) }) {
            lum = body.luminance
        }
        return min(max(lum, 0), 1)
    }

    /// First opaque surface along a ray from the eye (spec #11). A moving body
    /// wins over everything behind it; a solid obstacle occludes the
    /// background but can never loom, so it is reported as a non-moving hit.
    ///
    /// This is what lets the eye see an approach at all: sampling the
    /// luminance field at a fixed range would step straight over a nearer body
    /// and read the sky behind it, making a body that fills the visual field
    /// look identical to an empty one.
    public func raycast(origin: SIMD3<Float>, direction: SIMD3<Float>) -> RayHit? {
        let d = FlyMath.normalize(direction)
        var best: RayHit? = nil
        func consider(_ hit: RayHit) {
            if best == nil || hit.distance < best!.distance { best = hit }
        }
        // Moving bodies: ray/sphere intersection (nearest root in front).
        for m in movingObjects {
            let oc = origin - m.position
            let b = FlyMath.dot(oc, d)
            let c = FlyMath.dot(oc, oc) - m.radius * m.radius
            let disc = b * b - c
            if disc < 0 { continue }
            let sq = disc.squareRoot()
            let t0 = -b - sq
            let t1 = -b + sq
            let t = t0 > 0.0001 ? t0 : (t1 > 0.0001 ? t1 : -1)
            if t > 0.0001 {
                consider(RayHit(distance: t, isMovingBody: true, luminance: m.luminance))
            }
        }
        // Solid obstacles: ray/AABB slab test.
        for o in obstacles where o.isSolid {
            let half = o.size * 0.5
            let lo = o.position - half
            let hi = o.position + half
            var tmin: Float = 0.0001
            var tmax: Float = .greatestFiniteMagnitude
            var miss = false
            for axis in 0..<3 {
                let o0: Float = axis == 0 ? origin.x : (axis == 1 ? origin.y : origin.z)
                let dd: Float = axis == 0 ? d.x : (axis == 1 ? d.y : d.z)
                let l: Float = axis == 0 ? lo.x : (axis == 1 ? lo.y : lo.z)
                let h: Float = axis == 0 ? hi.x : (axis == 1 ? hi.y : hi.z)
                if abs(dd) < 0.000001 {
                    if o0 < l || o0 > h { miss = true; break }
                    continue
                }
                var n = (l - o0) / dd
                var f = (h - o0) / dd
                if n > f { swap(&n, &f) }
                tmin = max(tmin, n)
                tmax = min(tmax, f)
                if tmin > tmax { miss = true; break }
            }
            if !miss, tmax > 0.0001 {
                consider(RayHit(distance: max(tmin, 0.0001), isMovingBody: false,
                                luminance: 0))
            }
        }
        return best
    }

    public func odorConcentration(atX: Float, y: Float, z: Float) -> (left: Float, right: Float) {
        let p = SIMD3(atX, y, z)
        var totalL: Float = 0
        var totalR: Float = 0
        for o in odorSources {
            // small bilateral offset — antennas are ~0.18 mm apart (spec #13)
            totalL += o.concentration(at: p + SIMD3(0, 0.18, 0))
            totalR += o.concentration(at: p + SIMD3(0, -0.18, 0))
        }
        return (left: min(totalL, 1), right: min(totalR, 1))
    }

    public func temperature(atX: Float, y: Float, z: Float) -> Float {
        // simple: ground temp + tiny height gradient
        _ = (atX, z)
        return groundTemperatureC - max(0, y) * 0.2
    }

    /// This world does give up matter — the loop can actually feed here.
    public var isConsumable: Bool { true }

    public func ingest(atX: Float, y: Float, z: Float, amount: Float) -> Float {
        ingest(at: SIMD3(atX, y, z), amount: amount)
    }

    // MARK: - Taste (contact chemoreception, spec #14)

    /// Taste acceptance at a point: **positive** when the point is on a
    /// phagostimulant source (food), **negative** on an aversive one, and 0
    /// where there is nothing the labellum could touch. Water is neither — it
    /// must not read as a food taste, because an animal that cannot tell sugar
    /// from water is not tasting.
    ///
    /// HONESTY: the original sentence here said water "drives drinking through
    /// the hydration term". That consumer does not exist. `InternalState.drink()`
    /// has no caller anywhere in the app or the pipeline (grep-verified), and
    /// `OdorSource.Kind.water` is an ODOR source. Water is currently odor-only;
    /// nothing hydrates. Recorded because a comment describing behaviour that
    /// was never wired is how a gap survives review.
    ///
    /// The field is SHORTER-RANGE than odor by construction (contact sense),
    /// and it does not propagate as a cloud — see `OdorSource.tasteAcceptance`.
    public func tasteAcceptance(atX: Float, y: Float, z: Float) -> Float {
        let p = SIMD3(atX, y, z)
        var total: Float = 0
        for o in odorSources {
            total += o.tasteAcceptance(at: p)
        }
        return min(max(total, -1), 1)
    }

    // MARK: - Collision / physics helpers (spec #30)

    /// Resolve a candidate position against solid obstacles (slide along axis).
    public func resolveCollision(position p: inout SIMD3<Float>, radius: Float = 0.15) {
        for o in obstacles where o.isSolid {
            let half = o.size * 0.5
            // clamp into box, push out
            let nearest = SIMD3(
                min(max(p.x, o.position.x - half.x), o.position.x + half.x),
                min(max(p.y, o.position.y - half.y), o.position.y + half.y),
                min(max(p.z, o.position.z - half.z), o.position.z + half.z))
            let diff = p - nearest
            let d = FlyMath.length(diff)
            if d < radius {
                if d > 0.0001 {
                    p = nearest + diff / d * radius
                } else {
                    // center inside box: push along smallest penetration axis
                    let pen = SIMD3(
                        half.x - abs(p.x - o.position.x),
                        half.y - abs(p.y - o.position.y),
                        half.z - abs(p.z - o.position.z))
                    let axis = argmin(pen)
                    let s: SIMD3<Float> = SIMD3(axis == 0 ? (p.x > o.position.x ? 1 : -1) : 0,
                                                axis == 1 ? (p.y > o.position.y ? 1 : -1) : 0,
                                                axis == 2 ? (p.z > o.position.z ? 1 : -1) : 0)
                    p += s * (pen[axis] + radius)
                }
            }
        }
        // ground plane y >= 0 for walking surfaces (spec #30)
        if p.y < 0 { p.y = 0 }
    }

    private func argmin(_ v: SIMD3<Float>) -> Int {
        if v.x <= v.y && v.x <= v.z { return 0 }
        if v.y <= v.z { return 1 }
        return 2
    }
}