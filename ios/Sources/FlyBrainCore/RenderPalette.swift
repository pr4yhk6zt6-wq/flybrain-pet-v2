//
//  RenderPalette.swift
//  FlyBrainCore
//
//  Region colour, as a pure function of the region — no Metal, no GPU, so the
//  macOS test runner can cover it.
//
//  This file exists in the core rather than next to the shaders for the same
//  reason as `ConnectomeRenderModel`: the arithmetic is trivial and the failure
//  mode is not. The table is INDEXED BY REGION, so a table with fewer entries
//  than `RegionID` has cases does not error — it wraps, and two anatomically
//  unrelated regions are drawn in one colour with nothing anywhere reporting a
//  problem. The tests assert the index domain instead.
//
//  Colour choice is presentation, but it is not arbitrary. The regions a user
//  most needs to tell apart are the anatomically ADJACENT ones (optic lobe
//  against antennal lobe, cervical connective against VNC), so the assignment
//  deliberately breaks hue continuity instead of running a rainbow through the
//  enum order. Measured minimum separation between ANY two of the 21 entries is
//  0.119 (SEZ / abdominal neuromere), and between ADJACENT enum entries 0.191
//  (retinaRight / lamina) — the first draft of this table measured 0.197 with
//  its closest pair being optic lobe / antennal lobe. A table must be checked
//  with a measurement, not an eye: the numbers above came from
//  `tools/verify_render_palette.py`, which reads this file.
//

import Foundation

public enum RenderPalette {
    /// One RGB triple per `RegionID`, in raw-value order.
    ///
    /// `RegionID.unknown` (raw 0) is a neutral grey rather than a hue: an
    /// unlabelled neuron must read as "no data", not as a region.
    public static let regionColors: [SIMD3<Float>] = [
        SIMD3(0.55, 0.55, 0.58),   //  0 unknown            — neutral, no data
        SIMD3(0.96, 0.45, 0.14),   //  1 retinaLeft         — deep orange
        SIMD3(0.99, 0.78, 0.20),   //  2 retinaRight        — amber
        SIMD3(0.88, 0.90, 0.30),   //  3 lamina             — yellow-green
        SIMD3(0.58, 0.92, 0.34),   //  4 medulla            — green
        SIMD3(0.30, 0.86, 0.50),   //  5 lobula             — spring green
        SIMD3(0.16, 0.80, 0.70),   //  6 lobulaPlate        — teal
        SIMD3(0.15, 0.62, 0.96),   //  7 opticLobe          — azure
        SIMD3(0.42, 0.40, 0.98),   //  8 antennalLobe       — indigo
        SIMD3(0.68, 0.36, 0.96),   //  9 mushroomBody       — violet
        SIMD3(0.93, 0.38, 0.78),   // 10 lateralHorn        — magenta
        SIMD3(0.94, 0.35, 0.40),   // 11 centralComplex     — red
        SIMD3(0.66, 0.20, 0.24),   // 12 superiorBrain      — dark red
        SIMD3(0.94, 0.60, 0.62),   // 13 subesophagealZone  — pink
        SIMD3(0.62, 0.44, 0.22),   // 14 cervicalConnective — brown
        SIMD3(0.86, 0.72, 0.44),   // 15 ventralNerveCord   — tan
        SIMD3(0.40, 0.62, 0.30),   // 16 legNeuromere       — olive
        SIMD3(0.20, 0.80, 0.86),   // 17 wingNeuropil       — cyan
        SIMD3(0.58, 0.30, 0.72),   // 18 haltereNeuropil    — purple
        SIMD3(0.98, 0.55, 0.72),   // 19 abdominalNeuromere — rose
        SIMD3(0.30, 0.34, 0.58),   // 20 endocrineVisceral  — slate blue
    ]

    /// Colour for a packed region byte.
    ///
    /// Out-of-range values fall back to the neutral instead of trapping: the
    /// region field is packed into 5 bits (capacity 32) while the enum has 21
    /// cases, so a future asset may legitimately carry a code this build does
    /// not know. Drawing it grey is honest; crashing a render loop is not.
    public static func color(region: UInt8) -> SIMD3<Float> {
        let i = Int(region)
        guard i >= 0 && i < regionColors.count else { return regionColors[0] }
        return regionColors[i]
    }

    /// Colour for a `RegionID`.
    public static func color(region: RegionID) -> SIMD3<Float> {
        color(region: UInt8(region.rawValue))
    }

    /// How far a fully-bright neuron's colour may move from its silent colour.
    ///
    /// Activity is drawn as an additive highlight rather than as a hue change,
    /// so the region stays readable while a cell fires.
    ///
    /// This value is set from a measurement, and the measurement says something
    /// the obvious claim does not: a brightness lift CANNOT be made collision
    /// free for this palette. Several regions are distinguished partly by
    /// lightness (centralComplex is a bright red, superiorBrain a dark one), so
    /// lifting a bright region walks it towards a lighter, different region.
    /// Swept over the real table, the worst "fully-lit colour vs some other
    /// region's silent colour" distance is 0.113 at 0.25, collapsing to 0.032
    /// at 0.35 and 0.070 at 0.30 — i.e. larger lifts are worse, non-monotonically.
    /// 0.25 is the largest lift that keeps that worst case above 0.11.
    ///
    /// So the honest statement is: intensity 0 returns the base colour exactly,
    /// the lift is monotone and bounded, and region identity is carried by the
    /// base colour. It is NOT claimed that an active neuron can never resemble
    /// another region's colour — the test pins the measured worst case instead,
    /// so it cannot silently get worse.
    public static let maxActivityLift: Float = 0.25

    /// Applies an activity intensity in 0...1 to a region colour.
    ///
    /// Intensity 0 returns the colour untouched — a silent neuron must look
    /// exactly like the region colour, with no floor. Above 0 the colour is
    /// pushed towards white by a bounded amount, and the push saturates rather
    /// than scaling the distance to white, so it stays inside
    /// `maxActivityLift` no matter how high the rate climbs.
    public static func color(region: UInt8, intensity: Float) -> SIMD3<Float> {
        let base = color(region: region)
        let t = Swift.min(Swift.max(intensity, 0), 1)
        guard t > 0 else { return base }
        return base + (SIMD3<Float>(1, 1, 1) - base) * (maxActivityLift * t)
    }
}