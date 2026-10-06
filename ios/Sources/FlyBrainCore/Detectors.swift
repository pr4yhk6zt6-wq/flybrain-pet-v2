//
//  Detectors.swift
//  FlyBrainCore
//
//  Fixed-period / windowed biological detectors used by the sensory and
//  motor systems:
//  - SpikeCountWindow: counts spikes in a sliding window (raster/rate pool).
//  - RateDetector: decaying time-window rate estimate per neuron.
//  These are passive observers — they never feed commands back (spec #44).
//

import Foundation

/// Counts spikes arriving per neuron within a (fixed-step) window.
public struct SpikeCountWindow {
    public var windowSteps: Int
    public private(set) var counts: [Int]

    public init(neuronCount: Int, windowSteps: Int) {
        self.windowSteps = windowSteps
        self.counts = [Int](repeating: 0, count: neuronCount)
    }

    /// Called once per simulation step BEFORE stepping dynamics.
    public mutating func advance() {
        // rotate: decay - we simply divide by 2 each step (exponential window)
        for i in 0..<counts.count {
            counts[i] >>= 1
        }
    }

    public mutating func recordSpike(neuron: Int32) {
        guard neuron >= 0 && Int(neuron) < counts.count else { return }
        counts[Int(neuron)] += 1
    }

    public func count(for neuron: Int32) -> Int {
        guard neuron >= 0 && Int(neuron) < counts.count else { return 0 }
        return counts[Int(neuron)]
    }
}

/// Decaying rate estimate (Hz) per neuron over a time window.
public struct RateDetector {
    public var tauMs: Float
    public var dtMs: Float
    public private(set) var rates: [Float]

    public init(neuronCount: Int, tauMs: Float = 100, dtMs: Float = 0.1) {
        self.tauMs = tauMs
        self.dtMs = dtMs
        self.rates = [Float](repeating: 0, count: neuronCount)
    }

    public mutating func advance() {
        // exponential decay window
        let decay = exp(-dtMs / tauMs)
        for i in 0..<rates.count {
            rates[i] *= decay
        }
    }

    public mutating func recordSpike(neuron: Int32) {
        guard neuron >= 0 && Int(neuron) < rates.count else { return }
        rates[Int(neuron)] += 1000.0 / dtMs
    }

    public func rate(for neuron: Int32) -> Float {
        guard neuron >= 0 && Int(neuron) < rates.count else { return 0 }
        return rates[Int(neuron)]
    }
}