//
//  World.swift
//  FlyBrainCore
//
//  3D world (spec #29, #30): every object exposes sensory properties —
//  the world is the source of stimulation, never the source of commands.
//
//    FOOD → odor + taste + texture + energy
//    WATER → odor + contact + hydration
//    LIGHT → visual intensity + wavelength approximation
//    OBSTACLE / WALL → visual + tactile
//    MOVING OBJECT → visual motion + looming
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

    public init(position: SIMD3<Float>, kind: Kind, emissionRate: Float = 1,
                diffusionConstant: Float = 1) {
        self.position = position
        self.kind = kind
        self.emissionRate = emissionRate
        self.diffusionConstant = diffusionConstant
    }

    /// Concentration at a point (gaussian-ish falloff; bilateral sampling
    /// happens per antenna in SensoryInterface).
    public func concentration(at p: SIMD3<Float>) -> Float {
        let d = FlyMath.length(p - position)
        return emissionRate * exp(-d * d / (2 * diffusionConstant))
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

/// Full world scene — implements WorldProvider for the closed loop.
public final class World: WorldProvider, @unchecked Sendable {
    public private(set) var lights: [LightSource] = []
    public private(set) var odorSources: [OdorSource] = []
    public private(set) var obstacles: [Obstacle] = []
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
        return min(max(lum, 0), 1)
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