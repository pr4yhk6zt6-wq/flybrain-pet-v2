//
//  BodyModel.swift
//  FlyBrainCore
//
//  Articulated fly body (spec #18): head, thorax, abdomen, eyes, antennae,
//  proboscis, six legs, two wings, two halteres. Joints carry angle, velocity,
//  torque, limits, inertia, damping. The physics engine integrates these into
//  motion; animation only interpolates the physical result.
//
//  Phase 4/5 starter: full skeleton + DOFs + mass/inertia placeholders.
//

import Foundation

/// A rigid segment of the fly body.
public struct BodySegment: Sendable {
    public let name: String
    public var position: SIMD3<Float>   // relative to body origin
    public var mass: Float              // mg
    public var inertia: SIMD3<Float>    // diagonal inertia (mg·mm²)

    public init(name: String, position: SIMD3<Float>, mass: Float,
                inertia: SIMD3<Float>) {
        self.name = name
        self.position = position
        self.mass = mass
        self.inertia = inertia
    }
}

public struct Wing: Sendable {
    public var side: UInt8
    public var strokeAngle: JointState
    public var rotationAngle: JointState
    // Reduced-order aerodynamics (spec #19) live on
    // `PhysicsParameters.effectiveLiftCoefficient` / `effectiveDragCoefficient`,
    // which is what `BodyDynamics` actually multiplies into the wing force.
    //
    // `liftCoeff`/`thrustCoeff` used to be declared here as stored `Float = 1.0`
    // fields that nothing read. A knob that looks tunable and is not is worse
    // than no knob: it is how a reader concludes aerodynamic calibration is
    // wired to the wing when the real coefficient is elsewhere. Removed rather
    // than left as a duplicate — set the coefficient on the physics parameters.
}

public struct Haltere: Sendable {
    public var side: UInt8
    public var angle: JointState
    public var beatFrequency: Float = 0
}

public struct FlyBody {
    /// Length of the fully extended proboscis (mm). The labellum of
    /// *Drosophila* reaches roughly a head-length forward when fully
    /// extended; this is a skeleton approximation (spec #18), stated as such.
    public static let proboscisLengthMm: Float = 0.9
    /// Joint angle (rad) at which the proboscis is open far enough that the
    /// labellum could be touching a substrate. Below this the mouth is still
    /// folded and contact chemoreception is not possible, however close the
    /// fly is standing.
    public static let proboscisReachAngle: Float = 0.35
    /// Joint angle (rad) written when the motor drive is at maximum.
    ///
    /// This used to be a bare `0.8` multiplied into `body.proboscis.angle` at
    /// the articulation site. Naming it is not tidiness: the reach predicate
    /// `proboscisReaches` and the tip geometry both have to agree with it, and
    /// an unnamed literal in one file cannot be checked against a predicate in
    /// another. See `labellumGroundReachAngleMm` for the constraint it must
    /// satisfy.
    public static let proboscisMaxAngle: Float = 0.8

    /// Whether a proboscis at `angle` is open far enough for the labellum to
    /// touch a substrate. A pure predicate on the joint state, so the contact
    /// rule can be tested without a step of the simulation — the loop writes
    /// this joint every step, and a test that sets it by hand is testing a
    /// value the next step overwrites.
    public static func proboscisReaches(_ angle: Float) -> Bool {
        angle >= proboscisReachAngle
    }

    /// How far the proboscis is extended, 0 (folded) to 1 (full travel).
    ///
    /// The denominator is `proboscisMaxAngle`, NOT `proboscisReachAngle`. The
    /// earlier code divided by a hard-coded `1.4` at the tip-geometry site (and
    /// the tip's geometry was the only place extension was computed), so at the
    /// motor's full drive of 0.8 the proboscis reached only 0.8/1.4 = **57%** of
    /// its length. The reach predicate meanwhile called 0.35 rad "open", so the
    /// two disagreed about what extending meant, and the disagreement was
    /// invisible because each site was self-consistent.
    ///
    /// The consequence was that the labellum could not descend to the ground.
    /// The tip hangs off the head segment (offset y = 0) while the fly stands at
    /// `standHeightMm` = 0.8, and the proboscis only descends by sin(angle)
    /// times the fraction extended — so at 57% extension the tip sat **0.431 mm**
    /// above the ground against a `contactReach` of 0.25 mm. No joint angle
    /// could touch food, meaning labellar taste and ingestion were unreachable
    /// for every fly, and the gustatory pathway's tarsal bootstrap could open a
    /// mouth that had nowhere to go. Returning the true extension is what makes
    /// the tip reach 0.153 mm at full drive — inside contact reach.
    public static func proboscisExtensionFraction(angle: Float) -> Float {
        min(max(angle / proboscisMaxAngle, 0), 1)
    }

    /// Height of the labellum tip above the ground plane (mm) at a given joint
    /// angle, for a fly standing at `standHeightMm`.
    ///
    /// Returned rather than asserted so `tools/mirror_feeding_loop.py` can check
    /// the real number against the reach constants instead of trusting a
    /// comment — which is how the gap above went unnoticed in the first place.
    public static func labellumHeightAboveGroundMm(angle: Float,
                                                   standHeightMm: Float) -> Float {
        let frac = proboscisExtensionFraction(angle: angle)
        return standHeightMm - sin(angle) * proboscisLengthMm * frac
    }

    // Segments
    public var head: BodySegment
    public var thorax: BodySegment
    public var abdomen: BodySegment
    // Appendages
    public var leftEye: BodySegment
    public var rightEye: BodySegment
    public var leftAntenna: JointState
    public var rightAntenna: JointState
    public var proboscis: JointState
    public var legs: [Leg]
    public var wings: [Wing]
    public var halteres: [Haltere]

    public var neck: JointState

    // Body-level state
    public var velocity: SIMD3<Float> = SIMD3(0, 0, 0)
    public var angularVelocity: SIMD3<Float> = SIMD3(0, 0, 0)

    public init() {
        head = BodySegment(name: "head", position: SIMD3(0.6, 0, 0.15),
                           mass: 0.08, inertia: SIMD3(0.01, 0.01, 0.01))
        thorax = BodySegment(name: "thorax", position: SIMD3(0, 0, 0),
                             mass: 0.4, inertia: SIMD3(0.04, 0.04, 0.03))
        abdomen = BodySegment(name: "abdomen", position: SIMD3(-0.7, 0, 0),
                              mass: 0.3, inertia: SIMD3(0.03, 0.03, 0.04))
        leftEye = BodySegment(name: "eye-L", position: SIMD3(0.5, 0.18, 0.1),
                              mass: 0.02, inertia: SIMD3(0.002, 0.002, 0.002))
        rightEye = BodySegment(name: "eye-R", position: SIMD3(0.5, -0.18, 0.1),
                               mass: 0.02, inertia: SIMD3(0.002, 0.002, 0.002))
        leftAntenna = JointState(angle: 0.3, limitMin: -0.8, limitMax: 0.8)
        rightAntenna = JointState(angle: -0.3, limitMin: -0.8, limitMax: 0.8)
        proboscis = JointState(angle: 0, limitMin: 0, limitMax: 1.4)
        neck = JointState(angle: 0, limitMin: -0.6, limitMax: 0.6)
        legs = []
        for side in [UInt8(1), UInt8(2)] {
            for pos in [Leg.Position.front, .middle, .hind] {
                legs.append(Leg(side: side, position: pos))
            }
        }
        wings = [
            Wing(side: 1, strokeAngle: JointState(limitMin: -0.8, limitMax: 0.8),
                 rotationAngle: JointState(limitMin: -1.2, limitMax: 1.2)),
            Wing(side: 2, strokeAngle: JointState(limitMin: -0.8, limitMax: 0.8),
                 rotationAngle: JointState(limitMin: -1.2, limitMax: 1.2)),
        ]
        halteres = [
            Haltere(side: 1, angle: JointState(limitMin: -0.5, limitMax: 0.5)),
            Haltere(side: 2, angle: JointState(limitMin: -0.5, limitMax: 0.5)),
        ]
    }

    public var totalMass: Float {
        head.mass + thorax.mass + abdomen.mass + leftEye.mass + rightEye.mass
    }
}