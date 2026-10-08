//
//  RenderCamera.swift
//  FlyBrainCore
//
//  Everything the renderer needs to turn a `RenderBounds` into pixels, minus
//  the pixels. This is deliberate: a Metal renderer's arithmetic is where the
//  silent bugs live (a wrong depth convention misplaces nothing on screen and
//  raises nothing; a wrong fit distance clips the object's edge), and none of
//  it needs a GPU. So the arithmetic lives here, in the target that `swift
//  test` compiles and runs on CI, and the app target keeps only the buffer
//  plumbing and the shaders — the two parts with no arithmetic to get wrong.
//
//  Nothing here invents data. The fit distance is derived from the loaded
//  asset's own bounds, and the default view direction is derived from which
//  axis the asset is actually longest along.
//

import Foundation

/// A 4x4 matrix in COLUMN-MAJOR order, matching `simd_float4x4` and Metal's
/// `float4x4` so it can be handed to a shader by copying 16 floats.
///
/// Defined here rather than imported from `simd` because the core is built for
/// Linux SwiftPM too (see `VectorMath.swift` for the same reason). Storage is a
/// flat array so the byte order handed to Metal is explicit rather than
/// something a later refactor can quietly change: element `m[col * 4 + row]`.
public struct Float4x4: Equatable, Sendable {
    public var m: [Float]

    public init(_ m: [Float]) {
        // A wrong-sized literal is a programmer error, but clamping is used
        // rather than trapping: this runs inside a render loop on a device,
        // where a crash is worse than an identity matrix.
        self.m = m.count == 16 ? m : Float4x4.identity.m
    }

    public subscript(row: Int, col: Int) -> Float {
        get { m[col * 4 + row] }
        set { m[col * 4 + row] = newValue }
    }

    public static let identity = Float4x4([
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ])

    /// `lhs * rhs` applies `rhs` first, then `lhs` — the usual convention, so a
    /// model-view-projection chain reads `projection * view * model`.
    public static func * (lhs: Float4x4, rhs: Float4x4) -> Float4x4 {
        var out = [Float](repeating: 0, count: 16)
        for col in 0..<4 {
            for row in 0..<4 {
                var sum: Float = 0
                for k in 0..<4 {
                    sum += lhs[row, k] * rhs[k, col]
                }
                out[col * 4 + row] = sum
            }
        }
        return Float4x4(out)
    }

    public func transform(_ v: SIMD4<Float>) -> SIMD4<Float> {
        let x = m[0] * v.x + m[4] * v.y + m[8] * v.z + m[12] * v.w
        let y = m[1] * v.x + m[5] * v.y + m[9] * v.z + m[13] * v.w
        let z = m[2] * v.x + m[6] * v.y + m[10] * v.z + m[14] * v.w
        let w = m[3] * v.x + m[7] * v.y + m[11] * v.z + m[15] * v.w
        return SIMD4(x, y, z, w)
    }

    public func transformPoint(_ p: SIMD3<Float>) -> SIMD3<Float> {
        let r = transform(SIMD4(p.x, p.y, p.z, 1))
        return SIMD3(r.x, r.y, r.z)
    }

    /// Bridge to the GPU's matrix type.
    ///
    /// This lives in the core, not in the renderer, for a specific reason:
    /// `Float4x4` is backed by a Swift `Array`, which is a reference to heap
    /// storage, so the struct is 8 bytes and cannot be handed to Metal however
    /// the uniform is declared. `simd_float4x4` is the 64-byte inline value
    /// type a shader expects. The bridge has to exist, so it belongs somewhere
    /// a test can reach it — a renderer bug is diagnosed as "the screen is
    /// wrong", not as a failing assertion.
    ///
    /// Both types are column-major, so columns map 1:1. This is a copy, NOT a
    /// transpose: getting that backwards renders a silently mirrored (and, for
    /// a perspective matrix, sheared) image. `RenderCameraTests` pins it by
    /// comparing the round trip against the core's own `transform(_:)`, which
    /// would disagree under a transpose.
    public var columnMajorColumns: [SIMD4<Float>] {
        (0..<4).map { c in
            SIMD4(m[c * 4], m[c * 4 + 1], m[c * 4 + 2], m[c * 4 + 3])
        }
    }

    /// Perspective divide, returning normalised device coordinates.
    ///
    /// Returns nil rather than dividing by ~0: a point on the eye plane has no
    /// finite projection, and a renderer that silently emits NaN there draws
    /// nothing and reports nothing, which is the failure this whole file is
    /// written to make visible.
    public func project(_ p: SIMD3<Float>) -> SIMD3<Float>? {
        let r = transform(SIMD4(p.x, p.y, p.z, 1))
        guard abs(r.w) > 1e-9 else { return nil }
        return SIMD3(r.x / r.w, r.y / r.w, r.z / r.w)
    }
}

/// Orbit camera over a connectome point cloud.
///
/// The state is deliberately (target, distance, yaw, pitch, up) rather than a
/// free eye position, because the only interaction this app has is dragging to
/// orbit — and an orbit parameterisation cannot lose the object off-screen or
/// roll the horizon, both of which a free camera does by accident.
public struct RenderCamera: Equatable, Sendable {
    /// What the camera looks at and orbits around.
    public var target: SIMD3<Float>
    /// Distance from `target` to the eye, in the asset's units (mm).
    public var distance: Float
    /// Azimuth around `up`, in radians.
    public var yaw: Float
    /// Elevation above the `up` equator, in radians, clamped away from the
    /// poles so the view direction never becomes parallel to `up` — at which
    /// point the screen-up vector is undefined and the image degenerates.
    public var pitch: Float
    /// Screen-up axis. Chosen from the asset's own extents, not assumed.
    public var up: SIMD3<Float>
    public var verticalFovRadians: Float
    public var nearPlane: Float
    public var farPlane: Float

    /// How far `pitch` is allowed to approach the pole: 89°.
    public static let maxPitch = Float.pi / 2 * 0.988

    public init(target: SIMD3<Float>, distance: Float, yaw: Float,
                pitch: Float, up: SIMD3<Float>,
                verticalFovRadians: Float = .pi / 4,
                nearPlane: Float, farPlane: Float) {
        self.target = target
        self.distance = distance
        self.yaw = yaw
        self.pitch = Swift.min(Swift.max(pitch, -RenderCamera.maxPitch),
                               RenderCamera.maxPitch)
        self.up = FlyMath.normalize(up)
        self.verticalFovRadians = verticalFovRadians
        self.nearPlane = nearPlane
        self.farPlane = farPlane
    }

    /// A deterministic screen-plane basis for a given `up`.
    ///
    /// The reference axis is whichever world axis is LEAST aligned with `up`.
    /// Picking it by alignment rather than by a fixed axis means the orbit
    /// parameterisation cannot hit the degenerate case where the chosen
    /// reference axis is parallel to `up`.
    public static func basis(forUp up: SIMD3<Float>)
        -> (east: SIMD3<Float>, north: SIMD3<Float>) {
        let u = FlyMath.normalize(up)
        let axes = [SIMD3<Float>(1, 0, 0), SIMD3<Float>(0, 1, 0),
                    SIMD3<Float>(0, 0, 1)]
        var best = axes[0]
        var bestDot = Float.greatestFiniteMagnitude
        for a in axes {
            let d = abs(FlyMath.dot(a, u))
            if d < bestDot {
                bestDot = d
                best = a
            }
        }
        let east = FlyMath.normalize(best - u * FlyMath.dot(best, u))
        let north = FlyMath.normalize(FlyMath.cross(u, east))
        return (east, north)
    }

    /// Unit vector from `target` towards the eye.
    public var viewDirection: SIMD3<Float> {
        let (east, north) = RenderCamera.basis(forUp: up)
        let cp = cosf(pitch)
        return FlyMath.normalize(cp * (cosf(yaw) * east + sinf(yaw) * north)
                                 + sinf(pitch) * up)
    }

    public var eye: SIMD3<Float> { target + viewDirection * distance }

    /// Right-handed look-at. Maps `eye` to the origin and `target` onto -z,
    /// which is what Metal's rasteriser expects.
    public func viewMatrix() -> Float4x4 {
        let f = FlyMath.normalize(target - eye)
        var u = up
        // The screen-up vector must be orthogonal to the view direction; if
        // the caller's `up` is degenerate against `f`, the basis helper's
        // least-aligned axis is used rather than producing a zero cross.
        if abs(FlyMath.dot(f, u)) > 0.9999 {
            u = RenderCamera.basis(forUp: f).north
        }
        let s = FlyMath.normalize(FlyMath.cross(f, u))
        let t = FlyMath.cross(s, f)
        let e = eye
        return Float4x4([
            s.x, t.x, -f.x, 0,
            s.y, t.y, -f.y, 0,
            s.z, t.z, -f.z, 0,
            -FlyMath.dot(s, e), -FlyMath.dot(t, e), FlyMath.dot(f, e), 1,
        ])
    }

    /// Right-handed perspective projection with the depth range [0, 1].
    ///
    /// Metal clips to z in [0, 1]; OpenGL uses [-1, 1]. Using the OpenGL form
    /// here is the classic silent port: every visible pixel still lands in the
    /// right place, so nothing looks wrong, but the near half of the depth
    /// buffer is wasted and depth testing degrades. `RenderCameraTests` pins
    /// the near plane at 0 and the far plane at 1 for exactly that reason.
    public func projectionMatrix(aspect: Float) -> Float4x4 {
        let a = aspect > 1e-6 ? aspect : 1e-6
        let f = 1 / tanf(verticalFovRadians / 2)
        let n = nearPlane
        let far = farPlane
        let d = n - far
        return Float4x4([
            f / a, 0, 0, 0,
            0, f, 0, 0,
            0, 0, far / d, -1,
            0, 0, far * n / d, 0,
        ])
    }

    public func viewProjection(aspect: Float) -> Float4x4 {
        projectionMatrix(aspect: aspect) * viewMatrix()
    }

    /// Orbit around the target by a drag, in radians per unit of movement.
    public mutating func orbit(deltaYaw: Float, deltaPitch: Float) {
        yaw += deltaYaw
        pitch = Swift.min(Swift.max(pitch + deltaPitch,
                                    -RenderCamera.maxPitch),
                          RenderCamera.maxPitch)
    }

    /// Radius of the bounding sphere that exactly fills the vertical field of
    /// view at `distance`. For a sphere of radius r centred on the target, the
    /// distance that makes it touch the frustum's top and bottom edges is
    /// r / sin(halfFov) — so this is the inverse of that, with `sin` rather
    /// than `tan`. Used by the tests to assert the fit is exactly tangent.
    public var visibleRadius: Float {
        distance * sinf(verticalFovRadians / 2)
    }

    /// Frame `bounds` entirely, on a view axis derived from the asset.
    ///
    /// Two decisions, both measured from the bounds rather than assumed:
    ///
    ///  * Screen-up is the LONGEST extent axis. On the shipped BANC CNS asset
    ///    that axis is z (measured span 12.02 against x 1.53 and y 4.04), so
    ///    the animal's long axis runs up the screen instead of being foreshortened
    ///    into a blob.
    ///  * The eye looks along the SHORTEST extent axis, because that is the
    ///    axis with the least depth to see through: it presents the broadest
    ///    face of the cloud. Nothing about this is anatomical — it is a
    ///    presentation default, and it is derived so that a differently shaped
    ///    asset is framed sensibly without editing this file.
    ///
    /// No claim is made here about which anatomical direction is which; that
    /// would need the atlas frame, which the asset does not carry.
    public static func fitted(to bounds: RenderBounds,
                              aspect: Float,
                              verticalFovRadians: Float = .pi / 4) -> RenderCamera {
        let extent = bounds.extents
        let r = Swift.max(bounds.radius, 1e-5)

        // Longest axis -> up; shortest axis -> view.
        let e = [extent.x, extent.y, extent.z]
        var longest = 0
        var shortest = 0
        for i in 0..<3 {
            if e[i] > e[longest] { longest = i }
            if e[i] < e[shortest] { shortest = i }
        }
        func unit(_ axis: Int) -> SIMD3<Float> {
            SIMD3(axis == 0 ? 1 : 0, axis == 1 ? 1 : 0, axis == 2 ? 1 : 0)
        }
        let upAxis = unit(longest)
        let lookAxis = unit(shortest)

        // Distance so the bounding SPHERE fits. `sin`, not `tan`: the object is
        // a sphere of radius r, not a flat plane at the target depth, so `tan`
        // would clip the silhouette's edge. Portrait phones make the horizontal
        // field the binding constraint (aspect < 1), which is why both are
        // computed and the smaller taken.
        let halfV = verticalFovRadians / 2
        let a = aspect > 1e-6 ? aspect : 1e-6
        let halfH = atanf(tanf(halfV) * a)
        let limiting = Swift.min(halfV, halfH)
        let distance = r / Swift.max(sinf(limiting), 1e-6)

        // Depth range from the fit, so the whole cloud is inside it at any
        // allowed yaw. A hardcoded near plane is what makes a 12 mm-long cloud
        // z-fight at one end and vanish at the other.
        let near = Swift.max(distance - r * 2, r * 0.001)
        let far = distance + r * 2

        // Express the look axis in the (east, north, up) basis for this up.
        let (east, north) = basis(forUp: upAxis)
        let pitch = asinf(Swift.min(Swift.max(FlyMath.dot(lookAxis, upAxis),
                                              -1), 1))
        let yaw = atan2f(FlyMath.dot(lookAxis, north),
                         FlyMath.dot(lookAxis, east))

        return RenderCamera(target: bounds.center, distance: distance,
                            yaw: yaw, pitch: pitch, up: upAxis,
                            verticalFovRadians: verticalFovRadians,
                            nearPlane: near, farPlane: far)
    }
}