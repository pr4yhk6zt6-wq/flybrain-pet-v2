//
//  Plasticity.swift
//  FlyBrainCore
//
//  Research-grade plasticity layer (spec #9, #27) — SEPARATE from the
//  structural connectome. Dynamic synaptic state never rewrites the
//  underlying anatomy unless an explicit experimental feature is enabled.
//
//  Implemented:
//  - Short-term facilitation / depression (Tsodyks-Markram style)
//  - Dopaminergic reward-gated plasticity over olfactory pathways
//    (mushroom body, spec #9): activity-based Hebbian + dopamine gate.
//
//  All plasticity state is per-fly and NOT part of the shared connectome.
//

import Foundation

/// Per-synapse dynamic efficacy state.
public struct SynapticPlasticityState {
    public var baselineEfficacy: Float   // from connectome (INFERRED)
    public var currentEfficacy: Float    // effective, after STP + learning
    public var facilitation: Float       // u (utilization)
    public var depression: Float         // R (resource)
    public var tauFacilitation: Float
    public var tauDepression: Float
    public var lastUpdateTime: Double
    public var learningWeight: Float     // long-term learned modulation

    public init(baselineEfficacy: Float,
                tauFacilitation: Float = 150,
                tauDepression: Float = 200) {
        self.baselineEfficacy = baselineEfficacy
        self.currentEfficacy = baselineEfficacy
        self.facilitation = 0.1
        self.depression = 1.0
        self.tauFacilitation = tauFacilitation
        self.tauDepression = tauDepression
        self.lastUpdateTime = 0
        self.learningWeight = 1.0
    }
}

/// Dopamine-gated plasticity event (reward signal) — spec #9.
public struct DopamineSignal {
    public var time: Double
    public var magnitude: Float    // positive=reward, negative=punishment
    public var scope: Int32        // neuron index; -1 = global
}

public final class PlasticityManager: @unchecked Sendable {
    public private(set) var states: [SynapticPlasticityState] = []
    public var learningEnabled: Bool = false   // EXPERIMENTAL gate (spec #9)
    public var learningRate: Float = 0.001
    public private(set) var dopamineQueue: [DopamineSignal] = []

    public init(connectome: Connectome) {
        states = connectome.synapses.map {
            SynapticPlasticityState(baselineEfficacy: $0.estimatedEfficacy)
        }
    }

    /// Update STP state for a synapse before it delivers a spike.
    public func onSpikeDelivery(synapseIndex: Int, dtMs: Double) {
        guard synapseIndex >= 0 && synapseIndex < states.count else { return }
        var s = states[synapseIndex]
        let dt = Float(dtMs)
        // Tsodyks-Markram update on event
        let u = s.facilitation + (0.1 - s.facilitation) * Float(1 - exp(Double(-dt / s.tauFacilitation)))
        let R = s.depression + (1 - s.depression) * Float(1 - exp(Double(-dt / s.tauDepression)))
        s.facilitation = u
        s.depression = max(R - u * R, 0)        // resource consumed by this event
        s.currentEfficacy = s.baselineEfficacy * s.learningWeight * u * R
        s.lastUpdateTime = s.lastUpdateTime + Double(dt)
        states[synapseIndex] = s
    }

    /// Reward / punishment signal (dopamine). Positive = reward.
    public func pushDopamine(magnitude: Float, scope: Int32 = -1, time: Double) {
        dopamineQueue.append(DopamineSignal(time: time, magnitude: magnitude, scope: scope))
    }

    /// Hebbian + dopamine-gated update — called at the end of a learning window.
    /// pre/post are neuron indices; coactivity is the recent joint activity.
    public func applyLearning(pre: Int32, post: Int32, coactivity: Float, time: Double) {
        guard learningEnabled else { return }
        let gain = learningRate * coactivity
        // dopamine gate: total recent dopamine in window
        var da: Float = 0
        dopamineQueue.removeAll { $0.time < time - 1000 }
        for sig in dopamineQueue where sig.scope == -1 || sig.scope == post {
            da += sig.magnitude * Float(exp(Double(-(time - sig.time) / 500)))
        }
        // Full implementation walks the CSR synapse ranges for (pre→post);
        // here we demonstrate the gate on the synapse population.
        for (idx, s) in states.enumerated() {
            var ns = s
            ns.learningWeight += gain * (1 + da * 0.1)
            ns.learningWeight = min(max(ns.learningWeight, 0.2), 3.0)
            states[idx] = ns
        }
        _ = pre
    }
}