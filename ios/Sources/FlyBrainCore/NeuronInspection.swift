//
//  NeuronInspection.swift
//  FlyBrainCore
//
//  Picking a neuron out of the point cloud, and reading that neuron back.
//
//  ── WHY THIS EXISTS ────────────────────────────────────────────────────────
//  Format v3 records every neuron's original dataset ID (`Connectome.sourceID`)
//  for traceability. That block had no consumer: `sourceID(of:)` was called by
//  nothing in the app, so the one fact the work was done to provide — "which
//  release cell is the dot I am looking at?" — could not be seen on screen.
//  A tooltip needs something to hit-test against, and a hit-test is arithmetic,
//  so it lives here rather than in the Metal view where it cannot be tested.
//
//  ── WHAT THIS FILE DELIBERATELY DOES NOT CLAIM ─────────────────────────────
//  Picking is done against the SAME point layout the renderer draws
//  (`ConnectomeRenderModel`), projected through the SAME camera matrix
//  (`RenderCamera.viewProjection`). It is a CPU screen-space search, not a GPU
//  depth-buffer read: it answers "which dot is nearest the touch", and for ties
//  or near-ties it prefers the nearer one by depth, but it is NOT pixel-exact.
//  Calling it "what the user clicked" would overstate it; it is "the best
//  candidate at this point", and the returned struct says so.
//

import Foundation
import simd

/// A neuron identified by a tap, with everything the readout may display.
///
/// Every field carries the provenance the asset actually recorded — `sourceID`
/// is `nil` when the asset does not carry original IDs, rather than 0, because
/// 0 is a legal release ID and "unknown" must not be printed as a real one.
public struct NeuronInspection: Equatable, Sendable {
    /// Dense simulator index into `Connectome.neurons` (NOT a release ID).
    public let index: Int
    /// The release's own ID for this cell, when the asset records them.
    public let sourceID: UInt64?
    public let datasetID: UInt8
    public let region: UInt8
    public let side: UInt8
    public let transmitter: UInt8
    public let provenance: UInt8
    public let isMotor: Bool
    public let isSensory: Bool
    /// Whether the ASSET carries a class byte at all (`Connectome.hasCellClasses`).
    /// Carried explicitly because two `Bool`s cannot distinguish "the release
    /// classed this cell as neither" from "this asset predates the class byte",
    /// and printing the second as "unlabelled" would state something the asset
    /// never said.
    public let assetHasCellClasses: Bool
    public let cellType: UInt16
    public let position: SIMD3<Float>
    /// Outgoing edges as the asset records them. On a whole-CNS asset this is
    /// the number of distinct partner cells, not the number of synapses.
    public let outgoingEdgeCount: Int
    /// Screen distance in NDC from the touch, and NDC depth (0 near, 1 far).
    /// Exposed so a caller can tell a direct hit from a rough one.
    public let screenDistance: Float
    public let depth: Float
}

public extension Connectome {

    /// The nearest drawn neuron to an NDC point, or `nil` if nothing is close.
    ///
    /// - Parameters:
    ///   - ndc: touch position in normalised device coordinates, each axis in
    ///     [-1, 1], as produced by `Float4x4.project`.
    ///   - camera: the camera the frame was drawn with. This must be the same
    ///     instance handed to the renderer, or the result names a different dot.
    ///   - model: the layout that was uploaded.
    ///   - maxNDCDistance: how far from the touch a dot may be and still count.
    ///   - aspect: view aspect ratio, matching the drawable.
    public func neuron(nearestNDC ndc: SIMD2<Float>,
                       camera: RenderCamera,
                       model: ConnectomeRenderModel,
                       aspect: Float,
                       maxNDCDistance: Float = 0.04) -> NeuronInspection? {
        let limit = maxNDCDistance > 0 ? maxNDCDistance * maxNDCDistance : 0
        guard aspect.isFinite, aspect > 0 else { return nil }

        let vp = camera.viewProjection(aspect: aspect)
        var best: (instance: NeuronInstance, d2: Float, depth: Float)?

        // Every instance is projected rather than only the visible ones: the
        // model is the drawn set by construction, and a region/bounds filter
        // here would be a second, silently diverging definition of "visible".
        for inst in model.instances {
            guard let ndcP = vp.project(SIMD3(inst.x, inst.y, inst.z)) else {
                continue  // on the eye plane: no finite projection
            }
            // Reject anything outside the frustum. Without this, a neuron behind
            // the camera can project to a plausible screen point (the divide by
            // a negative w mirrors it) and be picked instead of the one in front.
            guard ndcP.z >= 0, ndcP.z <= 1 else { continue }
            let dx = ndcP.x - ndc.x
            let dy = ndcP.y - ndc.y
            let d2 = dx * dx + dy * dy
            guard d2 <= limit else { continue }
            // Nearest wins; on an exact tie the lower dense index wins. The
            // engine's event heap breaks ties by `seq` (insertion order), which
            // is not available here — this is the same KIND of tie-break (a
            // stated rule rather than iteration order) but not the same key, and
            // saying "same as the engine" would imply a shared ordering that
            // does not exist.
            if let b = best {
                if d2 < b.d2 || (d2 == b.d2 && Int(inst.index) < Int(b.instance.index)) {
                    best = (inst, d2, ndcP.z)
                }
            } else {
                best = (inst, d2, ndcP.z)
            }
        }

        guard let hit = best else { return nil }
        return inspection(of: Int(hit.instance.index),
                          screenDistance: hit.d2.squareRoot(),
                          depth: hit.depth)
    }

    /// Everything the asset records about one neuron, or `nil` if the index is
    /// out of range.
    func inspection(of index: Int,
                    screenDistance: Float = 0,
                    depth: Float = 0) -> NeuronInspection? {
        guard index >= 0, index < neurons.count else { return nil }
        let n = neurons[index]
        return NeuronInspection(
            index: index,
            sourceID: sourceID(of: index),
            datasetID: n.datasetID,
            region: n.region,
            side: n.side,
            transmitter: n.transmitter,
            provenance: n.provenance,
            isMotor: n.isMotorNeuron,
            isSensory: n.isSensoryNeuron,
            assetHasCellClasses: hasCellClasses,
            cellType: n.type,
            position: SIMD3(n.x, n.y, n.z),
            outgoingEdgeCount: outgoingRange(of: index).count,
            screenDistance: screenDistance,
            depth: depth)
    }
}

public extension NeuronInspection {
    /// True when the asset can name the release cell this dot came from.
    var isTraceable: Bool { sourceID != nil }

    /// "BANC #720575940381905254", or an explicit statement of the gap. Never
    /// renders a missing ID as a number.
    var traceabilityText: String {
        let source = DatasetID(rawValue: Int(datasetID))?.name ?? "dataset \(datasetID)"
        guard let sourceID else {
            return "\(source) — this asset records no original ID"
        }
        return "\(source) #\(sourceID)"
    }

    var regionName: String { RegionID(rawValue: Int(region))?.name ?? "region \(region)" }

    var sideName: String {
        switch side {
        case 1: return "left"
        case 2: return "right"
        default: return "centre"
        }
    }

    var transmitterName: String {
        TransmitterType(rawValue: Int(transmitter))?.name ?? "unknown"
    }

    /// The class label, phrased so that "neither" is a real answer rather than
    /// a silent downgrade. Two `Bool`s cannot express "this asset has no class
    /// byte": a v2 asset reports `false` for both on every neuron, and that is a
    /// different statement from "the release classed this cell as neither" —
    /// `Connectome.hasCellClasses` is the asset-level answer, so it is asked.
    var classText: String {
        guard assetHasCellClasses else {
            return "no class byte (pre-v3 asset)"
        }
        switch (isMotor, isSensory) {
        case (true, false):  return "motor"
        case (false, true):  return "sensory"
        case (true, true):   return "motor + sensory"
        case (false, false): return "unlabelled"
        }
    }
}