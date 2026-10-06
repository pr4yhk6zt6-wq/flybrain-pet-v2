//
//  SimulationCore.swift
//  FlyBrainCore
//
//  Closed-loop orchestrator (spec #28): ties connectome + neural engine +
//  sensory systems + internal state + world + body into one deterministic
//  simulation. This is the single object both Life Mode and Connectome Mode
//  observe (spec #32, #119).
//
//    WORLD → SENSORS → SENSORY NEURONS → CONNECTOME → MOTOR NEURONS
//          → BODY → WORLD → NEW SENSORY INPUT → …
//

import Foundation

/// Protocol the world implements (spec #13, #29): provides sensory fields.
public protocol WorldProvider: AnyObject {
    /// Luminance at a world point (for the compound-eye sampler).
    func luminance(atX: Float, y: Float, z: Float) -> Float
    /// Odor concentration at the antenna position (left/right separated).
    func odorConcentration(atX: Float, y: Float, z: Float) -> (left: Float, right: Float)
    /// Temperature at a position.
    func temperature(atX: Float, y: Float, z: Float) -> Float
}

/// Center of the simulation. Owns the fly's brain + body + the loop drivers.
public final class SimulationCore: @unchecked Sendable {
    public let connectome: Connectome
    public let engine: NeuralEngine
    public let sensory: SensoryInterface
    public let vision: VisionSystem
    public private(set) var internalState: InternalState = InternalState()
    public let parameters: SimulationParameters

    /// World provider (weak to avoid retain cycles; the view owns the world).
    public weak var world: WorldProvider?

    /// Concrete 3D world object when created in-app (spec #29).
    /// When set, it is also assigned as the `world` provider.
    public private(set) var scene: World?

    public func setScene(_ w: World) {
        scene = w
        world = w
    }

    // Fly pose/position (embodiment, spec #18)
    public private(set) var position: SIMD3<Float> = SIMD3(0, 0, 1)
    public private(set) var forward: SIMD3<Float> = SIMD3(1, 0, 0)
    public private(set) var up: SIMD3<Float> = SIMD3(0, 0, 1)

    /// Passive behavior classifier (spec #43/#44) — observes, never controls.
    public let behavior: BehaviorClassifier

    /// Motor pattern generation (Phase 4 starter, spec #17/#20).
    public let motor: MotorSystem
    /// Articulated body (Phase 5 starter, spec #18).
    public private(set) var body = FlyBody()

    /// Motor-neuron drive readout from VNC (per leg group + wing + proboscis).
    private var neuralDrive: [Float] = []

    public init(connectome: Connectome,
                parameters: SimulationParameters = SimulationParameters()) {
        self.connectome = connectome
        self.parameters = parameters
        let engine = NeuralEngine(connectome: connectome, parameters: parameters)
        self.engine = engine
        self.sensory = SensoryInterface(connectome: connectome)
        self.vision = VisionSystem()
        self.behavior = BehaviorClassifier()
        self.motor = MotorSystem()

        // wire the vision sampler to the world
        self.vision.luminanceProvider = { [weak self] x, y, z in
            self?.world?.luminance(atX: x, y: y, z: z) ?? 0.5
        }
    }

    // MARK: - Closed loop

    /// Advance the simulation for `steps` neural steps (each dt ms).
    /// Each step: sample senses → inject → integrate → motor → body → world.
    public func run(steps: Int) {
        for _ in 0..<steps {
            step()
        }
    }

    public func step() {
        let dt = parameters.dt

        // 1) Sensory sampling (biological transduction, spec #9)
        let pose = FlyPose(forwardX: forward.x, forwardY: forward.y, forwardZ: forward.z,
                           upX: up.x, upY: up.y, upZ: up.z)
        let visualEvents = vision.sample(position: (position.x, position.y, position.z),
                                         pose: pose, dt: dt)

        // 2) Inject sensory inputs into connectome neurons
        var sensoryInputs: [SensoryInput] = []
        for ev in visualEvents {
            if let (target, current) = vision.mapToInput(event: ev, connectome: connectome) {
                sensoryInputs.append(SensoryInput(neuron: target, current: current,
                                                  modality: .vision, strength: abs(current) / 80))
            }
        }
        // olfaction (biological sampling, spec #13)
        if let w = world {
            let (oL, oR) = w.odorConcentration(atX: position.x, y: position.y, z: position.z)
            sensoryInputs += sensory.odorInput(concentrationL: oL, concentrationR: oR)
        }
        // internal-state modulation → sensory gain (spec #24)
        let gain = internalState.sensoryGain
        for s in sensoryInputs {
            engine.injectCurrent(into: s.neuron, current: s.current * gain, at: engine.currentTimeMs + s.delayMs)
        }

        // 3) Integrate neural dynamics (sparse, event-driven)
        engine.step()

        // 4) Motor: read out motor-neuron firing → body drive (spec #17/#20)
        //    Motor drive comes from REAL connectome activity in leg/wing/
        //    SEZ neuropils; the CPG converts it into joint drives. This is
        //    the "neural output → muscles" link of the closed loop.
        readMotorDrive()
        motor.update(neuralDrive: neuralDrive, dt: dt)

        // 5) Body integration + world interaction (spec #30): apply motor
        //    output to the articulated body; move the fly; collide with
        //    obstacles; sample world back into senses (close the loop).
        integrateBody(dt: dt)
        integrateLocomotion(dt: dt)

        // 6) Internal physiology
        internalState.advance(dtMs: dt, activityLevel: activityLevel)

        // 7) Passive behavior classification (observation, spec #44)
        behavior.observe(core: self)
    }

    /// Read per-group motor drive from current VNC/SEZ activity (real spikes).
    private func readMotorDrive() {
        var drive = [Float](repeating: 0, count: 6)  // 6 legs
        var wing = 0
        var sez = 0
        for (idx, n) in connectome.neurons.enumerated() {
            let spk = Float(engine.recentSpikes(of: Int32(idx)))
            guard spk > 0 else { continue }
            switch RegionID(rawValue: Int(n.region)) ?? .unknown {
            case .legNeuromere:
                let slot = (n.side == 1 ? 0 : 1) * 3 + (Int(n.type) % 3)
                if slot < drive.count { drive[slot] += spk * 0.25 }
            case .wingNeuropil:
                wing += 1
            case .subesophagealZone:
                sez += 1
            default:
                break
            }
        }
        let d = drive.map { min($0, 1) }
        // refresh full 6-vector for motor CPG (neural drive per leg)
        neuralDrive = d
        motor.wingMuscleDrive = Float(wing) / 6
        motor.proboscisDrive = Float(sez) / 4
    }

    /// Phase-5 placeholder: apply motor output to the articulated body.
    private func integrateBody(dt: Double) {
        // Legs from motor output (joint torques → joint angles handled by CPG)
        for i in 0..<min(motor.legs.count, body.legs.count) {
            body.legs[i].coxa.angle = motor.legs[i].coxa.angle
            body.legs[i].femur.angle = motor.legs[i].femur.angle
            body.legs[i].tibia.angle = motor.legs[i].tibia.angle
        }
        // Wings from output (spec #19)
        let freq = motor.output.wingStrokeFreq          // Hz (up to ~180)
        let tMs = engine.currentTimeMs
        let phaseRad = Float(tMs / 1000.0) * 2 * .pi * max(freq, 1)   // full cycles
        for i in 0..<body.wings.count {
            let amp = motor.output.wingStrokeAmplitude
            body.wings[i].strokeAngle.angle = amp * sin(phaseRad)
            body.wings[i].rotationAngle.angle = amp * 0.3 * cos(phaseRad)
        }
        // Halteres follow wing beat (inertial feedback, spec #16)
        let hb = motor.output.haltereBeat
        for i in 0..<body.halteres.count {
            body.halteres[i].beatFrequency = hb * 180
        }
        // Proboscis
        body.proboscis.angle = motor.proboscisDrive * 0.8
        // Antennae follow head orientation placeholder
        body.leftAntenna.angle = 0.2 + motor.output.antennaAngle
        body.rightAntenna.angle = -0.2 + motor.output.antennaAngle
    }

    /// Phase-5 locomotion: moves the fly from motor output (six-legged gait)
    /// through the world with collision. Velocity scales with leg torque.
    private func integrateLocomotion(dt: Double) {
        // walking speed from motor output (leg torques sum)
        let legDrive = motor.output.legTorques.reduce(0, +) / 6
        let walkSpeed: Float = 8.0   // mm/s max (fly ~ several body lengths/s)
        // heading: straight forward with small turn from L/R asymmetry
        let turn = motor.output.leftRightAsymmetry * 0.5
        let heading = atan2(forward.y, forward.x) + turn * Float(dt)
        let dir = SIMD3(cos(heading), sin(heading), 0)
        forward = dir
        var pos = position + dir * (walkSpeed * legDrive * Float(dt))
        // gravityless walking plane; world collision resolves obstacles
        scene?.resolveCollision(position: &pos)
        position = pos

        // proprioception feedback (spec #15): leg contact → mechano input
        if let w = world {
            // ground contact squeeze — feed a weak mechano tone into VNC
            let legContact = legDrive > 0.05 ? 0.3 : 0.0
            let touchInputs = sensory.touchInput(side: 1, intensity: legContact)
            for t in touchInputs {
                engine.injectCurrent(into: t.neuron, current: t.current, at: engine.currentTimeMs)
            }
        }
    }

    /// Energetic cost of activity (0..1) — placeholder for body work.
    public var activityLevel: Float {
        // healthy proxy: some neurons in leg/wing neuropils fired recently
        let regions: [RegionID] = [.legNeuromere, .wingNeuropil]
        var active = 0
        for (idx, _) in connectome.neurons.enumerated() {
            let r = RegionID(rawValue: Int(connectome.neurons[idx].region)) ?? .unknown
            if regions.contains(r) && engine.recentSpikes(of: Int32(idx)) > 0 {
                active += 1
            }
        }
        return min(Float(active) / 8, 1)
    }

    // MARK: - Embodiment

    public func setPose(position: SIMD3<Float>, forward: SIMD3<Float>, up: SIMD3<Float>) {
        self.position = position
        self.forward = FlyMath.normalize(forward)
        self.up = FlyMath.normalize(up)
    }
}

/// Passive observer (spec #43/#44/#45): watches motor outputs, body motion,
/// neural activity, and environment; labels the current behavior.
/// Never feeds commands back to the fly.
public final class BehaviorClassifier {
    public private(set) var current: BehaviorClass = .unknown
    public private(set) var confidence: Float = 0
    private var history: [BehaviorClass] = []
    private let historyLimit = 30

    public init() {}

    public func observe(core: SimulationCore) {
        // Neural evidence: which region groups are active (real spike counts)
        var legActivity = 0
        var wingActivity = 0
        var proboscisActivity = 0
        var escapeActivity = 0
        for (idx, _) in core.connectome.neurons.enumerated() {
            guard core.engine.recentSpikes(of: Int32(idx)) > 0 else { continue }
            switch RegionID(rawValue: Int(core.connectome.neurons[idx].region)) ?? .unknown {
            case .legNeuromere: legActivity += 1
            case .wingNeuropil: wingActivity += 1
            case .subesophagealZone: proboscisActivity += 1
            case .lobulaPlate: escapeActivity += 1
            default: break
            }
        }

        let threshold = 2
        var cls: BehaviorClass = .resting
        var conf: Float = 0.5
        if escapeActivity >= threshold {
            cls = .escape
            conf = 0.8
        } else if wingActivity >= threshold {
            cls = .flight
            conf = 0.7
        } else if legActivity >= threshold {
            cls = .walking
            conf = 0.7
        } else if proboscisActivity >= threshold {
            cls = .feeding
            conf = 0.6
        } else {
            cls = .resting
            conf = 0.5
        }
        // Smooth: require majority in a small window to switch (avoids flicker)
        history.append(cls)
        if history.count > historyLimit { history.removeFirst() }
        let counts = Dictionary(grouping: history, by: { $0 }).mapValues(\.count)
        if let top = counts.max(by: { $0.value < $1.value }), top.value >= historyLimit / 2 {
            if cls != current {
                current = cls
                confidence = Float(top.value) / Float(historyLimit)
            }
        } else {
            conf = 0.4
        }
        _ = conf
    }

    public func reset() {
        current = .unknown
        confidence = 0
        history.removeAll()
    }
}