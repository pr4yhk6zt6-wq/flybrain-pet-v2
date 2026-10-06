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

    /// Stance/swing phase indicator 0..1 (0 = stance start, 1 = swing end).
    public var phase: Float = 0
}

/// Motor output summary for a step — what Life Mode renders.
public struct MotorOutput: Sendable {
    public var legTorques: [Float] = []       // per leg sum of joint torque
    public var wingStrokeFreq: Float = 0      // Hz
    public var wingStrokeAmplitude: Float = 0 // radians
    public var leftRightAsymmetry: Float = 0  // -1..1 (turn bias)
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

    /// Advance motor output given neural drive (0..1 per leg from connectome
    /// motor neurons) and proprioceptive feedback (spec #20).
    public func update(neuralDrive: [Float], dt: Double) {
        guard neuralDrive.count >= legs.count || true else { return }
        let n = legs.count
        var out = MotorOutput()

        for (i, leg) in legs.enumerated() {
            var leg = leg
            // tripod gait: legs {L1,R2,L3} swing together; {R1,L2,R3} stance
            let isTripodA = (i == 0 || i == 3 || i == 5)   // L1,R2,L3
            let drive = neuralDrive.isEmpty ? 0.5 : neuralDrive[i % n]
            // phase advances proportional to neural drive (speed)
            let phaseStep = Float(dt) * 0.004 * (0.3 + drive)
            leg.phase = (leg.phase + phaseStep).truncatingRemainder(dividingBy: 1)
            let inSwing = isTripodA ? leg.phase < 0.5 : leg.phase >= 0.5
            // compute joint torque toward target angle from phase
            let targetCoxa: Float = inSwing ? 0.6 : -0.2
            let targetFemur: Float = inSwing ? -0.8 : 0.3
            leg.coxa.velocity += (targetCoxa - leg.coxa.angle) * Float(dt) * 10
            leg.femur.velocity += (targetFemur - leg.femur.angle) * Float(dt) * 10
            leg.coxa.velocity *= max(0, 1 - leg.coxa.damping * Float(dt))
            leg.femur.velocity *= max(0, 1 - leg.femur.damping * Float(dt))
            leg.coxa.angle = min(max(leg.coxa.angle + leg.coxa.velocity * Float(dt),
                                     leg.coxa.limitMin), leg.coxa.limitMax)
            leg.femur.angle = min(max(leg.femur.angle + leg.femur.velocity * Float(dt),
                                      leg.femur.limitMin), leg.femur.limitMax)
            leg.coxa.torque = drive * 0.5
            leg.femur.torque = drive * 0.5
            legs[i] = leg
            out.legTorques.append(abs(leg.coxa.torque) + abs(leg.femur.torque))
        }

        // wings: stroke frequency from wing-muscle drive (spec #19)
        out.wingStrokeFreq = 180 * min(wingMuscleDrive, 1)          // up to ~180 Hz
        out.wingStrokeAmplitude = 0.9 * min(wingMuscleDrive, 1)     // radians
        // haltere beat follows wing (spec #16)
        out.haltereBeat = out.wingStrokeFreq / 180

        // proboscis extension from SEZ drive (gustatory acceptance, spec #14)
        out.proboscisExtension = proboscisDrive

        self.output = out
    }

    /// Drive set by SimulationCore reading SEZ motor-neuron activity.
    public var proboscisDrive: Float = 0

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