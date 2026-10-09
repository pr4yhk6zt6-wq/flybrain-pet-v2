//
//  ConnectomeRenderView.swift
//  FlyBrainPetApp
//
//  Hosts the Metal renderer in SwiftUI and owns the orbit gesture. The camera
//  maths is in FlyBrainCore (`RenderCamera`) so that the conventions it depends
//  on — column-major matrices, Metal's 0..1 depth, the up axis — are covered by
//  `swift test` rather than by looking at the screen. This file is the thin part
//  that cannot be tested, so it is kept as small as it can be.
//

import SwiftUI
import MetalKit
import simd
import FlyBrainCore

struct ConnectomeRenderView: UIViewRepresentable {
    let core: SimulationCore
    /// Called on a tap with the neuron under the finger, or nil for empty
    /// space. The view owns the gesture because only it knows the camera and
    /// the drawable aspect the frame was rendered with.
    var onPick: ((NeuronInspection?) -> Void)? = nil

    /// Must inherit from NSObject: the recognisers below are wired with
    /// `#selector`, which needs an Objective-C-visible target.
    final class Coordinator: NSObject {
        var renderer: ConnectomeRenderer?
        /// How many instances are currently uploaded, so `updateUIView` can tell
        /// a real reload from an ordinary redraw.
        var uploadedCount = -1
        /// Reports a picked neuron (or nil for a tap on empty space). Set by the
        /// SwiftUI layer; the tap itself is handled here because the camera and
        /// the aspect ratio that produced the frame live in the renderer.
        var onPick: ((NeuronInspection?) -> Void)?
        /// The connectome picks are resolved against. The coordinator cannot
        /// reach the view's `core`, and `Connectome` is a reference type, so
        /// this holds the one live instance rather than a copy.
        var connectome: Connectome?

        @objc func tap(_ g: UITapGestureRecognizer) {
            guard let renderer, let view = g.view,
                  let connectome, let model = renderer.currentModel else { return }

            let size = view.bounds.size
            guard size.width > 0, size.height > 0 else { return }
            let p = g.location(in: view)
            // UIKit's origin is top-left with y down; NDC is origin-centred with
            // y up, so y is flipped here and only here.
            let ndc = SIMD2<Float>(Float(p.x / size.width) * 2 - 1,
                                   1 - Float(p.y / size.height) * 2)
            // The aspect the LAST FRAME was drawn with, not the view's current
            // bounds: if the device rotated since, the frame on screen belongs
            // to the old aspect and picking against the new one would name a
            // neuron that is not under the finger. `draw(in:)` reframes on
            // rotation, so this is correct within one frame.
            let found = connectome.neuron(nearestNDC: ndc,
                                          camera: renderer.camera,
                                          model: model,
                                          aspect: renderer.drawnAspect)
            onPick?(found)
        }

        @objc func drag(_ g: UIPanGestureRecognizer) {
            guard let renderer, let view = g.view else { return }
            let t = g.translation(in: view)
            g.setTranslation(.zero, in: view)
            var cam = renderer.camera
            // ~0.005 rad per point: a full-width drag on a ~390 pt screen sweeps
            // about a third of a turn — enough to orbit, not enough to lose the
            // animal.
            cam.yaw += Float(t.x) * 0.005
            // Clamp pitch short of the poles. At ±π/2 the view axis becomes
            // parallel to `up` and the basis stops being determined, so the
            // frame flips; `RenderCamera.basis(forUp:)` documents the same limit.
            cam.pitch = max(-1.45, min(1.45, cam.pitch - Float(t.y) * 0.005))
            renderer.camera = cam
        }

        @objc func pinch(_ g: UIPinchGestureRecognizer) {
            guard let renderer, g.scale > 0 else { return }
            var cam = renderer.camera
            // Stay inside the frustum's own limits: closer than a few near
            // planes clips the cloud open, past the far plane it vanishes.
            cam.distance = max(cam.nearPlane * 4,
                               min(cam.farPlane * 0.9,
                                   cam.distance / Float(g.scale)))
            g.scale = 1
            renderer.camera = cam
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> MTKView {
        let view = MTKView()
        view.device = MTLCreateSystemDefaultDevice()
        view.colorPixelFormat = .bgra8Unorm
        // The engine ticks independently of the display, so a dropped frame
        // shows stale activity rather than stalling the simulation.
        view.preferredFramesPerSecond = 60
        view.isPaused = false
        view.enableSetNeedsDisplay = false
        view.clearColor = MTLClearColor(red: 0.04, green: 0.05, blue: 0.07,
                                        alpha: 1)

        guard let device = view.device,
              let renderer = ConnectomeRenderer(device: device, view: view) else {
            // No Metal device (or no shader): the host shows its own readout, so
            // an empty view is a visible failure rather than a silent one.
            return view
        }

        renderer.configure(model: ConnectomeRenderModel(connectome: core.connectome))
        context.coordinator.uploadedCount = renderer.renderedInstanceCount

        // Only the neurons that are actually firing are pushed each frame, and
        // the renderer clears exactly those on the next frame, so the upload
        // stays O(active) — the same discipline the engine itself is built on.
        let engine = core.engine
        renderer.activityProvider = { [weak engine] in
            guard let engine else { return [] }
            return RenderActivity.snapshot(engine: engine)
        }

        view.delegate = renderer
        context.coordinator.renderer = renderer
        context.coordinator.connectome = core.connectome
        context.coordinator.onPick = onPick

        let tap = UITapGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.tap(_:)))
        // One tap, not a double: a double-tap gesture would delay every pick by
        // the double-click interval for no benefit here.
        tap.numberOfTapsRequired = 1
        view.addGestureRecognizer(tap)

        let pan = UIPanGestureRecognizer(target: context.coordinator,
                                         action: #selector(Coordinator.drag(_:)))
        let pinch = UIPinchGestureRecognizer(target: context.coordinator,
                                             action: #selector(Coordinator.pinch(_:)))
        view.addGestureRecognizer(pan)
        view.addGestureRecognizer(pinch)
        return view
    }

    func updateUIView(_ uiView: MTKView, context: Context) {
        // A finished load swaps the connectome in place. Rebuild only then; an
        // ordinary redraw must not re-upload 153,746 instances.
        guard let renderer = context.coordinator.renderer else { return }
        // A finished load swaps the connectome IN PLACE, so the tap handler must
        // follow the new instance or it would pick against the previous asset.
        context.coordinator.connectome = core.connectome
        context.coordinator.onPick = onPick
        // `neuronCount` on the header is Int32; the coordinator tracks Int.
        let expected = Int(core.connectome.neuronCount)
        if context.coordinator.uploadedCount != expected {
            renderer.configure(model: ConnectomeRenderModel(connectome: core.connectome))
            context.coordinator.uploadedCount = renderer.renderedInstanceCount
        }

        // NOTE: no explicit reframe here, and that is deliberate. `configure`
        // flags the renderer, and `draw(in:)` reframes on the next frame using
        // the DRAWABLE's aspect ratio, which is the only place it is known.
        // Asking for a reframe from `updateUIView` would fit the camera against
        // whatever aspect happened to be cached — 1.0 before the first frame —
        // and a non-square view would frame the model slightly wrong. That was
        // a real bug here once; the fix is to leave the framing to the renderer
        // rather than to re-add a call from the view.
    }
}