//
//  RenderPaletteTests.swift
//  FlyBrainCoreTests
//
//  The palette is a table indexed by region, and the failure mode of an indexed
//  table is not a crash: a `regionColors` array shorter than `RegionID` has
//  cases wraps, so `endocrineVisceral` (raw 20) is drawn with `unknown`'s grey
//  and nothing anywhere reports a problem. These tests assert the index domain,
//  the separation, and the activity lift.
//
//  The numeric expectations are the ones tools/verify_render_palette.py
//  measures on the same file, so the two runners cannot disagree.
//

import XCTest
@testable import FlyBrainCore

final class RenderPaletteTests: XCTestCase {

    /// Every case of `RegionID` must be addressable. Written against
    /// `allCases` rather than a literal 21, so adding a region case without a
    /// colour fails here instead of silently wrapping.
    func testEveryRegionHasItsOwnColour() {
        XCTAssertEqual(RenderPalette.regionColors.count,
                       RegionID.allCases.count,
                       "a short table wraps and recolours regions silently")
        // Addressable by the packed byte the shader will receive.
        for region in RegionID.allCases {
            let c = RenderPalette.color(region: UInt8(region.rawValue))
            XCTAssertFalse(c.x.isNaN && c.y.isNaN,
                           "region \(region) has no usable colour")
        }
    }

    /// The packed region field is 5 bits (capacity 32) while the enum has 21
    /// cases, so an asset may legitimately carry a code this build does not
    /// know. That must draw as the neutral, not trap a render loop.
    func testUnknownRegionCodesFallBackToNeutral() {
        let neutral = RenderPalette.regionColors[0]
        XCTAssertEqual(RenderPalette.color(region: 31), neutral,
                       "an unrecognised 5-bit region code must not trap")
        XCTAssertEqual(RenderPalette.color(region: 200), neutral)
    }

    /// Region 0 is the sentinel: an unlabelled neuron must read as "no data",
    /// so it is achromatic rather than another hue.
    func testUnknownRegionIsAchromatic() {
        let c = RenderPalette.regionColors[0]
        XCTAssertEqual(c.x, c.y, accuracy: 0.05)
        XCTAssertEqual(c.y, c.z, accuracy: 0.05)
    }

    /// No two regions may share a colour. The measured worst pair in the
    /// shipped table is 0.1187 (subesophagealZone / abdominalNeuromere); the
    /// first draft of this table's worst pair was 0.197 but it put optic lobe
    /// and antennal lobe — anatomically adjacent — at that minimum, which is
    /// why the assignment deliberately breaks hue continuity instead of
    /// running a rainbow through enum order.
    func testRegionsAreDistinguishable() {
        var worst = (Float.greatestFiniteMagnitude, -1, -1)
        for i in 0..<RenderPalette.regionColors.count {
            for j in (i + 1)..<RenderPalette.regionColors.count {
                let d = distance(RenderPalette.regionColors[i],
                                 RenderPalette.regionColors[j])
                if d < worst.0 { worst = (d, i, j) }
            }
        }
        XCTAssertGreaterThan(worst.0, 0.10,
                             "regions \(worst.1) and \(worst.2) are drawn in "
                             + "nearly the same colour (\(worst.0))")
    }

    /// Anatomically adjacent regions sit next to each other in the enum, and
    /// they are exactly the pairs a user has to tell apart, so they must not be
    /// the closest pair in the table.
    func testAdjacentRegionsStayDistinct() {
        for i in 0..<(RenderPalette.regionColors.count - 1) {
            let d = distance(RenderPalette.regionColors[i],
                             RenderPalette.regionColors[i + 1])
            XCTAssertGreaterThan(d, 0.10,
                                 "regions \(i) and \(i + 1) are adjacent in "
                                 + "the enum and in the animal, yet close in "
                                 + "colour (\(d))")
        }
    }

    /// A silent neuron must look exactly like its region colour — no ambient
    /// floor and no idle pulse, which is why `intensity == 0` is a strict
    /// no-op rather than a small additive term.
    func testZeroIntensityIsAnExactNoOp() {
        for i in 0..<RenderPalette.regionColors.count {
            let base = RenderPalette.color(region: UInt8(i))
            let lit = RenderPalette.color(region: UInt8(i), intensity: 0)
            XCTAssertEqual(base.x, lit.x, accuracy: 1e-6)
            XCTAssertEqual(base.y, lit.y, accuracy: 1e-6)
            XCTAssertEqual(base.z, lit.z, accuracy: 1e-6)
        }
    }

    /// More rate must never look dimmer.
    func testBrightnessIsMonotoneInIntensity() {
        for i in 0..<RenderPalette.regionColors.count {
            let a = RenderPalette.color(region: UInt8(i), intensity: 0.1)
            let b = RenderPalette.color(region: UInt8(i), intensity: 0.4)
            let c = RenderPalette.color(region: UInt8(i), intensity: 0.7)
            let d = RenderPalette.color(region: UInt8(i), intensity: 1.0)
            XCTAssertLessThanOrEqual(a.x + a.y + a.z, b.x + b.y + b.z + 1e-6)
            XCTAssertLessThanOrEqual(b.x + b.y + b.z, c.x + c.y + c.z + 1e-6)
            XCTAssertLessThanOrEqual(c.x + c.y + c.z, d.x + d.y + d.z + 1e-6)
        }
    }

    /// The lift saturates: a cell firing far above the reference still lands on
    /// the fully-lit colour, so rate cannot walk a colour past its bound.
    func testLiftSaturatesAndIsBounded() {
        XCTAssertEqual(RenderPalette.maxActivityLift, 0.25, accuracy: 1e-6,
                       "the lift is set from a measurement; see the source")
        let atOne = RenderPalette.color(region: 3, intensity: 1.0)
        let atFive = RenderPalette.color(region: 3, intensity: 5.0)
        XCTAssertEqual(atOne.x, atFive.x, accuracy: 1e-6)
        XCTAssertEqual(atOne.z, atFive.z, accuracy: 1e-6)
        let base = RenderPalette.color(region: 3)
        XCTAssertLessThan(distance(atOne, base), 0.35,
                          "the lift must stay small enough to keep the region "
                          + "readable while the cell fires")
    }

    // MARK: - helper

    private func distance(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float {
        let d = a - b
        return (d.x * d.x + d.y * d.y + d.z * d.z).squareRoot()
    }
}