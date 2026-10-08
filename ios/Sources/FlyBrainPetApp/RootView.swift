// RootView.swift — tabbed entry: Life Mode (3D-ish scene) + Connectome Mode
// (neural activity HUD placeholder) over the SAME simulation (spec #119).

import SwiftUI
import FlyBrainCore

struct RootView: View {
    @EnvironmentObject private var app: AppModel
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            LifeView()
                .tabItem { Label("Life", systemImage: "ant.fill") }
                .tag(0)
            ConnectomeView()
                .tabItem { Label("Brain", systemImage: "brain.head.profile") }
                .tag(1)
        }
        .overlay(alignment: .top) {
            if let err = app.loadError {
                Text(err).font(.caption).foregroundStyle(.red).padding(6)
                    .background(.ultraThinMaterial).clipShape(Capsule())
            }
        }
    }
}

// MARK: - Life Mode (spec #14/#42) — observe + interact with environment

struct LifeView: View {
    @EnvironmentObject private var app: AppModel

    var body: some View {
        VStack(spacing: 12) {
            // The wordmark from the brand SVG (tools/make_app_icons.py builds
            // the 1x/2x/3x imageset from assets/brand). Rendered, not re-drawn
            // in SwiftUI, so it stays identical to the app icon's lettering.
            //
            // The SVG is a solid plate, not artwork on transparency: every
            // border pixel is the same dark ground as the app icon
            // (measured RGB 7,9,14 across all four edges), with the light
            // lettering in the middle. So it reads correctly on the dark
            // scheme and would look like a dark slab on a light one — worth
            // knowing before it is put on a light background.
            Image("FlyBrainLogo")
                .resizable()
                .scaledToFit()
                .frame(maxWidth: 260)
                .accessibilityLabel("FlyBrain Pet")
            Text("Adult female Drosophila — emergent behavior from a spiking connectome")
                .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)

            if let core = app.core {
                TelemetryStrip(core: core)
                Spacer()
                Text(behaviorText(core))
                    .font(.headline)
                    .padding(10)
                    .background(.ultraThinMaterial)
                    .clipShape(Capsule())
                Spacer()
                controls
            } else if app.loadError == nil {
                ProgressView("Loading connectome…")
            }
        }
        .padding()
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Button {
                let x = (app.core?.position.x ?? 0) + 2
                app.dropFood(at: SIMD3(x, 0, 0))
            } label: {
                Label("Food", systemImage: "leaf.fill")
            }
            Button {
                let x = (app.core?.position.x ?? 0) - 2
                app.dropWater(at: SIMD3(x, 0, 0))
            } label: {
                Label("Water", systemImage: "drop.fill")
            }
            Button { app.toggleLights() } label: {
                Label("Lights", systemImage: "lightbulb.fill")
            }
        }
        .buttonStyle(.bordered)
    }

    private func behaviorText(_ core: SimulationCore) -> String {
        "Behavior: \(core.behavior.current.name) (confidence \(String(format: "%.0f", core.behavior.confidence * 100))%)"
    }
}

// MARK: - Connectome Mode placeholder (Phase 9-10 full Metal renderer)

struct ConnectomeView: View {
    @EnvironmentObject private var app: AppModel

    var body: some View {
        VStack(spacing: 16) {
            Text("Connectome Mode").font(.title2.bold())
            if let core = app.core {
                TelemetryStrip(core: core)
                Text("Full Metal neuron renderer lands in Phase 9-10.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Neurons: \(core.connectome.neuronCount)   Synapses: \(core.connectome.synapseCount)")
                    .font(.callout.monospaced())
                // live spike-rate summary (real telemetry, spec #38)
                Text("Spikes: \(core.engine.spikeCount)")
                    .font(.callout.monospaced())
            }
            Spacer()
        }
        .padding()
    }
}

// MARK: - Real telemetry strip (never faked, spec #38)

struct TelemetryStrip: View {
    let core: SimulationCore

    var body: some View {
        let engine = core.engine
        VStack(alignment: .leading, spacing: 2) {
            Text("sim time \(String(format: "%.2f", engine.currentTimeMs / 1000)) s   "
                 + "spikes \(engine.spikeCount)   "
                 + "active \(engine.activeNeuronCount)")
                .font(.caption.monospaced())
            Text("spikes/s \(String(format: "%.0f", engine.spikesPerSecond))   "
                 + "events \(engine.pendingEventCount)")
                .font(.caption.monospaced())
            // Which motor neurons the drive is read from. Assets written before
            // the cell-class byte existed carry no class, and the readout then
            // falls back to summing every neuron in the neuropil — which on the
            // real BANC release is 98.1% sensory/interneuron, not motor. Say
            // which of the two is happening rather than implying the stronger.
            Text(core.motorClassified
                 ? "motor drive: from motor-labelled cells"
                 : "motor drive: region only (asset has no cell class)")
                .font(.caption2.monospaced())
                .foregroundStyle(core.motorClassified ? Color.secondary : Color.orange)
        }
        .padding(8)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}