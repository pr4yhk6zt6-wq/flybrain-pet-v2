//
//  InternalState.swift
//  FlyBrainCore
//
//  Dynamic internal state of the fly (spec #24, #25, #47, #104):
//  energy, hydration, temperature, circadian phase, arousal, satiety,
//  stress/injury, feeding state, reproductive state (female-consistent),
//  learning state. These MODULATE neural/neuromodulatory input — they never
//  select behavior by threshold rules (spec #43).
//

import Foundation

public struct InternalState: Sendable {
    // Energy / hydration / temperature / arousal / satiety / stress
    public private(set) var energy: Float        // 0..1
    public private(set) var hydration: Float      // 0..1
    public private(set) var temperatureC: Float   // body temp (°C)
    public private(set) var arousal: Float        // 0..1 (neuromodulatory tone)
    public private(set) var satiety: Float        // 0..1
    public private(set) var stress: Float         // 0..1 (e.g. injury)
    public private(set) var circadianPhaseHours: Float  // 0..24
    public private(set) var feedingState: FeedingState
    public private(set) var reproductiveState: ReproductiveState  // female-consistent
    public private(set) var learningState: LearningState

    public enum FeedingState: Int, Sendable {
        case searching = 0, probing = 1, feeding = 2, digesting = 3, satiated = 4
    }
    public enum ReproductiveState: Int, Sendable {
        case immature = 0, receptive = 1, mated = 2, postMating = 3
    }
    public struct LearningState: Sendable {
        public var associativeMemories: Int
        public var lastRewardTime: Double
        public init(associativeMemories: Int = 0, lastRewardTime: Double = -1e9) {
            self.associativeMemories = associativeMemories
            self.lastRewardTime = lastRewardTime
        }
    }

    public init(energy: Float = 0.8, hydration: Float = 0.8,
                temperatureC: Float = 25, arousal: Float = 0.4,
                satiety: Float = 0.4, stress: Float = 0,
                circadianPhaseHours: Float = 6) {
        self.energy = energy
        self.hydration = hydration
        self.temperatureC = temperatureC
        self.arousal = arousal
        self.satiety = satiety
        self.stress = stress
        self.circadianPhaseHours = circadianPhaseHours
        self.feedingState = .searching
        self.reproductiveState = .immature
        self.learningState = LearningState()
    }

    /// Advance internal physiology by dt ms (spec #24). Metabolism, hydration
    /// loss, circadian clock (24 h), arousal decay/rise with activity.
    public mutating func advance(dtMs: Double, activityLevel: Float = 0) {
        let dtH = Float(dtMs) / 3_600_000.0
        let metabolic = 0.02 * (0.3 + activityLevel * 0.7) * (1 + max(0, temperatureC - 25) * 0.01)
        energy = max(0, energy - metabolic * dtH * 10)
        hydration = max(0, hydration - 0.005 * dtH * 10)
        // circadian: 24h cycle
        circadianPhaseHours = (circadianPhaseHours + dtH * 24 / 24).truncatingRemainder(dividingBy: 24)
        // arousal relaxes toward baseline
        arousal += (0.4 - arousal) * Float(dtMs) / 5_000
        // feeding state transitions
        if feedingState == .feeding && energy > 0.95 { feedingState = .satiated }
        if feedingState == .satiated && energy < 0.7 { feedingState = .searching }
        if stress > 0 { stress = max(0, stress - Float(dtMs) / 20_000) }
    }

    // MARK: - Modulatory signals

    /// Hunger peptide-ish modulation: 1 when hungry, 0 when fed (spec #24).
    public var hungerDrive: Float { max(0, 1 - energy * 1.4) }
    /// Thirst drive.
    public var thirstDrive: Float { max(0, 1 - hydration * 1.4) }
    /// Neuromodulatory gain on sensory inputs (octopamine-like arousal).
    public var sensoryGain: Float { 0.7 + arousal * 0.6 }

    // MARK: - Physiology effects (call from body/physics)

    public mutating func consumeEnergy(_ delta: Float) { energy = max(0, energy - delta) }
    public mutating func feed(amount: Float) {
        energy = min(1, energy + amount)
        satiety = min(1, satiety + amount * 0.5)
        feedingState = amount > 0 ? .feeding : feedingState
    }
    public mutating func drink(amount: Float) {
        hydration = min(1, hydration + amount)
    }
    public mutating func applyStress(_ delta: Float) {
        stress = min(1, stress + delta)
        arousal = min(1, arousal + delta * 0.3)
    }
    public mutating func setTemperature(_ c: Float) { temperatureC = c }
    public mutating func resetCircadian(hours: Float) { circadianPhaseHours = hours }
    public mutating func markReward(time: Double) {
        learningState.associativeMemories += 1
        learningState.lastRewardTime = time
    }
    public mutating func setReproductiveState(_ s: ReproductiveState) {
        reproductiveState = s
    }
}