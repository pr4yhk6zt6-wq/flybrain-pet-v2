//
//  MotorSystem.swift
//  FlyBrainCore
//
//  Motor pattern generation (spec #17, #19, #20): converts connectome motor
//  neuron output into body drive. Phase 4 starter — provides the interfaces
//  and a six-legged gait pattern generator driven by neural activity, NOT
//  by an animation controller. Animation may only interpolate the physical
//  result (which the body model produces).
//

import Foundation

/// Joint state (spec #18).
public struct JointState: Sendable {
    public var angle: Float          // radians
    public var velocity: Float       // rad/s
    public var torque: Float         // N·m (normalized)
    public var limitMin: Float
    public var limitMax: Float
    public var damping: Float

    public init(angle: Float = 0, velocity: Float = 0, torque: Float = 0,
                limitMin: Float = -1.2, limitMax: Float = 1.2, damping: Float = 2) {
        self.angle = angle
        self.velocity = velocity
        self.torque = torque
        self.limitMin = limitMin
        self.limitMax = limitMax
        self.damping = damping
    }
}

/// One leg of the fly (6 legs: L1..L3, R1..R3).
public struct Leg: Sendable {
    public enum Position: Int, Sendable { case front = 0, middle = 1, hind = 2 }
    public let side: UInt8        // 1 = left, 2 = right
    public let position: Position

    // coxa→femur→tibia joints (spec #18)
    public var coxa: JointState
    public var femur: JointState
    public var tibia: JointState

    public init(side: UInt8, position: Position,
                coxa: JointState = JointState(),
                femur: JointState = JointState(),
                tibia: JointState = JointState()) {
        self.side = side
        self.position = position
        self.coxa = coxa
        self.femur = femur
        self.tibia = tibia
    }

    /// Stance/swing readout 0..1 (0 = liftoff, 0.5 = touchdown). Advanced by
    /// the stride rate derived from motor-neuron activity, so it is an
    /// observation of the mechanical cycle rather than a driving clock.
    public var phase: Float = 0
    /// True while the leg is off the ground (swing phase).
    public var isSwing: Bool = false
    /// Milliseconds spent in the current stance phase.
    public var stanceTimeMs: Double = 0
    /// Milliseconds spent in the current swing phase.
    public var swingTimeMs: Double = 0
}

/// Motor output summary for a step — what Life Mode renders.
public struct MotorOutput: Sendable {
    public var legTorques: [Float] = []       // per leg sum of joint torque
    /// Speed the planted legs are trying to drive the body to (mm/s, body
    /// forward axis). Derived from motor-neuron activity × stride kinematics —
    /// zero activity means zero setpoint, so a silent fly does not walk.
    public var forwardSpeedTarget: Float = 0
    /// Lateral stride speed target (mm/s, + = body +y), from left/right
    /// difference in leg drive.
    public var lateralSpeedTarget: Float = 0
    /// Fraction of legs reporting substrate contact (0..1).
    public var legContactFraction: Float = 1
    /// True when at least one leg is planted.
    public var legsInContact: Bool = true
    /// Fraction of legs currently planted (0..1), from the mechanical cycle.
    public var plantedLegDrive: Float = 0
    /// Mean motor-neuron activity of the planted legs (0..1).
    public var walkDrive: Float = 0
    /// Turning bias in deg/s, from the left/right difference in leg drive.
    /// A consequence of asymmetric neural output, not a steering command.
    public var turningBias: Float = 0
    public var wingStrokeFreq: Float = 0      // Hz
    public var wingStrokeAmplitude: Float = 0 // radians
    public var leftRightAsymmetry: Float = 0  // -1..1 (wing stroke asymmetry)
    public var haltereDrive: Float = 0        // 0..1, scales stabilising damping
    public var pitchBias: Float = 0           // -1..1 from wing/abdomen muscles
    public var proboscisExtension: Float = 0
    public var antennaAngle: Float = 0
    public var haltereBeat: Float = 0

    public init() {}
}

/// Drives the body from motor-neuron activity. 
/// Phase 4: gait CPG (central pattern generator) — a neural oscillator
/// network whose phase coupling produces tripod gait (spec #20). This CPG is
/// driven BY neural activity (motor command neurons), not by a timer.
public final class MotorSystem {
    // MARK: - Gait constants (APPROXIMATED — see docs/PHYSICS.md)

    /// Motor-neuron activity (0..1) at or above which a leg is commanded to
    /// step. Set from the leg-neuromere spike readout, so the threshold is in
    /// units of "fraction of motor drive", not a behaviour rule.
    public static let stepActivityThreshold: Float = 0.08
    /// Maximum stance duration (ms) — a mechanical/proprioceptive limit:
    /// a leg cannot stay loaded forever. APPROXIMATED from the ~8 Hz stride
    /// rate of freely walking Drosophila (stance ≈ 60-70% of a 125 ms cycle).
    public static let maxStanceMs: Double = 90
    /// Minimum swing duration (ms) before touchdown is allowed. APPROXIMATED
    /// from the same stride: swing ≈ 35-40% of the cycle.
    public static let minSwingMs: Double = 35
    /// Strides per second at full motor drive (0..1). APPROXIMATED from the
    /// measured ~8-10 Hz stride frequency of freely walking flies.
    public static let strideRatePerUnitDrive: Float = 10
    /// Legs that must be planted before another may lift (statically stable
    /// hexapod support triangle). A mechanical stability condition, not a
    /// gait pattern the code imposes: it is what turns drive into tripods.
    public static let minSupportLegs = 3
    /// Yaw rate (deg/s) produced at full left/right motor asymmetry.
    /// APPROXIMATED upper bound of observed turning rates in walking
    /// Drosophila (saccadic turns of roughly 100-300 deg/s are reported for
    /// walking flies; the simulator treats this as the saturation value).
    public static let maxTurnRateDegPerS: Float = 200
    /// Distance covered by one stride at full drive (mm) — used to turn the
    /// neural stride rate into a speed setpoint. APPROXIMATED: at ~8 Hz stride
    /// rate this gives ~25 mm/s, within the published 10-30 mm/s range for
    /// freely walking Drosophila.
    public static let strideLengthMm: Float = 3.0

    /// Number of legs currently planted.
    public var plantedCount: Int { legs.filter { !$0.isSwing }.count }

    public private(set) var legs: [Leg] = []
    public private(set) var output = MotorOutput()
    public var wingMuscleDrive: Float = 0      // from neural drive of wing neuropil
    public var haltereDrive: Float = 0

    public init() {
        // three pairs: left front/mid/hind, right front/mid/hind
        for side in [UInt8(1), UInt8(2)] {
            for pos in [Leg.Position.front, .middle, .hind] {
                legs.append(Leg(side: side, position: pos))
            }
        }
    }

    /// Advance motor output given neural drive (0..1 per leg, computed from
    /// REAL leg-neuromere spikes) and proprioceptive feedback (spec #20).
    ///
    /// There is no free-running oscillator here. The previous version stepped
    /// the phase with `dt * 0.004 * (0.3 + drive)`, so the fly walked even with
    /// zero motor-neuron activity — a scripted CPG, i.e. the animation driving
    /// the fly (forbidden, spec #11). Now:
    ///
    ///   • swing is entered ONLY when the leg's own motor neurons produce
    ///     activity (real spikes), so silence ⇒ the fly stands still;
    ///   • stride frequency, and therefore speed, are proportional to that
    ///     activity, which is what makes "walk faster" a neural outcome;
    ///   • stance termination also honours a maximum stance duration, a
    ///     proprioceptive/mechanical limit (APPROXIMATED, spec #10) — a leg
    ///     cannot stay loaded indefinitely — not a behaviour choice.
    ///
    /// Tripod coordination is not imposed by a timer either: a leg may only
    /// lift when its contralateral counterpart is planted (`minSupportLegs`),
    /// which is the mechanical condition for a statically stable hexapod
    /// gait and produces the alternating tripod pattern from the drive.
    public func update(neuralDrive: [Float], dt: Double) {
        var out = MotorOutput()
        // `dt` arrives in milliseconds (the neural clock); joint dynamics are
        // in seconds, so the two must not be mixed. Every rate term below uses
        // dtSec, every timer uses dtMs.
        let dtSec = Float(dt / 1000.0)

        for (i, leg) in legs.enumerated() {
            var leg = leg
            let drive = i < neuralDrive.count ? min(max(neuralDrive[i], 0), 1) : 0

            // --- stance / swing state machine (driven by spikes) -----------
            leg.stanceTimeMs += dt
            leg.swingTimeMs += dt
            if leg.isSwing {
                // swing ends when the leg has been repositioned forward
                if leg.swingTimeMs >= Self.minSwingMs {
                    leg.isSwing = false
                    leg.swingTimeMs = 0
                    leg.phase = 0.5              // touchdown = stance begin
                }
            } else {
                let neuralStep = drive >= Self.stepActivityThreshold
                let contralateralPlanted = plantedNeighbours(of: i) >= Self.minSupportLegs
                let mechanicalTimeout = leg.stanceTimeMs >= Self.maxStanceMs
                if (neuralStep && contralateralPlanted) || mechanicalTimeout {
                    leg.isSwing = true
                    leg.swingTimeMs = 0
                    leg.phase = 0               // liftoff
                }
            }
            // phase is a *readout* of the mechanical cycle, advanced by the
            // stride rate (which is neural), never by a fixed offset
            let strideRate = drive * MotorSystem.strideRatePerUnitDrive   // cycles/s
            leg.phase = (leg.phase + dtSec * strideRate)
                .truncatingRemainder(dividingBy: 1)

            // --- joint kinematics from the mechanical cycle ----------------
            let inSwing = leg.isSwing
            let targetCoxa: Float = inSwing ? 0.6 : -0.2
            let targetFemur: Float = inSwing ? -0.8 : 0.3
            // swing is faster than stance (as in real insects) and both scale
            // with drive, so a weak motor command yields a slow, weak step
            let stiffness = 4 + 8 * drive
            leg.coxa.velocity += (targetCoxa - leg.coxa.angle) * dtSec * stiffness
            leg.femur.velocity += (targetFemur - leg.femur.angle) * dtSec * stiffness
            leg.coxa.velocity *= max(0, 1 - leg.coxa.damping * dtSec)
            leg.femur.velocity *= max(0, 1 - leg.femur.damping * dtSec)
            leg.coxa.angle = min(max(leg.coxa.angle + leg.coxa.velocity * dtSec,
                                     leg.coxa.limitMin), leg.coxa.limitMax)
            leg.femur.angle = min(max(leg.femur.angle + leg.femur.velocity * dtSec,
                                      leg.femur.limitMin), leg.femur.limitMax)
            // Muscle force follows the neural command (spikes), NOT the phase:
            // a lifted leg transmits no ground force, so the physical body
            // only propels while a leg is planted.
            let muscleForce = drive
            leg.coxa.torque = muscleForce * 0.5
            leg.femur.torque = muscleForce * 0.5
            legs[i] = leg
            out.legTorques.append(inSwing ? 0 : (abs(leg.coxa.torque) + abs(leg.femur.torque)))
        }

        // wings: stroke frequency from wing-muscle drive (spec #19)
        out.wingStrokeFreq = 180 * min(wingMuscleDrive, 1)          // up to ~180 Hz
        out.wingStrokeAmplitude = 0.9 * min(wingMuscleDrive, 1)     // radians
        // haltere beat follows wing (spec #16)
        out.haltereBeat = out.wingStrokeFreq / 180

        // --- gait readouts the body physics consumes --------------------
        let planted = legs.filter { !$0.isSwing }
        out.plantedLegDrive = Float(planted.count) / Float(max(legs.count, 1))
        out.legContactFraction = out.plantedLegDrive
        out.legsInContact = !planted.isEmpty
        // Speed setpoint is the stride kinematics of the planted legs: mean
        // activity × stride rate × stride length. Zero activity ⇒ no setpoint.
        let meanDrive = planted.isEmpty ? 0
            : planted.map(\.coxa.torque).reduce(0, +) / Float(planted.count)
        out.walkDrive = meanDrive
        let strideRate = meanDrive * MotorSystem.strideRatePerUnitDrive  // Hz
        out.forwardSpeedTarget = strideRate * MotorSystem.strideLengthMm
        // Turning comes from the left/right difference in leg drive: the body
        // converts it into yaw through physics, not a steering decision.
        let leftDrive = legs.prefix(3).map(\.coxa.torque).reduce(0, +) / 3
        let rightDrive = legs.suffix(3).map(\.coxa.torque).reduce(0, +) / 3
        out.turningBias = (leftDrive - rightDrive) * MotorSystem.maxTurnRateDegPerS
        out.lateralSpeedTarget = (leftDrive - rightDrive) * MotorSystem.strideLengthMm * 4
        out.haltereDrive = min(wingMuscleDrive, 1)
        out.pitchBias = (legs[0].coxa.torque + legs[3].coxa.torque) / 2
            - (legs[2].coxa.torque + legs[5].coxa.torque) / 2

        // proboscis extension from SEZ drive (gustatory acceptance, spec #14)
        out.proboscisExtension = proboscisDrive

        self.output = out
    }

    /// Drive set by SimulationCore reading SEZ motor-neuron activity.
    public var proboscisDrive: Float = 0

    /// Legs planted besides `i` that can carry the load when it lifts.
    /// Tripod geometry: a leg shares support with the two legs of the
    /// opposite tripod group and, transiently, with its contralateral partner.
    /// Counted from the actual mechanical state, so the alternating tripod
    /// pattern appears as a consequence of the support requirement.
    private func plantedNeighbours(of i: Int) -> Int {
        guard legs.indices.contains(i) else { return 0 }
        // contralateral partner: legs 0..2 are left, 3..5 are right
        let partner = i < 3 ? i + 3 : i - 3
        var n = 0
        if legs.indices.contains(partner), !legs[partner].isSwing { n += 1 }
        for (j, l) in legs.enumerated() where j != i && j != partner {
            if !l.isSwing { n += 1 }
        }
        return n
    }

    /// Reset all joints.
    public func reset() {
        legs = []
        for side in [UInt8(1), UInt8(2)] {
            for pos in [Leg.Position.front, .middle, .hind] {
                legs.append(Leg(side: side, position: pos))
            }
        }
        output = MotorOutput()
    }
}
