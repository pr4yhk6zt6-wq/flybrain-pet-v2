//
//  ConnectomeRenderer.swift
//  FlyBrainPetApp
//
//  Metal renderer for the connectome point cloud. This file owns the GPU
//  resources and the draw call; all the arithmetic it depends on (the instance
//  layout, the activity mapping, the region palette, the camera fit) lives in
//  FlyBrainCore, where `swift test` on a machine with no GPU can still cover it.
//
//  What is deliberately NOT here: any computation that could be tested. This
//  layer is thin because a renderer bug is expensive to diagnose — it shows up
//  as "the screen is wrong", not as a failing assertion.
//

import Metal
import MetalKit
import simd
import FlyBrainCore

private extension Float4x4 {
    /// Forwards the core's verified column accessor into the GPU's 64-byte
    /// inline matrix type. The mapping itself is tested in
    /// `RenderCameraTests.testColumnMajorColumnsMatchTransform`, because this
    /// target has no test bundle.
    var simdMatrix: simd_float4x4 {
        let c = columnMajorColumns
        return simd_float4x4(columns: (c[0], c[1], c[2], c[3]))
    }
}

/// A `RegionColor` as the shader declares it: float3 + padding, 16 bytes.
private struct RegionColor {
    // Four separate Floats, NOT a SIMD3<Float>: a vector type is padded to its
    // full width for alignment, so `{SIMD3<Float>, Float}` is size 20 / stride
    // 32 in BOTH Swift and Metal. That happens to agree, but it agrees because
    // two different padding rules land on the same answer — and the earlier
    // version of this struct asserted `stride == 16` in a precondition, which
    // would have trapped on device. Four Floats are 16 bytes with no padding
    // rule involved on either side, so the layout is exactly what it looks like.
    var r: Float
    var g: Float
    var b: Float
    var a: Float
}

/// A `Uniforms` as the shader declares it. 16-byte aligned because it leads with
/// a 4×4 matrix.
private struct Uniforms {
    var viewProjection: simd_float4x4 = matrix_identity_float4x4
    var pointSize: Float = 2
    var referenceRateHz: Float = 100
    var maxActivityLift: Float = 0.25
    var _pad0: Float = 0
}

final class ConnectomeRenderer: NSObject, MTKViewDelegate {
    private let device: MTLDevice
    private let queue: MTLCommandQueue
    private let pipeline: MTLRenderPipelineState

    private var neuronBuffer: MTLBuffer?
    private var rateBuffer: MTLBuffer?
    private var paletteBuffer: MTLBuffer?
    private var neuronCount = 0

    /// Invoked on the frame's source data. Returns the live activity for this
    /// frame; the renderer uploads only the differences. Set by the view.
    var activityProvider: (() -> [NeuronActivity])?

    init?(device: MTLDevice, view: MTKView) {
        guard let queue = device.makeCommandQueue() else { return nil }
        self.device = device
        self.queue = queue

        let library: MTLLibrary
        do {
            library = try device.makeDefaultLibrary(bundle: Bundle.main)
        } catch {
            return nil
        }
        guard let vfn = library.makeFunction(name: "neuron_vertex"),
              let ffn = library.makeFunction(name: "neuron_fragment") else {
            return nil
        }

        let desc = MTLRenderPipelineDescriptor()
        desc.vertexFunction = vfn
        desc.fragmentFunction = ffn
        desc.colorAttachments[0].pixelFormat = view.colorPixelFormat
        // Additive blending: overlapping neurons accumulate rather than the last
        // one winning, so a dense cluster reads as brighter than a sparse one.
        // That is the whole point of drawing activity as points — but it does
        // mean a very dense region can saturate to white, which is the honest
        // trade: the renderer shows accumulated activity, not per-neuron colour
        // in dense areas. Depth testing is off, so draw order never changes the
        // result.
        desc.colorAttachments[0].isBlendingEnabled = true
        desc.colorAttachments[0].rgbBlendOperation = .add
        desc.colorAttachments[0].alphaBlendOperation = .add
        desc.colorAttachments[0].sourceRGBBlendFactor = .sourceAlpha
        desc.colorAttachments[0].destinationRGBBlendFactor = .one
        desc.colorAttachments[0].sourceAlphaBlendFactor = .one
        // Alpha is deliberately NOT accumulated: the fragment shader outputs
        // alpha 1.0, so adding it every pixel would drive the drawable's alpha
        // past 1 and leave garbage there. Keeping destination alpha at zero
        // pins it to 1, which is what an opaque layer expects. Colour still
        // accumulates additively, which is the effect that matters.
        desc.colorAttachments[0].destinationAlphaBlendFactor = .zero

        do {
            self.pipeline = try device.makeRenderPipelineState(descriptor: desc)
        } catch {
            return nil
        }
        super.init()
    }

    /// Uploads the static per-neuron data once. Safe to call again on reload;
    /// the old buffers are released.
    func configure(model: ConnectomeRenderModel) {
        // The layout contract with the shader. A mismatch shows up as garbage
        // geometry with no error anywhere, so check the one number the compiler
        // will not: the stride the GPU will assume.
        precondition(MemoryLayout<NeuronInstance>.stride == 32,
                     "NeuronInstance must be 32 bytes to match the Metal struct; "
                     + "got \(MemoryLayout<NeuronInstance>.stride)")
        precondition(MemoryLayout<RegionColor>.stride == 16,
                     "RegionColor must be 16 bytes to match the shader")

        let insts = model.instances
        neuronCount = insts.count
        renderedInstanceCount = neuronCount
        // A fresh buffer starts silent, so nothing from the previous asset can
        // survive a reload.
        previousActive.removeAll(keepingCapacity: true)
        guard neuronCount > 0 else {
            neuronBuffer = nil
            rateBuffer = nil
            return
        }

        neuronBuffer = insts.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return nil }
            return device.makeBuffer(bytes: base, length: raw.count,
                                     options: .storageModeShared)
        }
        // Label helps when reading a GPU frame capture; costs nothing at run time.
        neuronBuffer?.label = "NeuronInstance[\(neuronCount)]"

        // Frame the model once the drawable's aspect is known, not before — the fit
    // depends on the aspect ratio (see `RenderCamera.fitted`). Without this the
    // camera keeps a placeholder fit and the real 12.02 mm BANC cloud renders
    // as a speck or misses the frustum entirely.
    loadedBounds = model.bounds
    needsReframe = true

    // Direct write into the GPU buffer, so no shadow copy of 153,746 floats is
        // kept on the CPU. Zeroed explicitly: `makeBuffer` does NOT promise
        // zeroed contents, and a frame drawn before the first activity upload
        // would otherwise show whatever was in that memory.
        rateBuffer = device.makeBuffer(length: neuronCount * MemoryLayout<Float>.stride,
                                       options: .storageModeShared)
        if let rateBuffer {
            memset(rateBuffer.contents(), 0, rateBuffer.length)
        }
        rateBuffer?.label = "rateHz[\(neuronCount)]"

        // Pad the palette to the full 5-bit region domain (32 slots). The
        // shader indexes with the raw packed bits, so a table of only 21
        // entries would be read out of bounds the moment an asset carries a
        // code this build does not know.
        var padded = [RegionColor](repeating: RegionColor(r: 0, g: 0, b: 0, a: 1),
                                   count: 32)
        for i in 0..<min(32, RenderPalette.regionColors.count) {
            let c = RenderPalette.regionColors[i]
            padded[i] = RegionColor(r: c.x, g: c.y, b: c.z, a: 1)
        }
        paletteBuffer = padded.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return nil }
            return device.makeBuffer(bytes: base, length: raw.count,
                                     options: .storageModeShared)
        }
        paletteBuffer?.label = "RegionColor[32]"
    }

    /// How many instances the last `configure` uploaded. The view uses this to
    /// notice a reload rather than re-uploading 153,746 instances every update.
    private(set) var renderedInstanceCount = 0

    /// The live camera. Getting it returns the current view; setting it stores
    /// the value — `draw(in:)` is what applies it, so a gesture never touches
    /// the GPU directly and never races the render loop.
    ///
    /// Initialised here rather than in `init` because `init` has several early
    /// `return nil` paths before `super.init()`, and Swift will not let a
    /// stored property be assigned in a failable initialiser after those.
    var camera = RenderCamera(target: .zero, distance: 10, yaw: 0, pitch: 0,
                              up: SIMD3(0, 0, 1),
                              nearPlane: 0.01, farPlane: 1000)

    /// Bounds of the loaded model, kept so the camera can be refitted once the
    /// drawable's aspect ratio is actually known.
    private var loadedBounds: RenderBounds?

    /// Set by `configure`, cleared by the first `draw`. Framing DEPENDS on the
    /// aspect ratio, which is not known until there is a drawable — the first
    /// version of this reframed inside `configure` against the fallback aspect
    /// of 1, so every non-square view framed the model slightly wrong.
    private var needsReframe = false

    /// Aspect of the last frame drawn. Kept so a "reset view" control can
    /// reframe immediately instead of waiting for the next frame.
    private var lastAspect: Float = 1

    /// Returns the camera to the fitted view for the loaded model, keeping the
    /// user's orbit angles. Used by a reset control.
    func resetView() {
        guard let bounds = loadedBounds else { return }
        camera = RenderCamera.fitted(to: bounds, aspect: lastAspect)
    }

    // MARK: - MTKViewDelegate

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {
        guard size.height > 0 else { return }
        lastAspect = Float(size.width / size.height)
    }

    func draw(in view: MTKView) {
        guard let neuronBuffer, let rateBuffer, let paletteBuffer,
              let drawable = view.currentDrawable,
              let pass = view.currentRenderPassDescriptor,
              let command = queue.makeCommandBuffer(),
              neuronCount > 0 else { return }

        // Compute the aspect once, up front, and use it for both framing and the
        // matrices. Relying on the `drawableSizeWillChange` callback to have
        // populated this before the first frame depends on delegate ordering;
        // computing it from the drawable in hand does not.
        let aspect = view.drawableSize.height > 0
            ? Float(view.drawableSize.width / view.drawableSize.height) : 1
        lastAspect = aspect

        // Frame the model on the first real frame, when the aspect is known.
        if needsReframe, let bounds = loadedBounds {
            camera = RenderCamera.fitted(to: bounds, aspect: aspect)
            needsReframe = false
        }

        // --- activity: upload only the neurons that are actually firing -----
        // The GPU buffer is the source of truth; only entries that CHANGED are
        // written. Copying the whole shadow array instead would be 153,746
        // floats (614 KB) of memcpy every frame — 37 MB/s at 60 fps, which is
        // exactly the whole-array sweep that the engine's O(active) design
        // (docs/PERFORMANCE.md) exists to avoid on the CPU side.
        let rates = rateBuffer.contents().assumingMemoryBound(to: Float.self)
        for i in previousActive {
            rates[i] = 0                      // return last frame's to silence
        }
        previousActive.removeAll(keepingCapacity: true)
        if let provider = activityProvider {
            for a in provider() {
                let i = Int(a.index)
                guard i >= 0 && i < neuronCount else { continue }
                rates[i] = a.rateHz
                previousActive.append(i)
            }
        }

        // --- uniforms -------------------------------------------------------
        var u = Uniforms()
        u.viewProjection = camera.viewProjection(aspect: aspect).simdMatrix
        u.pointSize = max(1.5, Float(view.drawableSize.width) * 0.0035)
        u.referenceRateHz = RenderActivity.referenceRateHz
        u.maxActivityLift = RenderPalette.maxActivityLift

        pass.colorAttachments[0].clearColor = MTLClearColor(red: 0.04, green: 0.05,
                                                           blue: 0.07, alpha: 1)
        pass.colorAttachments[0].loadAction = .clear
        pass.colorAttachments[0].storeAction = .store

        guard let encoder = command.makeRenderCommandEncoder(descriptor: pass) else {
            return
        }
        encoder.setRenderPipelineState(pipeline)
        encoder.setVertexBuffer(neuronBuffer, offset: 0, index: 0)
        encoder.setVertexBuffer(rateBuffer, offset: 0, index: 1)
        encoder.setVertexBytes(&u, length: MemoryLayout<Uniforms>.stride, index: 2)
        encoder.setVertexBuffer(paletteBuffer, offset: 0, index: 3)
        // One vertex, one instance per neuron: the point sprite is the whole
        // primitive, so there is no per-vertex array to index.
        encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: 1,
                               instanceCount: neuronCount)
        encoder.endEncoding()

        command.present(drawable)
        command.commit()
    }

    /// Indices left non-zero in the GPU rate buffer by the previous frame. Kept
    /// so the next frame can return exactly those entries to zero without
    /// touching any other — the upload is O(active), not O(neurons).
    private var previousActive: [Int] = []
}