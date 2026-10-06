// AppModel.swift — owns the single live simulation shared by Life Mode
// and Connectome Mode (spec #32, #119). No behavior control here; the
// simulation core is driven purely by world signals + connectome dynamics.

import Foundation
import FlyBrainCore

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var core: SimulationCore?
    @Published private(set) var world: World
    @Published private(set) var loadError: String?
    @Published private(set) var isLoaded = false

    private var ticker: Timer?

    init() {
        world = World()
        // default scene (spec #29/#42): a warm lit room with a food source
        world.addLight(LightSource(position: SIMD3(0, 6, 3), intensity: 0.9,
                                   color: SIMD3(1.0, 0.95, 0.85)))
        world.addOdorSource(OdorSource(position: SIMD3(3, 0, 0), kind: .food,
                                       emissionRate: 1.5, diffusionConstant: 4))
        world.addOdorSource(OdorSource(position: SIMD3(-3, 0, 0.5), kind: .water,
                                       emissionRate: 1.0, diffusionConstant: 3))
        world.addObstacle(Obstacle(position: SIMD3(1.5, 0, 0.5), size: SIMD3(0.6, 1.2, 0.8)))
        loadConnectome()
    }

    func loadConnectome() {
        guard let url = Bundle.main.url(forResource: "demo_micro", withExtension: "fbpack") else {
            loadError = "Missing bundled connectome asset (demo_micro.fbpack)"
            return
        }
        do {
            let connectome = try Connectome.loadFBPack(from: url)
            var params = SimulationParameters()
            params.seed = 0x5EED
            let core = SimulationCore(connectome: connectome, parameters: params)
            core.setScene(world)
            core.setPose(position: SIMD3(0, 0.2, 0),
                         forward: SIMD3(1, 0, 0), up: SIMD3(0, 0, 1))
            self.core = core
            isLoaded = true
            startTicker()
        } catch {
            loadError = "Failed to load connectome: \(error.localizedDescription)"
        }
    }

    private func startTicker() {
        stopTicker()
        // neural clock is independent of render (spec #46): step at fixed hz
        let timer = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.tick()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        ticker = timer
    }

    func stopTicker() { ticker?.invalidate(); ticker = nil }

    /// Advance the shared simulation (called from render loop / timer).
    func tick() {
        guard let core else { return }
        core.run(steps: 4)   // 4 × 0.1ms per 60Hz frame → 24ms/s neural time
    }

    // MARK: - Player interaction (environment only, spec #42/#54)

    func dropFood(at p: SIMD3<Float>) {
        world.addOdorSource(OdorSource(position: p, kind: .food,
                                       emissionRate: 1.5, diffusionConstant: 3))
    }

    func dropWater(at p: SIMD3<Float>) {
        world.addOdorSource(OdorSource(position: p, kind: .water,
                                       emissionRate: 1.2, diffusionConstant: 2.5))
    }

    func toggleLights() {
        if world.lights.isEmpty {
            world.addLight(LightSource(position: SIMD3(0, 6, 3), intensity: 0.9))
        } else {
            world.removeAllLights()
        }
    }
}