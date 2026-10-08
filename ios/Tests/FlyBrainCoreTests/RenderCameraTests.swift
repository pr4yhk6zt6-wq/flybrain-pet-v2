import XCTest
@testable import FlyBrainCore

/// The camera is the part of the renderer whose mistakes are invisible on
/// screen: a wrong depth range still puts every pixel in the right place, and
/// a fit that is slightly too close clips an edge nobody is looking at. None of
/// that needs a GPU to detect, so it is tested here rather than on a device.
///
/// Every number in this file is mirrored by tools/verify_render_camera.py, which
/// runs on the Python CI runner and computes the same values independently — so
/// a disagreement between the two is a real defect rather than a tolerance.
final class RenderCameraTests: XCTestCase {

    /// Measured, not assumed: the extents of the shipped BANC asset
    /// (data/generated/banc_cns.fbpack, 153,746 neurons, positions in mm):
    ///   x 0.0852 .. 1.6171   (span 1.532)
    ///   y 0.0406 .. 4.0781   (span 4.038)
    ///   z 0.0000 .. 12.0204  (span 12.020)
    /// Typical for a whole CNS: long and thin, which is exactly the shape that
    /// breaks a renderer that assumes the usual unit cube.
    static let bancBounds = RenderBounds(minX: 0.0852, minY: 0.0406, minZ: 0.0,
                                         maxX: 1.6171, maxY: 4.0781,
                                         maxZ: 12.0204)

    // MARK: - The fit comes from the asset

    /// Screen-up is the longest extent axis. Measured spans are z 12.02 against
    /// x 1.53 and y 4.04, so z is the one where the animal's long axis should
    /// run up the screen instead of being foreshortened into a blob.
    func testFitPutsTheLongestExtentOnTheScreenUpAxis() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        XCTAssertEqual(cam.up, SIMD3<Float>(0, 0, 1),
                       "the up axis must be the longest extent (z, span 12.02)")
        let bounds = Self.bancBounds
        XCTAssertGreaterThan(bounds.extents.z, bounds.extents.x)
        XCTAssertGreaterThan(bounds.extents.z, bounds.extents.y)
    }

    /// The eye looks along the SHORTEST extent axis: that is the axis with the
    /// least depth to see through, so it presents the broadest face of the
    /// cloud. Nothing anatomical is claimed here — the asset carries no atlas
    /// frame to make such a claim with.
    func testFitLooksAlongTheShortestExtentAxis() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let look = FlyMath.normalize(cam.target - cam.eye)
        XCTAssertEqual(abs(look.x), 1, accuracy: 1e-4,
                       "the view axis must be the shortest extent axis (x)")
        XCTAssertEqual(look.y, 0, accuracy: 1e-4)
        XCTAssertEqual(look.z, 0, accuracy: 1e-4)
    }

    /// Mirrors tools/verify_render_camera.py: for these bounds the bounding
    /// radius is 6.3863 mm, and at 0.46 aspect (portrait iPhone) the binding
    /// constraint is the HORIZONTAL field, giving a distance of 34.12 mm.
    /// A test that only asserted "distance > 0" would pass for a camera inside
    /// the cloud.
    func testFitDistanceMatchesTheIndependentlyComputedValue() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let r = Self.bancBounds.radius
        XCTAssertEqual(r, 6.3863, accuracy: 0.001, "bounding radius changed")
        XCTAssertEqual(cam.distance, 34.12, accuracy: 0.05,
                       "portrait fit distance disagrees with the Python mirror")
    }

    /// A wide aspect makes the vertical field binding, and the fit is then exactly
    /// r / sin(halfFov) against the INDEPENDENTLY known radius (6.3863 mm, the
    /// half-diagonal of the measured extents — not something read back off the
    /// camera). The first version of this test divided the camera's own stored
    /// radius by its own fit formula, which is true by construction and could
    /// never fail; asserting against the bounds radius is what makes it a test.
    func testWideAspectFitsCloserAndIsExactlyTangent() {
        let wide = RenderCamera.fitted(to: Self.bancBounds, aspect: 2.17)
        let halfV = wide.verticalFovRadians / 2
        let expected = Self.bancBounds.radius / sinf(halfV)
        XCTAssertEqual(wide.distance, expected, accuracy: 0.02,
                       "a wide aspect must be limited by the vertical field, "
                       + "at distance = r/sin(halfFov) from the measured radius")
        let portrait = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        XCTAssertGreaterThan(portrait.distance, wide.distance,
                             "the narrower aspect must back off further")
    }

    // MARK: - Nothing escapes the frustum, at any allowed orbit

    /// Sweeps yaw and pitch and requires every point of the bounding sphere —
    /// which contains every neuron by construction — to stay inside clip space,
    /// in front of the eye. This is the check that catches a near plane or fit
    /// distance that is wrong only at an angle, which is when the user sees it.
    func testWholeCloudStaysInsideClipSpaceAtEveryOrbitAngle() {
        var cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let r = Self.bancBounds.radius
        for yawDeg in stride(from: 0, to: 360, by: 15) {
            for pitchDeg in [-88, -45, 0, 45, 88] {
                cam.yaw = Float(yawDeg) * .pi / 180
                cam.pitch = Float(pitchDeg) * .pi / 180
                let vp = cam.viewProjection(aspect: 0.46)
                for i in 0..<48 {
                    let phi = 2 * Float.pi * Float(i) / 48
                    let theta = Float.pi * Float(i % 13) / 13
                    let p = cam.target + SIMD3(r * sinf(theta) * cosf(phi),
                                               r * sinf(theta) * sinf(phi),
                                               r * cosf(theta))
                    let clip = vp.transform(SIMD4(p.x, p.y, p.z, 1))
                    XCTAssertGreaterThan(clip.w, 1e-6,
                                         "point behind the eye at yaw \(yawDeg), pitch \(pitchDeg)")
                    guard let ndc = vp.project(p) else {
                        return XCTFail("projection returned nil inside the frustum")
                    }
                    XCTAssertLessThanOrEqual(abs(ndc.x), 1.0001,
                                             "escaped horizontally at yaw \(yawDeg), pitch \(pitchDeg)")
                    XCTAssertLessThanOrEqual(abs(ndc.y), 1.0001,
                                             "escaped vertically at yaw \(yawDeg), pitch \(pitchDeg)")
                    XCTAssertTrue(ndc.z >= -0.0001 && ndc.z <= 1.0001,
                                  "escaped in depth (\(ndc.z)) at yaw \(yawDeg), pitch \(pitchDeg)")
                }
            }
        }
    }

    // MARK: - Depth convention

    /// Metal clips depth to [0, 1]; OpenGL uses [-1, 1]. The two agree at the
    /// FAR plane and differ at the NEAR plane, so only the near plane can tell
    /// them apart — asserting the far plane would be a check that cannot fail.
    /// With the OpenGL form the near plane lands at -1 and the first half of the
    /// depth buffer is wasted with nothing visibly wrong in the image.
    func testDepthRangeIsMetalZeroToOne() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let vp = cam.viewProjection(aspect: 0.46)
        let forward = FlyMath.normalize(cam.target - cam.eye)
        let nearPt = cam.eye + forward * cam.nearPlane
        let farPt = cam.eye + forward * cam.farPlane
        let zn = try! XCTUnwrap(vp.project(nearPt))
        let zf = try! XCTUnwrap(vp.project(farPt))
        XCTAssertEqual(zn.z, 0, accuracy: 1e-4,
                       "near plane must map to z == 0 (Metal), not -1 (OpenGL)")
        XCTAssertEqual(zf.z, 1, accuracy: 1e-4, "far plane must map to z == 1")
    }

    /// A point on the eye plane has no finite projection. Returning NaN there
    /// would draw nothing and report nothing; `project` returns nil instead.
    func testProjectionRefusesPointsOnTheEyePlane() {
        let cam = RenderCamera(target: SIMD3(0, 0, 0), distance: 10, yaw: 0,
                               pitch: 0, up: SIMD3(0, 1, 0),
                               nearPlane: 0.1, farPlane: 100)
        XCTAssertNil(cam.viewProjection(aspect: 1).project(cam.eye),
                     "the eye itself must not produce a projection (w == 0)")
    }

    /// Which way the image faces. With `up` = +z and yaw = pitch = 0, the eye
    /// sits on +x looking back along -x, so screen-right is `north` and
    /// screen-up is `up`. Asserting the SIGNS of the projected axes is what
    /// pins the handedness: reverse it and the image is mirrored, which is a
    /// defect the symmetric orbit sweep above cannot detect because a mirrored
    /// frustum is still a valid frustum.
    func testScreenAxesPointTheExpectedWay() {
        let cam = RenderCamera(target: SIMD3(0, 0, 0), distance: 10, yaw: 0,
                               pitch: 0, up: SIMD3(0, 0, 1),
                               nearPlane: 0.1, farPlane: 100)
        let (east, north) = RenderCamera.basis(forUp: SIMD3(0, 0, 1))
        XCTAssertEqual(north, SIMD3<Float>(0, 1, 0), "basis changed")
        let vp = cam.viewProjection(aspect: 1)
        let towardsNorth = try! XCTUnwrap(vp.project(cam.target + north))
        XCTAssertEqual(towardsNorth.x, 1, accuracy: 1e-3,
                       "a point towards `north` must project to screen right")
        let towardsUp = try! XCTUnwrap(vp.project(cam.target + SIMD3<Float>(0, 0, 1)))
        XCTAssertGreaterThan(towardsUp.y, 0,
                             "a point towards `up` must project to screen up")

        // Note what is NOT asserted here: `east` at yaw 0 is exactly the view
        // axis, so it projects to x == 0 and cannot say anything about
        // handedness. Rotate a quarter turn instead — from +north the eye's
        // `east` lies on the far side and must fall to screen LEFT (measured
        // -1.0), which is the assertion that actually fails if the cross
        // products are reversed.
        var turned = cam
        turned.yaw = .pi / 2
        let vpT = turned.viewProjection(aspect: 1)
        let eastFromNorth = try! XCTUnwrap(vpT.project(turned.target + east))
        XCTAssertLessThan(eastFromNorth.x, 0,
                          "from +north, `east` must project to screen left")
    }

    // MARK: - Basis

    /// The screen basis must be orthonormal for any up axis, including a
    /// diagonal one, and the reference axis is chosen by least alignment with
    /// `up` so the degenerate cross product can never be reached.
    func testBasisIsOrthonormalForEveryUpAxis() {
        let ups = [SIMD3<Float>(0, 0, 1), SIMD3<Float>(0, 1, 0),
                   SIMD3<Float>(1, 0, 0), FlyMath.normalize(SIMD3(1, 1, 1))]
        for up in ups {
            let (e, n) = RenderCamera.basis(forUp: up)
            let u = FlyMath.normalize(up)
            XCTAssertEqual(FlyMath.dot(e, n), 0, accuracy: 1e-5)
            XCTAssertEqual(FlyMath.dot(e, u), 0, accuracy: 1e-5)
            XCTAssertEqual(FlyMath.dot(n, u), 0, accuracy: 1e-5)
            XCTAssertEqual(FlyMath.length(e), 1, accuracy: 1e-5)
            XCTAssertEqual(FlyMath.length(n), 1, accuracy: 1e-5)
        }
    }

    /// Pitch is clamped short of the poles. At exactly the pole the view
    /// direction is parallel to `up`, the screen-up vector is undefined, and the
    /// image degenerates — so an unbounded drag must not be able to reach it.
    func testPitchIsClampedAwayFromThePoles() {
        var cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        cam.orbit(deltaYaw: 0, deltaPitch: 99)
        XCTAssertLessThan(cam.pitch, .pi / 2,
                          "pitch reached the pole, where the basis degenerates")
        XCTAssertEqual(cam.pitch, RenderCamera.maxPitch, accuracy: 1e-6)
        cam.orbit(deltaYaw: 0, deltaPitch: -99)
        XCTAssertGreaterThan(cam.pitch, -.pi / 2)

        // ...and a full orbit still yields a usable basis at the clamp.
        let (e, n) = RenderCamera.basis(forUp: cam.up)
        XCTAssertEqual(FlyMath.dot(e, n), 0, accuracy: 1e-5)
        XCTAssertGreaterThan(FlyMath.length(cam.viewDirection), 0.9)
    }

    // MARK: - Matrix algebra

    func testIdentityIsNeutralOnBothSides() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let m = cam.viewProjection(aspect: 0.46)
        XCTAssertEqual((Float4x4.identity * m).m, m.m)
        XCTAssertEqual((m * Float4x4.identity).m, m.m)
    }

    /// `lhs * rhs` applies `rhs` first, so the chain reads as a product. Getting
    /// this backwards transposes the composite and puts the view *inside* the
    /// projection — which still renders something, just wrongly, so pin it.
    func testMultiplicationOrderAppliesTheRightOperandFirst() {
        var a = Float4x4.identity
        a[0, 3] = 5                      // translate +5 on x
        let s = Float4x4([2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1])
        // scale-then-translate: the translation is NOT scaled
        let st = a * s
        XCTAssertEqual(st.transformPoint(SIMD3(1, 0, 0)).x, 7, accuracy: 1e-6)
        // translate-then-scale: the translation IS scaled
        let ts = s * a
        XCTAssertEqual(ts.transformPoint(SIMD3(1, 0, 0)).x, 12, accuracy: 1e-6)
    }

    /// A malformed matrix literal must not trap inside a render loop: a crash
    /// on a device is worse than an identity matrix.
    func testWrongSizedMatrixLiteralFallsBackToIdentity() {
        XCTAssertEqual(Float4x4([1, 2, 3]).m, Float4x4.identity.m)
        XCTAssertEqual(Float4x4([]).m, Float4x4.identity.m)
    }

    /// The view matrix maps the eye to the origin and the target onto -z, which
    /// is what Metal's rasteriser expects. Checked by transformation rather than
    /// by reading the matrix back, so the assertion is independent of the layout.
    func testViewMatrixPlacesEyeAtOriginAndTargetOnNegativeZ() {
        let cam = RenderCamera.fitted(to: Self.bancBounds, aspect: 0.46)
        let v = cam.viewMatrix()
        let eyeInView = v.transformPoint(cam.eye)
        XCTAssertEqual(FlyMath.length(eyeInView), 0, accuracy: 1e-3,
                       "the eye must map to the origin")
        let targetInView = v.transformPoint(cam.target)
        XCTAssertEqual(targetInView.x, 0, accuracy: 1e-3)
        XCTAssertEqual(targetInView.y, 0, accuracy: 1e-3)
        XCTAssertEqual(targetInView.z, -cam.distance, accuracy: 1e-2,
                       "the target must sit on -z at the eye distance")
    }

    /// An empty model must not produce a NaN camera. The fit clamps its radius,
    /// and an all-zero bounds has no longest axis to pick — it still has to
    /// yield finite matrices rather than poison the vertex buffer.
    func testEmptyBoundsProduceAFiniteCamera() {
        let cam = RenderCamera.fitted(to: .empty, aspect: 0.46)
        XCTAssertTrue(cam.distance.isFinite)
        XCTAssertGreaterThan(cam.distance, 0)
        let vp = cam.viewProjection(aspect: 0.46)
        for value in vp.m {
            XCTAssertTrue(value.isFinite, "non-finite value in the view-projection")
        }
    }

    /// The bridge to the GPU's matrix type must be a copy, NOT a transpose.
    ///
    /// Both `Float4x4` (core) and `simd_float4x4` (Metal) are column-major, so
    /// the mapping is 1:1. A transposed bridge is not a compile error and not a
    /// crash — it renders a mirrored image, and with a perspective matrix a
    /// sheared one, which reads on screen as "the model looks a bit odd" rather
    /// than as a bug. This pins the mapping by feeding a point through both
    /// paths and requiring the same answer.
    func testColumnMajorColumnsAreNotTransposed() {
        let cam = RenderCamera(target: SIMD3(1, 2, 3), distance: 12, yaw: 0.7,
                               pitch: 0.2, up: SIMD3(0, 0, 1),
                               nearPlane: 0.1, farPlane: 100)
        let m = cam.viewProjection(aspect: 0.5)

        // What the GPU would do with the bridged columns.
        let cols = m.columnMajorColumns
        let v = SIMD4<Float>(4, -1, 2.5, 1)
        let gpu = SIMD4<Float>(
            cols[0].x * v.x + cols[1].x * v.y + cols[2].x * v.z + cols[3].x * v.w,
            cols[0].y * v.x + cols[1].y * v.y + cols[2].y * v.z + cols[3].y * v.w,
            cols[0].z * v.x + cols[1].z * v.y + cols[2].z * v.z + cols[3].z * v.w,
            cols[0].w * v.x + cols[1].w * v.y + cols[2].w * v.z + cols[3].w * v.w)

        // What the core's own multiply does.
        let cpu = m.transform(v)
        XCTAssertEqual(gpu.x, cpu.x, accuracy: 1e-5)
        XCTAssertEqual(gpu.y, cpu.y, accuracy: 1e-5)
        XCTAssertEqual(gpu.z, cpu.z, accuracy: 1e-5)
        XCTAssertEqual(gpu.w, cpu.w, accuracy: 1e-5)

        // And a transposed bridge must disagree, or the check above is vacuous:
        // if the two orders coincided for this matrix, it would prove nothing.
        let wrong = SIMD4<Float>(
            cols[0].x * v.x + cols[0].y * v.y + cols[0].z * v.z + cols[0].w * v.w,
            cols[1].x * v.x + cols[1].y * v.y + cols[1].z * v.z + cols[1].w * v.w,
            cols[2].x * v.x + cols[2].y * v.y + cols[2].z * v.z + cols[2].w * v.w,
            cols[3].x * v.x + cols[3].y * v.y + cols[3].z * v.z + cols[3].w * v.w)
        XCTAssertNotEqual(wrong.z, cpu.z, accuracy: 1e-3,
                          "a transposed bridge must produce a different result, "
                          + "or this test cannot detect one")
    }
}