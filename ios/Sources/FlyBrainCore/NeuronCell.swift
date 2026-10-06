//
//  NeuronCell.swift
//  FlyBrainCore
//
//  Standard neuron model set (LIF / Izhikevich / Poisson / pulse generators)
//  and oscillator neurons that act as time bases for motor pattern
//  generation (aerodynamics, haltere beats, courtship song).
//  All models are deterministic given `rngState`.
//

import Foundation

/// Standard neuron configuration. 64 bytes (x86_64) / 56 (arm64).
public struct NeuronConfig {
    public var type: NeuronType
    public var a: Float32    // time-scale (Izh), tau (LIF), lambda (Poisson)
    public var b: Float32    // recovery (Izh), reset (LIF), amp (pulse)
    public var c: Float32    // reset potential (Izh), resting (LIF), phase0 (osc)
    public var d: Float32    // recovery offset (Izh), refractory (LIF), freq (osc)
    public var e: Float32    // input gain, background current (Izh)
    public var mode: NeuronMode
    public var reserved0: UInt32
    public var rngState: UInt32
    public var reserved1: UInt32
    public var reserved2: UInt64

    public init(type: NeuronType = .lif, a: Float32 = 0.02, b: Float32 = 0.2,
                c: Float32 = -65, d: Float32 = 2.0, e: Float32 = 0.0,
                mode: NeuronMode = .default, rngState: UInt32 = 0) {
        self.type = type
        self.a = a; self.b = b; self.c = c; self.d = d; self.e = e
        self.mode = mode
        self.reserved0 = 0
        self.rngState = rngState == 0 ? 0x9E3779B9 : rngState
        self.reserved1 = 0
        self.reserved2 = 0
    }
}

public enum NeuronType: UInt32 {
    case lif = 0
    case izhikevich = 1
    case poisson = 2
    case pulseGenerator = 3
    case oscillator = 4
}

public enum NeuronMode: UInt32 {
    case `default` = 0
    case tonicSpiking = 1     // Izh RS
    case chattering = 2       // Izh CH
    case fastSpiking = 3      // Izh FS
    case lowThreshold = 4     // Izh LTS
    case poissonRate = 5
}

/// State carried alongside a NeuronConfig (not the full connectome state;
/// per-neuron run state for models that need memory).
public struct NeuronStateVars {
    public var v: Float32      // membrane potential (Izh/LIF)
    public var u: Float32      // recovery variable (Izh)
    public var phase: Float32  // oscillator phase [0,1)
    public var lastSpikeTime: Float32

    public init(v: Float32 = -65, u: Float32 = -13, phase: Float32 = 0, lastSpikeTime: Float32 = -1e9) {
        self.v = v; self.u = u; self.phase = phase; self.lastSpikeTime = lastSpikeTime
    }
}