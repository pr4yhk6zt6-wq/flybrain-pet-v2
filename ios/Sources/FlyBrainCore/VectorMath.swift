//
//  VectorMath.swift
//  FlyBrainCore
//
//  Portable SIMD helpers. SIMD3<Float> lives in the Swift standard library
//  on every platform, but `simd_length` / `simd_normalize` are Apple SDK
//  functions — these wrappers keep the core buildable on macOS AND Linux
//  (SwiftPM), so `swift test` works in CI on any runner.
//

import Foundation

// On Apple platforms the `simd` module also provides simd_length / simd_normalize;
// we deliberately use our own FlyMath wrappers so `swift test` works the same
// on Linux CI runners. If compiling for Apple-only and you want the true
// Apple simd functions, this file can be swapped — but keep the core portable.
#if canImport(simd)
import simd
#endif

public enum FlyMath {
    @inlinable
    public static func length(_ v: SIMD3<Float>) -> Float {
        (v.x * v.x + v.y * v.y + v.z * v.z).squareRoot()
    }

    @inlinable
    public static func normalize(_ v: SIMD3<Float>) -> SIMD3<Float> {
        let l = length(v)
        return l > 0.000001 ? v / l : v
    }

    /// Cross product (portable — `cross` lives in Apple's simd module).
    @inlinable
    public static func cross(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> SIMD3<Float> {
        SIMD3(a.y * b.z - a.z * b.y,
              a.z * b.x - a.x * b.z,
              a.x * b.y - a.y * b.x)
    }
}