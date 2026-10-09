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

    /// The metabolic clock, stated instead of hidden (spec #24).
    ///
    /// Physiology does NOT run at the rate of the neural clock, and pretending
    /// it does is what made this field decorative. In the animal the two time
    /// bases are ~seven orders of magnitude apart: a synaptic event is
    /// milliseconds while a meal's worth of reserves takes hours. A simulator
    /// that runs them on one clock has to pick, and picking neural time makes
    /// every metabolic variable invisible.
    ///
    /// It was invisible here in a second way too. The neural clock itself runs
    /// ~42x slower than wall time at the shipped 0.4 ms/frame
    /// (`tools/measure_time_scale.py`). With metabolism scaled to neural time, a
    /// fly went from fed to hungry in **40.6 wall-clock hours**
    /// (`tools/measure_metabolism_balance.py`) — so no session, and therefore no
    /// user of this app, had ever seen a hungry fly. `hungerDrive` was computed
    /// correctly, modulated nothing that mattered, and could not have, however
    /// well the loop downstream of it was wired.
    ///
    /// So this is a distinct time base with its ratio written down: metabolic
    /// hours per neural hour. 12,000 puts a full reserve at ~24 wall-clock
    /// minutes and fed-to-hungry at ~2 minutes, i.e. several feeding cycles per
    /// session. That is a **time-compressed depiction**, not a metabolic
    /// measurement, and it is the same kind of statement the project already
    /// makes about the 42x slow motion rather than a number dressed up as data.
    /// `tools/measure_metabolism_balance.py` reports the wall-clock consequence
    /// and fails if a session can no longer contain a hungry fly.
    public static let metabolicHoursPerNeuralHour: Float = 12_000

    /// Advance internal physiology by dt ms (spec #24). Metabolism, hydration
    /// loss, circadian clock (24 h), arousal decay/rise with activity.
    ///
    /// `dtMs` is NEURAL milliseconds; metabolism is advanced on the metabolic
    /// clock (`metabolicHoursPerNeuralHour`), which is why the two are not the
    /// same quantity.
    public mutating func advance(dtMs: Double, activityLevel: Float = 0) {
        let dtH = Float(dtMs) / 3_600_000.0        // neural hours
        let metabolicH = dtH * Self.metabolicHoursPerNeuralHour
        // Per metabolic hour. Metabolic cost tracks activity and temperature —
        // a resting fly burns ~30% of what an active one does.
        let metabolic = 0.02 * (0.3 + activityLevel * 0.7) * (1 + max(0, temperatureC - 25) * 0.01)
        energy = max(0, energy - metabolic * metabolicH)
        hydration = max(0, hydration - 0.005 * metabolicH)
        // circadian: 24h cycle
        circadianPhaseHours = (circadianPhaseHours + dtH * 24 / 24).truncatingRemainder(dividingBy: 24)
        // arousal relaxes toward baseline
        arousal += (0.4 - arousal) * Float(dtMs) / 5_000
        // feeding state transitions
        // Searching is the state a hungry fly is in; it is entered from a real
        // energy level, not from a script. `probing` is written by the loop when
        // the proboscis actually reaches the substrate (contact), so it
        // reflects the animal's motor output rather than a timer.
        if feedingState == .feeding && energy > 0.95 { feedingState = .satiated }
        if feedingState == .satiated && energy < 0.7 { feedingState = .searching }
        if energy < Self.hungerEnergyThreshold && feedingState == .satiated {
            feedingState = .searching
        }
        if stress > 0 { stress = max(0, stress - Float(dtMs) / 20_000) }
    }

    // MARK: - Modulatory signals

    /// Energy below which a fly is looking for food. `hungerDrive` is zero at
    /// `1 / hungerReferenceEnergy` = 0.714, so this is the same threshold
    /// expressed as an energy level instead of a drive — one definition, read
    /// from either end, rather than two numbers that can drift apart.
    public static let hungerEnergyThreshold: Float = 1 / 1.4

    /// Hunger peptide-ish modulation: 1 when hungry, 0 when fed (spec #24).
    public var hungerDrive: Float { max(0, 1 - energy * 1.4) }
    /// Thirst drive.
    public var thirstDrive: Float { max(0, 1 - hydration * 1.4) }
    /// Neuromodulatory gain on sensory inputs (octopamine-like arousal).
    public var sensoryGain: Float { 0.7 + arousal * 0.6 }

    /// Gustatory gain: how much a given taste acceptance drives the SEZ
    /// afferent, given how hungry the animal is.
    ///
    /// This is the link TASK-005 is about — `hungerDrive` had NO consumer. A
    /// hungry fly does not run a different behavior program; its taste system is
    /// more sensitive, so the SAME food produces a stronger signal and the
    /// existing feed-forward wiring does the rest.
    ///
    /// It is NOT one gain, and the first version of this code was wrong because
    /// it used one. Starvation does not scale taste up uniformly — sweet and
    /// bitter sensitivity are modulated **independently and reciprocally**
    /// (Inagaki, Panse & Anderson, *Neuron* 2014, PMID 25451195): starved flies
    /// show INCREASED sugar sensitivity and DECREASED bitter sensitivity. A
    /// single multiplier on a signed acceptance would do the exact opposite for
    /// the aversive half — it would make a starving fly *more* repelled by
    /// bitter food, which is backwards on the one nutrient state where it most
    /// needs calories.
    ///
    /// The same paper adds the ordering: these pathways are "recruited at
    /// increasing hunger levels, such that low-risk changes (higher sugar
    /// sensitivity) precede high-risk changes (lower sensitivity to potentially
    /// toxic resources)". So the appetitive gain moves from hunger 0, while the
    /// aversive gain only starts falling past `aversiveBluntingOnsetHunger` —
    /// losing your aversion to toxins is the riskier change and happens later.
    ///
    /// The curve shapes and the two gain spans are APPROXIMATED; the direction
    /// and the ordering are the measured claims. Numeric calibration is
    /// reported by `tools/mirror_feeding_loop.py`.
    public static let gustatoryGainFloor: Float = 1.0
    public static let hungerGustatoryGainSpan: Float = 2.0
    /// Fraction of aversive gain that full hunger can remove at most. Never 1.0:
    /// a starving fly is less deterred by bitter, not blind to it.
    public static let aversiveBluntingSpan: Float = 0.6
    /// Hunger at which bitter sensitivity starts to fall — after the sweet
    /// increase, per the ordering above.
    public static let aversiveBluntingOnsetHunger: Float = 0.4

    /// Gain applied to a PHAGOSTIMULANT (positive) taste sample.
    ///
    /// Never reaches zero: a fed fly can still taste sugar, it just responds
    /// less. A gain that could zero would make taste a hunger alarm rather than
    /// a sense.
    public var gustatoryAppetitiveGain: Float {
        Self.gustatoryGainFloor + hungerDrive * Self.hungerGustatoryGainSpan
    }

    /// Gain applied to an AVERSIVE (negative) taste sample. Falls with hunger,
    /// starting only once the animal is properly hungry.
    public var gustatoryAversiveGain: Float {
        let excess = max(0, hungerDrive - Self.aversiveBluntingOnsetHunger)
            / max(1 - Self.aversiveBluntingOnsetHunger, 0.0001)
        return Self.gustatoryGainFloor * (1 - excess * Self.aversiveBluntingSpan)
    }

    // MARK: - Physiology effects (call from body/physics)

    public mutating func consumeEnergy(_ delta: Float) { energy = max(0, energy - delta) }
    /// The mouth has reached a substrate. Written by the loop from the real
    /// proboscis joint angle, so `probing` is the motor state and not a timer.
    /// It must not overwrite a state that is further along the meal.
    public mutating func probe() {
        if feedingState == .searching { feedingState = .probing }
    }
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
    /// Set the energy reserve directly (test hook).
    ///
    /// `energy` is `private(set)` so the feeding loop's only production writer
    /// stays `advance()` (drains) and `feed()` (fills). Tests need to place the
    /// animal at a specific hunger level to check gain modulation, so this is
    /// the single sanctioned door — named `ForTesting` so it cannot be mistaken
    /// for part of the simulation.
    public mutating func setEnergyForTesting(_ value: Float) {
        energy = min(1, max(0, value))
    }
    public mutating func resetCircadian(hours: Float) { circadianPhaseHours = hours }
    public mutating func markReward(time: Double) {
        learningState.associativeMemories += 1
        learningState.lastRewardTime = time
    }
    public mutating func setReproductiveState(_ s: ReproductiveState) {
        reproductiveState = s
    }
}