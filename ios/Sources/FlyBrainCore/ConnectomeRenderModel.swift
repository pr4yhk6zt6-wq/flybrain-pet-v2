//
//  ConnectomeRenderModel.swift
//  FlyBrainCore
//
//  GPU-facing snapshot of the connectome, plus the pure arithmetic the renderer
//  needs to turn it into pixels.
//
//  Why this lives in FlyBrainCore and not in the app target: it has no Metal
//  dependency at all, so `swift test` on the macOS runner (which has no GPU
//  device to create) can still cover the parts that are easy to get silently
//  wrong — vertex ordering, the activity mapping, the buffer sizing. The Metal
//  types stay in ios/Sources/FlyBrainPetApp/Render/, where only the iOS build
//  sees them.
//
//  This file is a VIEW SUPPORT layer. Nothing in it mutates the simulation, and
//  nothing in it invents data: every field comes from a `NeuronRecord` that was
//  loaded from an asset, or from the engine's leaky rate estimate.
//

import Foundation

/// One neuron as the GPU wants it: a position, a stable identity, and the
/// attributes that are constant for the whole run so they can be uploaded once.
///
/// Field order and types are chosen so the struct is 32 bytes with no implicit
/// padding on either side of the Swift/Metal boundary:
///
///   offset  size  field
///        0     4  x            Float
///        4     4  y            Float
///        8     4  z            Float
///       12     4  packed       UInt32   (region | side | provenance | class)
///       16     4  typeIndex    UInt32   (the asset's cell-type vocabulary index)
///       20     4  index        UInt32   (neuron array index; identifies the row)
///       24     4  _pad0        UInt32   (reserved, written as 0)
///       28     4  _pad1        UInt32   (reserved, written as 0)
///
/// Every member is exactly 4 bytes and the total is a multiple of 8, so a Swift
/// array of these and a Metal `struct` with the same members agree without
/// either side needing `packed`. That is deliberate: Swift pads a struct to its
/// largest member's alignment and Metal does not pad the same way, so a struct
/// containing a `SIMD3<Float>` or a `Bool` is a silent garbage-on-screen bug
/// that cannot be caught anywhere but on a device. Keep it to 4-byte members.
public struct NeuronInstance: Equatable, Sendable {
    public var x: Float
    public var y: Float
    public var z: Float
    /// region | side | provenance | classes packed into one word so the
    /// per-neuron CPU work is a single store.
    ///
    /// Layout, least significant bit first. The widths are sized to the enums
    /// as they actually are, measured in `Types.swift`, not to a round number:
    ///
    ///   bits  0..4  region      RegionID raw, 5 bits — 21 cases, highest raw
    ///                          20, so 4 bits would truncate `endocrineVisceral`
    ///                          into `medulla` without any error anywhere.
    ///   bits  5..8  side        0 centre, 1 left, 2 right (4 bits, spare room)
    ///   bits  9..12 provenance  index into `Provenance.allCases` (6 cases).
    ///                          It is an INDEX, not the enum's `String`
    ///                          rawValue: `Connectome.validate()` bounds-checks
    ///                          it against `allCases.count`.
    ///   bits 13..14 classes     0 none, 1 motor, 2 sensory, 3 both
    ///   bit   15..  unused
    ///
    /// `ConnectomeRenderModelTests` asserts every RegionID case and every
    /// provenance index survives the round trip, so widening either enum fails
    /// in CI instead of silently aliasing two regions into one colour.
    public var packed: UInt32
    /// The asset's vocabulary index for the cell type. NOT a class: two cells
    /// of the same type share it, and it is opaque to the renderer.
    public var typeIndex: UInt32
    /// Index into `Connectome.neurons` — the same index the engine reports
    /// activity in terms of, so a firing index can be looked up in O(1).
    public var index: UInt32
    public var _pad0: UInt32 = 0
    public var _pad1: UInt32 = 0

    public init(x: Float, y: Float, z: Float,
                region: UInt8, side: UInt8, provenance: UInt8,
                isMotor: Bool, isSensory: Bool,
                typeIndex: UInt16, index: UInt32) {
        self.x = x
        self.y = y
        self.z = z
        var cls: UInt32 = 0
        if isMotor { cls |= 1 }
        if isSensory { cls |= 2 }
        self.packed = (UInt32(region) & 0x1F)
            | ((UInt32(side) & 0xF) << 5)
            | ((UInt32(provenance) & 0xF) << 9)
            | (cls << 13)
        self.typeIndex = UInt32(typeIndex)
        self.index = index
    }

    public var region: UInt8 { UInt8(packed & 0x1F) }
    public var side: UInt8 { UInt8((packed >> 5) & 0xF) }
    public var provenance: UInt8 { UInt8((packed >> 9) & 0xF) }
    public var classes: UInt32 { (packed >> 13) & 0x3 }
    public var isMotor: Bool { classes & 1 != 0 }
    public var isSensory: Bool { classes & 2 != 0 }
}

/// Where the neurons of the loaded asset actually are. Both shipped assets are
/// neckless point clouds, so the renderer needs to know the extent to frame the
/// camera honestly rather than guessing a unit cube.
public struct RenderBounds: Equatable, Sendable {
    public var minX: Float, minY: Float, minZ: Float
    public var maxX: Float, maxY: Float, maxZ: Float

    public init(minX: Float, minY: Float, minZ: Float,
                maxX: Float, maxY: Float, maxZ: Float) {
        self.minX = minX; self.minY = minY; self.minZ = minZ
        self.maxX = maxX; self.maxY = maxY; self.maxZ = maxZ
    }

    public var isEmpty: Bool { minX > maxX || minY > maxY || minZ > maxZ }

    public static let empty = RenderBounds(minX: .infinity, minY: .infinity,
                                           minZ: .infinity, maxX: -.infinity,
                                           maxY: -.infinity, maxZ: -.infinity)

    public var center: SIMD3<Float> {
        isEmpty ? SIMD3(0, 0, 0)
                : SIMD3((minX + maxX) / 2, (minY + maxY) / 2, (minZ + maxZ) / 2)
    }

    public var extents: SIMD3<Float> {
        isEmpty ? SIMD3(0, 0, 0)
                : SIMD3(maxX - minX, maxY - minY, maxZ - minZ)
    }

    /// Radius of the bounding sphere containing every vertex, in the asset's
    /// own units (the loader converts nm to mm, so this is mm).
    public var radius: Float {
        isEmpty ? 0 : FlyMath.length(extents) / 2
    }

    fileprivate mutating func include(x: Float, y: Float, z: Float) {
        minX = Swift.min(minX, x); maxX = Swift.max(maxX, x)
        minY = Swift.min(minY, y); maxY = Swift.max(maxY, y)
        minZ = Swift.min(minZ, z); maxZ = Swift.max(maxZ, z)
    }
}

/// Immutable, GPU-ready description of one loaded connectome.
public struct ConnectomeRenderModel {
    public let instances: [NeuronInstance]
    public let bounds: RenderBounds
    /// How many neurons carry a measured class byte. Zero means the asset has
    /// no class information at all, which the renderer states in the legend
    /// rather than colouring every neuron as though it were classified.
    public let classifiedCount: Int
    public let synapseCount: Int

    public var neuronCount: Int { instances.count }

    public init(instances: [NeuronInstance], bounds: RenderBounds,
                classifiedCount: Int, synapseCount: Int) {
        self.instances = instances
        self.bounds = bounds
        self.classifiedCount = classifiedCount
        self.synapseCount = synapseCount
    }

    /// Build once per loaded asset. O(neurons), no allocation per neuron beyond
    /// the single array.
    public init(connectome: Connectome) {
        var out = [NeuronInstance]()
        out.reserveCapacity(connectome.neurons.count)
        var b = RenderBounds.empty
        var classified = 0
        for (i, n) in connectome.neurons.enumerated() {
            out.append(NeuronInstance(
                x: n.x, y: n.y, z: n.z,
                region: n.region, side: n.side, provenance: n.provenance,
                isMotor: n.isMotorNeuron, isSensory: n.isSensoryNeuron,
                typeIndex: n.type, index: UInt32(i)))
            b.include(x: n.x, y: n.y, z: n.z)
            if n.flags != 0 { classified += 1 }
        }
        self.init(instances: out, bounds: b,
                  classifiedCount: classified,
                  synapseCount: connectome.synapses.count)
    }

    public static let empty = ConnectomeRenderModel(
        instances: [], bounds: .empty, classifiedCount: 0, synapseCount: 0)
}

/// One neuron's live state, as uploaded every frame.
///
/// 32 bytes, all 4-byte members, same packing discipline as `NeuronInstance`:
///   offset  size  field
///        0     4  index   UInt32   (which instance row this describes)
///        4     4  rateHz  Float    (engine.rateHz; 0 means silent)
///        8     4  _pad0   UInt32
///       12     4  _pad1   UInt32
public struct NeuronActivity: Equatable, Sendable {
    public var index: UInt32
    public var rateHz: Float
    public var _pad0: UInt32 = 0
    public var _pad1: UInt32 = 0

    public init(index: UInt32, rateHz: Float) {
        self.index = index
        self.rateHz = rateHz
    }
}

public enum RenderActivity {
    /// The reference rate at which a neuron is drawn fully bright, in Hz.
    ///
    /// This is a PRESENTATION choice, not a measurement, and it is deliberately
    /// the same reference the motor readout uses (`motorDriveReferenceHz` = 100)
    /// so that what the user sees bright is what the fly's motor system treats
    /// as a strong drive. Nothing here claims a biological maximum: measured
    /// firing rates in the engine's own estimator sit well below this for
    /// ordinary cells, and a cell above it simply clamps.
    public static let referenceRateHz: Float = 100

    /// Maps a rate to 0...1 brightness. Strictly monotonic in the rate, and
    /// exactly 0 for exactly 0 — a silent neuron must look silent, which is why
    /// there is no ambient floor or idle pulse here.
    public static func intensity(rateHz: Float) -> Float {
        guard rateHz > 0 else { return 0 }
        return Swift.min(rateHz / referenceRateHz, 1)
    }

    /// Collects the live activity of a frame. Reads only the engine's O(active)
    /// ring — never scans the full connectome — and returns an entry per neuron
    /// whose rate is above zero, which is the same set the engine is willing to
    /// talk about.
    public static func snapshot(engine: NeuralEngine) -> [NeuronActivity] {
        let active = engine.firingActiveNeurons
        var out = [NeuronActivity]()
        out.reserveCapacity(active.count)
        for idx in active {
            let rate = engine.rateHz(of: idx)
            if rate > 0 {
                out.append(NeuronActivity(index: UInt32(bitPattern: idx),
                                          rateHz: rate))
            }
        }
        return out
    }
}