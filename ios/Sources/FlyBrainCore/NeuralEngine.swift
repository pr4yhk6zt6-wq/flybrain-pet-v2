//
//  NeuralEngine.swift
//  FlyBrainCore
//
//  Sparse, event-driven spiking neural simulation (spec #6, #7, #28).
//
//  Design principles:
//  - STRUCTURAL connectome (Connectome, immutable, shared) is separated from
//    DYNAMIC neural state (per-fly, this engine) — spec #9, #117.
//  - Events only: quiescent neurons cost ~nothing; spikes are queued into a
//    deterministic min-heap ordered by (time, sequence) — reproducible replays.
//  - Neuron models: LIF (level 0) and AdEx-like adaptive LIF (level 1);
//    higher levels plug in via protocol conformance (spec #6).
//  - Every spike emitted is a real simulation event. No decorative activity.
//

import Foundation

// MARK: - Dynamic per-fly neural state

/// Per-neuron dynamic state packed for cache locality.
public struct NeuronDynamics {
    public var voltage: Float          // mV
    public var threshold: Float        // mV
    public var reset: Float            // mV
    public var resting: Float          // mV (E_rest)
    public var tauM: Float             // membrane time constant (ms)
    public var tauRefractory: Float    // ms
    public var adaptation: Float       // AdEx w (nA-ish, normalized)
    public var tauAdaptation: Float    // ms
    public var adaptationCoupling: Float // b
    public var spikeTriggeredAdaptation: Float // a
    public var firingRate: Float       // Hz, smoothed
    public var refractoryRemaining: Float
    public var lastSpikeTime: Double
    public var level: UInt8            // neuron model level 0..3

    public init(level: UInt8 = 1,
                resting: Float = -60,
                threshold: Float = -50,
                reset: Float = -65,
                tauM: Float = 10,
                tauRefractory: Float = 2,
                adaptationCoupling: Float = 0.0,
                spikeTriggeredAdaptation: Float = 0.0,
                tauAdaptation: Float = 100) {
        self.level = level
        self.resting = resting
        self.threshold = threshold
        self.reset = reset
        self.tauM = tauM
        self.tauRefractory = tauRefractory
        self.adaptationCoupling = adaptationCoupling
        self.spikeTriggeredAdaptation = spikeTriggeredAdaptation
        self.tauAdaptation = tauAdaptation
        self.voltage = resting
        self.adaptation = 0
        self.firingRate = 0
        self.refractoryRemaining = 0
        self.lastSpikeTime = -.infinity
    }
}

// MARK: - Synaptic events and deterministic event queue

/// A pending synaptic input (postsynaptic current). Sorted by (time, seq).
public struct SynapticEvent: Codable, Sendable {
    public var time: Double            // simulation ms at which event is delivered
    public var seq: UInt64             // insertion order — deterministic tie-break
    public var postNeuron: Int32
    public var current: Float          // nA (signed: excitatory/inhibitory)
    public var transmitter: UInt8

    public init(time: Double, seq: UInt64, postNeuron: Int32, current: Float, transmitter: UInt8) {
        self.time = time
        self.seq = seq
        self.postNeuron = postNeuron
        self.current = current
        self.transmitter = transmitter
    }
}

/// Deterministic binary min-heap keyed by (time, seq).
public struct EventHeap {
    private var items: [SynapticEvent] = []
    public var count: Int { items.count }

    public init() {}

    public mutating func push(_ e: SynapticEvent) {
        items.append(e)
        var i = items.count - 1
        while i > 0 {
            let parent = (i - 1) / 2
            if less(items[i], items[parent]) {
                items.swapAt(i, parent)
                i = parent
            } else { break }
        }
    }

    public mutating func pop() -> SynapticEvent? {
        guard !items.isEmpty else { return nil }
        if items.count == 1 { return items.removeLast() }
        let top = items[0]
        items[0] = items.removeLast()
        var i = 0
        let n = items.count
        while true {
            let l = 2 * i + 1, r = 2 * i + 2
            var smallest = i
            if l < n && less(items[l], items[smallest]) { smallest = l }
            if r < n && less(items[r], items[smallest]) { smallest = r }
            if smallest == i { break }
            items.swapAt(i, smallest)
            i = smallest
        }
        return top
    }

    public var top: SynapticEvent? { items.first }
    public var isEmpty: Bool { items.isEmpty }

    /// All items in heap order (for snapshot/serialization — deterministic
    /// because (time, seq) ordering fully determines the heap).
    public var allItems: [SynapticEvent] { items }

    /// Rebuild the heap from a previously snapshotted array.
    public mutating func rebuild(from items: [SynapticEvent]) {
        self.items = []
        for e in items { push(e) }
    }

    @inline(__always) private func less(_ a: SynapticEvent, _ b: SynapticEvent) -> Bool {
        a.time < b.time || (a.time == b.time && a.seq < b.seq)
    }
}

// MARK: - Simulation parameters

public struct SimulationParameters: Sendable {
    /// Global experimental efficacy multiplier (spec #39/#54). Labeled
    /// EXPERIMENTAL — defaults to 1.0 (biological default).
    public var synapticGain: Float = 1.0
    public var noiseScale: Float = 0.005
    public var dt: Double = 0.1        // ms per neural step
    public var maxEventsPerStep: Int = 1_000_000
    public var seed: UInt64 = 0x5EED

    public init() {}
}

/// LOD of the neural simulation (spec #23, #48).
public enum SimulationLOD: Int, Sendable {
    case background = 0    // reduced population rate
    case wholeNetwork = 1  // full population, LIF/AdEx
    case selectedCircuit = 2
    case singleNeuron = 3
}

// MARK: - Neuron model protocol (levels 0..3)

public protocol NeuronModel {
    /// Advance one step and return true if the neuron fires now.
    /// `incomingCurrent` is the summed postsynaptic current this step.
    mutating func step(dynamics: inout NeuronDynamics, incomingCurrent: Float,
                       dt: Double, spikeNow: Bool) -> Bool
}

/// LEVEL 0 — simple LIF (very low power background).
public struct LIFModel: NeuronModel {
    public init() {}
    public mutating func step(dynamics: inout NeuronDynamics, incomingCurrent: Float,
                              dt: Double, spikeNow: Bool) -> Bool {
        // refractory window is advanced by the engine (single clock owner) so a
        // sparse step can never stall the counter
        if dynamics.refractoryRemaining > 0 { return false }
        let dv = ((dynamics.resting - dynamics.voltage) + incomingCurrent * 10) / dynamics.tauM * Float(dt)
        dynamics.voltage += dv
        if dynamics.voltage >= dynamics.threshold {
            dynamics.voltage = dynamics.reset
            dynamics.refractoryRemaining = dynamics.tauRefractory
            dynamics.lastSpikeTime = 0 // set by engine
            return true
        }
        return false
    }
}

/// LEVEL 1 — adaptive LIF / AdEx-like (default whole-network model).
public struct AdExModel: NeuronModel {
    public init() {}
    public mutating func step(dynamics: inout NeuronDynamics, incomingCurrent: Float,
                              dt: Double, spikeNow: Bool) -> Bool {
        // refractory window is advanced by the engine (single clock owner) so a
        // sparse step can never stall the counter
        if dynamics.refractoryRemaining > 0 { return false }
        // Membrane
        let dv = ((dynamics.resting - dynamics.voltage) + incomingCurrent * 10
                  - dynamics.adaptation) / dynamics.tauM * Float(dt)
        dynamics.voltage += dv
        // Adaptation variable (AdEx w)
        let dw = (dynamics.adaptationCoupling * (dynamics.voltage - dynamics.resting)
                  - dynamics.adaptation) / dynamics.tauAdaptation * Float(dt)
        dynamics.adaptation += dw

        if dynamics.voltage >= dynamics.threshold {
            dynamics.voltage = dynamics.reset
            dynamics.refractoryRemaining = dynamics.tauRefractory
            dynamics.adaptation += dynamics.spikeTriggeredAdaptation
            return true
        }
        return false
    }
}

// MARK: - Spiking event emission

/// A spike emitted by a neuron (real event — for telemetry, raster, rendering).
public struct SpikeEvent: Sendable {
    public let neuron: Int32
    public let time: Double          // ms
    public let sourceTransmitter: UInt8
}

// MARK: - The engine

public final class NeuralEngine: @unchecked Sendable {
    public let connectome: Connectome
    public private(set) var dynamics: [NeuronDynamics]
    public private(set) var modelLevels: [UInt8]
    public var parameters: SimulationParameters

    // Deterministic RNG (xorshift64*), seeded — reproducible (spec #63)
    private var rngState: UInt64

    // Event machinery
    public private(set) var eventHeap = EventHeap()
    private var eventSeq: UInt64 = 0
    private var spikeAccumulator: [Float]   // incoming current per neuron (this event batch)

    // Telemetry (all real)
    public private(set) var spikeCount: UInt64 = 0
    public private(set) var spikeEventsThisWindow: UInt64 = 0
    public private(set) var windowStartTime: Double = 0
    public private(set) var lastWindowSpikesPerSecond: Double = 0
    public private(set) var currentTimeMs: Double = 0
    public private(set) var simulationStep: UInt64 = 0

    // Recent spike ring buffer for raster/inspector (unaligned, cheap)
    public private(set) var recentSpikesPerNeuron: [UInt16]
    private var recentWindowSeconds: Double = 1.0
    private var recentWindowStartTime: Double = 0

    /// Cumulative spikes per neuron (never decayed) — for tests/inspector.
    public private(set) var cumulativeSpikes: [UInt32]

    public private(set) var activeNeuronsThisWindow: Int32 = 0
    private var activeWindowMark: [UInt32]

    /// Indices of neurons whose refractory window is still open. Maintained
    /// incrementally — a neuron is added when it spikes and dropped when its
    /// window closes — so the per-step cost is O(active), not O(neurons). On
    /// whole-BANC (153,746 neurons) the previous unconditional sweep over
    /// every neuron ran once per 0.1 ms step regardless of activity, which
    /// contradicts the event-driven design (spec #46) and is the single
    /// largest per-step cost on a real connectome.
    private var refractoryRing: [Int32] = []

    /// Index of every neuron whose recent-spike counter is non-zero, with the
    /// same incremental discipline. Only these can decay; a full sweep also
    /// conflated two clocks (the counter was scaled to a 1 s window but
    /// decremented as if the window were one step, so any count below 2500
    /// was wiped to zero every 10 ms — see the decay code in `step()`).
    private var recentRing: [Int32] = []

    /// Leaky firing-rate estimate per neuron, in Hz, updated incrementally.
    ///
    /// `recentSpikesPerNeuron` is an INSPECTION window: it is a tumbling
    /// counter, cleared wholesale when the 1 s window rolls, so using it as a
    /// drive signal reports zero for most of every window and then a lump.
    /// That is wrong for the motor system in two ways: the value depends on
    /// WHEN it is sampled (so it is not a rate at all), and with the neural
    /// clock running ~42x slower than real time a 1 s neural window is ~42 s
    /// of wall time, so the animal could not change its drive quickly. The
    /// motor readout therefore uses a properly leaky rate instead: it responds
    /// on a sensorimotor timescale and reads correctly at any instant.
    public private(set) var leakyRatePerNeuron: [Float]
    private var leakyRateRing: [Int32] = []

    /// Sensorimotor rate time constant, in ms of NEURAL time.
    /// INFERRED: insect leg motor neurons follow descending drive on a
    /// ~10-50 ms timescale (SCIENCE_SOURCES.md); this is deliberately far
    /// shorter than the 1 s inspection window, which is not a motor timescale.
    public var motorRateTauMs: Float = 20

    /// Scratch buffers reused across steps. Allocating a fresh Set and Array
    /// per step was the other hot-loop heap cost; `step()` has a single owner
    /// and is not reentrant, so reuse is safe.
    private var stepTouched: [Int32] = []
    private var stepTouchedSet: Set<Int32> = []

    /// AdEx model shared for whole-network LOD1.
    private var adex = AdExModel()
    private var lif = LIFModel()

    public init(connectome: Connectome,
                parameters: SimulationParameters = SimulationParameters(),
                restingPotential: Float = -60,
                threshold: Float = -50) {
        self.connectome = connectome
        self.parameters = parameters
        self.rngState = parameters.seed &* 0x9E3779B97F4A7C15 &+ 1
        let n = connectome.neuronCount
        self.dynamics = [NeuronDynamics](repeating: NeuronDynamics(), count: n)
        self.modelLevels = [UInt8](repeating: 1, count: n)
        self.spikeAccumulator = [Float](repeating: 0, count: n)
        self.recentSpikesPerNeuron = [UInt16](repeating: 0, count: n)
        self.cumulativeSpikes = [UInt32](repeating: 0, count: n)
        self.leakyRatePerNeuron = [Float](repeating: 0, count: n)
        // Sentinel so the first spike of second-bucket 0 is counted.
        self.activeWindowMark = [UInt32](repeating: UInt32.max, count: n)

        // Region-based default model assignment (spec #23)
        for i in 0..<n {
            let region = RegionID(rawValue: Int(connectome.neurons[i].region)) ?? .unknown
            let d = NeuronDynamics(level: 1, resting: restingPotential, threshold: threshold)
            switch region {
            case .ventralNerveCord, .legNeuromere, .wingNeuropil, .haltereNeuropil, .abdominalNeuromere:
                // motor/sensory periphery: fast, little adaptation
                var dd = d
                dd.tauM = 5
                dd.tauRefractory = 1
                dd.adaptationCoupling = 0.2
                dd.spikeTriggeredAdaptation = 0.5
                dynamics[i] = dd
            case .mushroomBody, .centralComplex:
                // learning/integrative: stronger adaptation
                var dd = d
                dd.tauM = 15
                dd.tauAdaptation = 150
                dd.adaptationCoupling = 0.5
                dd.spikeTriggeredAdaptation = 2.0
                dynamics[i] = dd
            default:
                dynamics[i] = d
            }
        }
    }

    // MARK: - Simulation loop (fixed-step event-driven, deterministic)

    /// Run `steps` simulation steps of `dt` ms each. Pure function of state —
    /// deterministic given seed + connectome + inputs (spec #63).
    public func run(steps: Int) {
        for _ in 0..<steps {
            step()
        }
    }

    /// One simulation step (dt ms). Sorted event population first, then
    /// integrate all neurons that received input this step (sparse).
    public func step() {
        let dt = parameters.dt
        let stepTime = currentTimeMs + dt
        simulationStep += 1

        // 0) Advance refractory windows on the real clock. Sparse integration
        // only *integrates* neurons that received input, but a refractory
        // window is a property of time, not of input — decaying it inside the
        // model (touched neurons only) would leave it stalled whenever a
        // neuron was silent, silently discarding later input. Engine owns the
        // clock; models only test the flag (spec #63 determinism preserved).
        //
        // Done over the active set, not over every neuron: nothing else can
        // have a non-zero window, so this is identical in result and O(active)
        // instead of O(neurons) per step.
        if !refractoryRing.isEmpty {
            let decay = Float(dt)
            var w = 0
            for k in 0..<refractoryRing.count {
                let i = Int(refractoryRing[k])
                let r = dynamics[i].refractoryRemaining
                // Keep the invariant that the ring holds EXACTLY the neurons
                // with an open window: anything that has reached 0 (or was
                // never refractory) is dropped, so a zero-length refractory
                // period cannot leak entries forever.
                if r > 0 {
                    let next = max(0, r - decay)
                    dynamics[i].refractoryRemaining = next
                    if next > 0 { refractoryRing[w] = refractoryRing[k]; w += 1 }
                }
            }
            refractoryRing.removeLast(refractoryRing.count - w)
        }

        // 0b) Decay the leaky firing-rate estimate. Same incremental discipline:
        // only neurons with a non-zero estimate can decay, so this is O(active).
        // A neuron is dropped from the set once its estimate reaches zero.
        if !leakyRateRing.isEmpty {
            // Per-step retention for an exponential decay with time constant
            // tau: rate <- rate * exp(-dt/tau); a spike adds 1000/tau Hz (so a
            // neuron firing at rate R settles at R).
            let stepFactor = Float(exp(-Double(dt) / Double(max(motorRateTauMs, 0.001))))
            let spikeAdd = 1000.0 / max(motorRateTauMs, 0.001)
            var w = 0
            for k in 0..<leakyRateRing.count {
                let i = Int(leakyRateRing[k])
                let next = leakyRatePerNeuron[i] * stepFactor
                leakyRatePerNeuron[i] = next
                // Keep the invariant that the ring holds EXACTLY the non-zero
                // estimates: entries that have decayed below the smallest
                // representable increment are clamped out, so the set cannot
                // grow without bound over a long run.
                if next > spikeAdd * 0.001 {
                    leakyRateRing[w] = leakyRateRing[k]; w += 1
                } else {
                    leakyRatePerNeuron[i] = 0
                }
            }
            leakyRateRing.removeLast(leakyRateRing.count - w)
        }

        // 1) Deliver all events at or before stepTime
        stepTouched.removeAll(keepingCapacity: true)
        stepTouchedSet.removeAll(keepingCapacity: true)
        var delivered = 0
        while let ev = eventHeap.top, ev.time <= stepTime, delivered < parameters.maxEventsPerStep {
            _ = eventHeap.pop()
            spikeAccumulator[Int(ev.postNeuron)] += ev.current
            if stepTouchedSet.insert(ev.postNeuron).inserted { stepTouched.append(ev.postNeuron) }
            delivered += 1
        }

        // 2) Integrate touched neurons (sparse — untrouched stay at rest)
        for idx in stepTouched {
            let i = Int(idx)
            let current = spikeAccumulator[i]
            spikeAccumulator[i] = 0

            // intrinsic noise (deterministic, seeded)
            let noise = (Float(randomDouble()) * 2 - 1) * parameters.noiseScale * dynamics[i].tauM

            var fired = false
            switch modelLevels[i] {
            case 0:
                fired = lif.step(dynamics: &dynamics[i], incomingCurrent: current + noise,
                                 dt: dt, spikeNow: false)
            default:
                fired = adex.step(dynamics: &dynamics[i], incomingCurrent: current + noise,
                                  dt: dt, spikeNow: false)
            }

            if fired {
                emitSpike(from: Int32(i), at: stepTime)
            }
        }

        // 3) Advance global clock; recompute telemetry windows
        currentTimeMs = stepTime
        if currentTimeMs - windowStartTime >= 1000 {
            lastWindowSpikesPerSecond = Double(spikeEventsThisWindow) * 1000.0 / (currentTimeMs - windowStartTime)
            spikeEventsThisWindow = 0
            windowStartTime = currentTimeMs
            activeNeuronsThisWindow = 0
            // sentinel reset — all marks now stale
            activeWindowMark.withUnsafeMutableBufferPointer { buf in
                buf.update(repeating: UInt32.max)
            }
        }

        // decay recent-spike ring. The field means "spikes in a 1 s window"
        // (recentWindowSeconds), so it is a tumbling window exactly like
        // spikesPerSecond below: entries accumulate and are cleared when the
        // window rolls. The previous code instead decremented by a quarter of
        // the window *expressed in steps* every 100 steps — with the defaults
        // that is a decay of 2500 on a counter holding single-digit counts, so
        // recentSpikes() (the motor drive's only real input, spec #20) read 0
        // for every neuron on every frame, and the doc's "1 s" window was
        // physically 10 ms. Clearing only the non-zero entries keeps this
        // O(active) on a whole-BANC connectome.
        if currentTimeMs - recentWindowStartTime >= recentWindowSeconds * 1000.0 {
            for k in 0..<recentRing.count {
                recentSpikesPerNeuron[Int(recentRing[k])] = 0
            }
            recentRing.removeAll(keepingCapacity: true)
            recentWindowStartTime = currentTimeMs
        }
    }

    @inline(__always) private func emitSpike(from neuron: Int32, at time: Double) {
        spikeCount += 1
        spikeEventsThisWindow += 1
        let ri = Int(neuron)
        // leaky firing-rate estimate (Hz) — the drive signal the motor system
        // reads. Incremental: an existing entry is decayed by the same factor
        // used in the decay pass, a new one is seeded at zero first so the two
        // paths agree exactly.
        if leakyRatePerNeuron[ri] == 0 { leakyRateRing.append(neuron) }
        leakyRatePerNeuron[ri] += 1000.0 / max(motorRateTauMs, 0.001)
        if recentSpikesPerNeuron[ri] == 0 { recentRing.append(neuron) }
        if recentSpikesPerNeuron[ri] < .max { recentSpikesPerNeuron[ri] &+= 1 }
        if cumulativeSpikes[ri] < .max { cumulativeSpikes[ri] &+= 1 }
        // active-neuron counter is reset when the telemetry window rolls
        let bucket = UInt32(floor((time - windowStartTime) / 1000.0))
        if activeWindowMark[ri] != bucket {
            activeWindowMark[ri] = bucket
            activeNeuronsThisWindow += 1
        }
        // register the refractory window in the active set. No "is it already
        // tracked?" test is needed: a neuron only reaches a spike while its
        // window is closed (the models return false while refractory > 0) and
        // the ring holds exactly the open windows, so the spiking neuron is
        // guaranteed absent. The models have already set refractoryRemaining by
        // the time this runs, so testing it here would always be false.
        if dynamics[ri].tauRefractory > 0 {
            refractoryRing.append(neuron)
        }
        // smooth firing rate (ms window decay)
        let rate = dynamics[ri].firingRate
        dynamics[ri].firingRate = rate * 0.99 + (1000.0 / Float(max(time - dynamics[ri].lastSpikeTime, 1))) * 0.01
        dynamics[ri].lastSpikeTime = time

        // dispatch to postsynaptic partners (topology from real connectome)
        let outRange = connectome.outgoingRange(of: Int(neuron))
        for k in outRange {
            let syn = connectome.synapses[k]
            // sign is +1 excitatory, -1 inhibitory, 0 UNPOLARISED. A 0 must
            // carry NO current: the old `sign < 0 ? -1 : 1` collapsed 0 to +1
            // and silently asserted excitation for every unpolarised edge —
            // 9.3% of BANC's synaptic weight (2.2M synapses, all the empty
            // transmitter predictions and, after the fix in flying/banc.py,
            // all glutamatergic ones). The edge stays in the connectome; only
            // its valence is absent.
            let polarity: Float = syn.sign > 0 ? 1 : (syn.sign < 0 ? -1 : 0)
            guard polarity != 0 else { continue }
            let gain = parameters.synapticGain
            let efficacy = syn.estimatedEfficacy * gain * Float(syn.synapseCount)
            let signed = efficacy * polarity
            let delayMs = Double(syn.delaySteps) * parameters.dt
            let arrive = time + delayMs
            eventSeq &+= 1
            eventHeap.push(SynapticEvent(time: arrive, seq: eventSeq,
                                         postNeuron: syn.postNeuron, current: signed,
                                         transmitter: syn.transmitter))
        }
    }

    // MARK: - External inputs (sensors)

    /// Inject a sensory current directly into a neuron (sensory transduction
    /// layer, spec #3/#9). Signed; positive excitatory, negative inhibitory.
    public func injectCurrent(into neuron: Int32, current: Float, at time: Double? = nil) {
        let t = time ?? currentTimeMs
        eventSeq &+= 1
        eventHeap.push(SynapticEvent(time: t, seq: eventSeq,
                                     postNeuron: neuron, current: current,
                                     transmitter: 0))
    }

    /// Convenience for spike-driven sensory input: spike `source` → postNeuron
    /// with given efficacy, used by the odor/vision transduction helpers.
    public func injectSpikeInput(from source: Int32, to postNeuron: Int32,
                                 efficacy: Float, delayMs: Double = 1.0) {
        let t = currentTimeMs + delayMs
        eventSeq &+= 1
        eventHeap.push(SynapticEvent(time: t, seq: eventSeq,
                                     postNeuron: postNeuron, current: efficacy,
                                     transmitter: 0))
    }

    // MARK: - Telemetry

    /// Real spikes/sec over the last telemetry window (spec #38 — never faked).
    public var spikesPerSecond: Double { lastWindowSpikesPerSecond }

    /// Indices the engine is currently tracking as refractory (test/telemetry
    /// access to the incremental active set — kept exact in `step()`).
    public var refractoryActiveNeurons: [Int32] { refractoryRing }

    /// Indices whose recent-spike counter is non-zero.
    public var recentActiveNeurons: [Int32] { recentRing }

    /// Indices with a non-zero leaky firing-rate estimate (the motor drive
    /// set — responsive on a sensorimotor timescale, unlike `recentRing`).
    public var firingActiveNeurons: [Int32] { leakyRateRing }

    /// Recent spike count of a neuron (for raster/inspector).
    public func recentSpikes(of neuron: Int32) -> Int {
        guard neuron >= 0 && Int(neuron) < recentSpikesPerNeuron.count else { return 0 }
        return Int(recentSpikesPerNeuron[Int(neuron)])
    }

    /// Total spikes of a neuron since engine start (never decays).
    public func totalSpikes(of neuron: Int32) -> Int {
        guard neuron >= 0 && Int(neuron) < cumulativeSpikes.count else { return 0 }
        return Int(cumulativeSpikes[Int(neuron)])
    }

    /// Leaky firing-rate estimate of a neuron, in Hz, updated every step.
    /// Unlike `recentSpikes` this is not a tumbling inspection window: it can
    /// be sampled at any instant and reported as a rate, and it responds on a
    /// sensorimotor timescale (`motorRateTauMs`) rather than the 1 s window.
    public func rateHz(of neuron: Int32) -> Float {
        guard neuron >= 0 && Int(neuron) < leakyRatePerNeuron.count else { return 0 }
        return leakyRatePerNeuron[Int(neuron)]
    }

    public func firingRate(of neuron: Int32) -> Float {
        guard neuron >= 0 && Int(neuron) < dynamics.count else { return 0 }
        return dynamics[Int(neuron)].firingRate
    }

    public func voltage(of neuron: Int32) -> Float {
        guard neuron >= 0 && Int(neuron) < dynamics.count else { return 0 }
        return dynamics[Int(neuron)].voltage
    }

    /// Active neuron count in current window (real).
    public var activeNeuronCount: Int { Int(activeNeuronsThisWindow) }

    public var pendingEventCount: Int { eventHeap.count }

    // MARK: - Deterministic RNG (xorshift64*)

    @inline(__always) private func nextRandom() -> UInt64 {
        var x = rngState
        x ^= x >> 12
        x ^= x << 25
        x ^= x >> 27
        rngState = x
        return x &* 0x2545F4914F6CDD1D
    }

    @inline(__always) func randomDouble() -> Double {
        Double(nextRandom() >> 11) * (1.0 / 9007199254740992.0)
    }

    // MARK: - Lifecycle / state capture

    /// Snapshot for replay/save (spec #56/#63/#116). Include everything
    /// needed to resume: clock, RNG, dynamics, heap, telemetry state.
    public struct EngineSnapshot: Codable, Sendable {
        public var currentTimeMs: Double
        public var simulationStep: UInt64
        public var rngState: UInt64
        public var spikeCount: UInt64
        public var eventSeq: UInt64
        public var dynamics: [NeuronDynamicsSnapshot]
        public var events: [SynapticEvent]

        // MARK: Telemetry windows (format v2 — see the note on restore)
        //
        // These are not decoration. The two tumbling windows below decide WHEN
        // state is CLEARED, and both are measured against `currentTimeMs`,
        // which the snapshot already restores. Restoring the clock without the
        // window starts makes every window look overdue by the full age of the
        // simulation: the first step after a restore rolls all of them over at
        // once, `lastWindowSpikesPerSecond` is computed by dividing by a window
        // length that never existed, and the recent-spike ring is wiped — so a
        // restored run reports a different `spikesPerSecond` than the run it
        // replaced, from the same seed and the same connectome (spec #63).
        public var spikeEventsThisWindow: UInt64
        public var windowStartTime: Double
        public var lastWindowSpikesPerSecond: Double
        public var recentSpikesPerNeuron: [UInt16]
        public var recentWindowStartTime: Double

        /// Cumulative spikes are a monotone counter the inspector and the
        /// tests read. Leaving them out made `totalSpikes(of:)` restart from
        /// zero on restore while the run continued — a counter that cannot be
        /// compared with itself across a checkpoint.
        public var cumulativeSpikes: [UInt32]

        public var activeNeuronsThisWindow: Int32
        /// Per-neuron bucket marks for the active-neuron count. The sentinel is
        /// `UInt32.max` for "not counted this window"; restoring the count
        /// without the marks would re-count every neuron that fires next.
        public var activeWindowMark: [UInt32]

        /// Model levels are NOT derived from the connectome: the LOD scheduler
        /// and `setModelLevel` mutate them, and they select which integration
        /// equations run. A restore that guessed them would integrate different
        /// math than the run it replaced (spec #48/#63).
        public var modelLevels: [UInt8]
    }

    public struct NeuronDynamicsSnapshot: Codable, Sendable {
        public var voltage: Float
        public var firingRate: Float
        public var adaptation: Float
        public var refractoryRemaining: Float
        public var lastSpikeTime: Double
        /// Leaky motor-drive rate (Hz). Captured because it feeds the motor
        /// readout, so leaving it out would make snapshot/restore
        /// non-deterministic in the body's motion (spec #63).
        public var leakyRate: Float
    }

    public func snapshot() -> EngineSnapshot {
        EngineSnapshot(
            currentTimeMs: currentTimeMs,
            simulationStep: simulationStep,
            rngState: rngState,
            spikeCount: spikeCount,
            eventSeq: eventSeq,
            dynamics: dynamics.enumerated().map { (i, d) in
                NeuronDynamicsSnapshot(voltage: d.voltage, firingRate: d.firingRate,
                                       adaptation: d.adaptation,
                                       refractoryRemaining: d.refractoryRemaining,
                                       lastSpikeTime: d.lastSpikeTime,
                                       leakyRate: leakyRatePerNeuron[i])
            },
            events: eventHeap.allItems,
            spikeEventsThisWindow: spikeEventsThisWindow,
            windowStartTime: windowStartTime,
            lastWindowSpikesPerSecond: lastWindowSpikesPerSecond,
            recentSpikesPerNeuron: recentSpikesPerNeuron,
            recentWindowStartTime: recentWindowStartTime,
            cumulativeSpikes: cumulativeSpikes,
            activeNeuronsThisWindow: activeNeuronsThisWindow,
            activeWindowMark: activeWindowMark,
            modelLevels: modelLevels
        )
    }

    /// Restore from snapshot. Rebuilds the event heap by sorting restored
    /// events deterministically (heap contents are re-validated by callers).
    public func restore(_ snap: EngineSnapshot) {
        currentTimeMs = snap.currentTimeMs
        simulationStep = snap.simulationStep
        rngState = snap.rngState
        spikeCount = snap.spikeCount
        eventSeq = snap.eventSeq

        // Telemetry windows. Restoring the clock alone made the first step
        // after a restore see every window as overdue and roll them all at
        // once (see EngineSnapshot). Copy them back so the restored run
        // continues the windows instead of restarting them.
        spikeEventsThisWindow = snap.spikeEventsThisWindow
        windowStartTime = snap.windowStartTime
        lastWindowSpikesPerSecond = snap.lastWindowSpikesPerSecond
        recentWindowStartTime = snap.recentWindowStartTime
        activeNeuronsThisWindow = snap.activeNeuronsThisWindow

        // Guarded by count because these are per-neuron arrays: an older
        // snapshot (or a hand-written one) must not be able to resize the
        // engine's storage out from under the connectome.
        if snap.recentSpikesPerNeuron.count == recentSpikesPerNeuron.count {
            recentSpikesPerNeuron = snap.recentSpikesPerNeuron
        }
        if snap.cumulativeSpikes.count == cumulativeSpikes.count {
            cumulativeSpikes = snap.cumulativeSpikes
        }
        if snap.activeWindowMark.count == activeWindowMark.count {
            activeWindowMark = snap.activeWindowMark
        }
        if snap.modelLevels.count == modelLevels.count {
            modelLevels = snap.modelLevels
        }

        for (i, d) in snap.dynamics.enumerated() {
            guard i < dynamics.count else { break }
            dynamics[i].voltage = d.voltage
            dynamics[i].firingRate = d.firingRate
            dynamics[i].adaptation = d.adaptation
            dynamics[i].refractoryRemaining = d.refractoryRemaining
            dynamics[i].lastSpikeTime = d.lastSpikeTime
            leakyRatePerNeuron[i] = d.leakyRate
        }
        eventHeap.rebuild(from: snap.events)

        // Rebuild the two active sets from the restored state. They are
        // derived data, so recomputing them is cheaper and safer than
        // serialising them (and a stale set would silently stop decaying a
        // restored refractory window, or keep visiting neurons that are zero).
        refractoryRing.removeAll(keepingCapacity: true)
        recentRing.removeAll(keepingCapacity: true)
        leakyRateRing.removeAll(keepingCapacity: true)
        for i in 0..<dynamics.count where dynamics[i].refractoryRemaining > 0 {
            refractoryRing.append(Int32(i))
        }
        for i in 0..<recentSpikesPerNeuron.count where recentSpikesPerNeuron[i] > 0 {
            recentRing.append(Int32(i))
        }
        for i in 0..<leakyRatePerNeuron.count where leakyRatePerNeuron[i] > 0 {
            leakyRateRing.append(Int32(i))
        }
    }
}

// MARK: - Neuron model selection helpers

extension NeuralEngine {
    /// Assign a model level to a neuron (LOD control, spec #48).
    public func setModelLevel(_ level: UInt8, forNeuron neuron: Int32) {
        guard neuron >= 0 && Int(neuron) < modelLevels.count else { return }
        modelLevels[Int(neuron)] = level
    }

    /// Assign model levels by region (used by adaptive LOD scheduling).
    public func setModelLevel(_ level: UInt8, forRegion region: RegionID) {
        for i in 0..<connectome.neuronCount {
            if RegionID(rawValue: Int(connectome.neurons[i].region)) == region {
                modelLevels[i] = level
            }
        }
    }
}