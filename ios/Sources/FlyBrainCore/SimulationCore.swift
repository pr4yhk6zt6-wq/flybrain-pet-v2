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

    // Fly pose/position (embodiment, spec #18). Y-UP world: dorsal is +y.
    // These are only the pre-step defaults — `integrateLocomotion` republishes
    // them from the rigid body on every step, and `setPose` is what the app
    // calls to place the fly.
    public private(set) var position: SIMD3<Float> = SIMD3(0, 0.8, 0)
    public private(set) var forward: SIMD3<Float> = SIMD3(1, 0, 0)
    public private(set) var up: SIMD3<Float> = SIMD3(0, 1, 0)

    /// Passive behavior classifier (spec #43/#44) — observes, never controls.
    public let behavior: BehaviorClassifier

    /// Motor pattern generation (Phase 4/6, spec #17/#20).
    public let motor: MotorSystem
    /// Articulated body (Phase 5 starter, spec #18).
    public private(set) var body = FlyBody()
    /// Rigid-body dynamics of the whole animal (Phase 6/7, spec #18, #22).
    /// Owns pose, velocity and the physical response to motor output; the
    /// SensoryInterface samples the world from its pose.
    public private(set) var dynamics = BodyDynamics()

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

    /// A high step-time gate lives in the neural engine, but the body owns its
    /// own clock (spec #22 multirate): the reflex check runs every NN step.
    private static let bodyStepsPerNeuralStep = 1

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

        // 5) Body integration + world interaction (spec #30): apply the motor
        //    command to the rigid body, integrate contact forces, move the
        //    fly, collide with the scene, then let the new pose drive the
        //    next sensory sample (this is what closes the loop).
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
        // `recentSpikes > 0` is exactly the engine's recent active set, so walk
        // that (O(active)) instead of scanning every neuron each step.
        for idx in engine.recentActiveNeurons {
            let spk = Float(engine.recentSpikes(of: idx))
            guard spk > 0 else { continue }
            let n = connectome.neurons[Int(idx)]
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

    /// Embody locomotion. The fly is a rigid body with mass and inertia; the
    /// motor command produces leg traction and wing forces, which are the ONLY
    /// sources of momentum. Integration and collision resolution happen in
    /// `dynamics` (spec #18, #30), and the resulting pose feeds the next
    /// sensory sample — walking speed is therefore an outcome of the loop, not
    /// a constant chosen here.
    private func integrateLocomotion(dt: Double) {
        // The muscles' command comes from the connectome readout (spikes).
        var command = BodyMotorCommand()
        command.legContactFraction = motor.output.legContactFraction
        command.legsInContact = motor.output.legsInContact
        command.forwardSpeedTarget = motor.output.forwardSpeedTarget
        command.lateralSpeedTarget = motor.output.lateralSpeedTarget
        command.wingStrokeFrequency = motor.output.wingStrokeFreq
        command.wingStrokeAmplitude = motor.output.wingStrokeAmplitude
        command.wingAsymmetry = motor.output.leftRightAsymmetry
        command.haltereDrive = motor.output.haltereDrive
        command.pitchBias = motor.output.pitchBias

        // Ground contact is handled INSIDE the solver from the leg-contact state
        // in the command, and the substrate force itself comes from the contact
        // solver in `BodyDynamics.step` — there is no mode flag here, and no
        // place in this function where the fly is "put" into the air.

        // Body clock (spec #22): the rigid body integrates at its own rate,
        // never tied to render FPS and decoupled from the neural dt. The body
        // solver works in seconds (mm/mg/s) while `parameters.dt` is in
        // milliseconds, so the two clocks are converted explicitly here.
        dynamics.step(command: command, dt: Float(dt / 1000))

        // Publish the new pose for rendering, sensing and telemetry.
        position = dynamics.position
        forward = dynamics.forward
        up = dynamics.up

        resolveSceneCollision()

        applyArticulation()

        // Proprioceptive / mechanosensory reafference (spec #15): leg load,
        // wing strain and airflow now reach the VNC from the real contact
        // state, closing the body→sensor arc.
        emitMechanosensoryReafference(dt: dt)
    }

    /// Push the physics pose into the articulated body for rendering, and read
    /// the joint angles back from the motor cycle.
    private func applyArticulation() {
        for i in 0..<min(motor.legs.count, body.legs.count) {
            body.legs[i].coxa.angle = motor.legs[i].coxa.angle
            body.legs[i].femur.angle = motor.legs[i].femur.angle
            body.legs[i].tibia.angle = motor.legs[i].tibia.angle
            body.legs[i].isSwing = motor.legs[i].isSwing
        }
        let freq = motor.output.wingStrokeFreq
        let phaseRad = Float(dynamics.simulationTimeMs / 1000.0) * 2 * .pi * max(freq, 1)
        for i in 0..<body.wings.count {
            let amp = motor.output.wingStrokeAmplitude
            body.wings[i].strokeAngle.angle = amp * sin(phaseRad)
            body.wings[i].rotationAngle.angle = amp * 0.3 * cos(phaseRad)
        }
        let hb = motor.output.haltereBeat
        for i in 0..<body.halteres.count {
            body.halteres[i].beatFrequency = hb * 180
        }
        body.proboscis.angle = motor.proboscisDrive * 0.8
        body.leftAntenna.angle = 0.2 + motor.output.antennaAngle
        body.rightAntenna.angle = -0.2 + motor.output.antennaAngle
    }

    /// The world acts on the BODY, not on a teleported point (spec #30): when
    /// the solver has pushed the fly into a solid obstacle, the scene resolves
    /// it, and the corrected position — plus the momentum actually absorbed —
    /// goes back into the rigid body so the next step starts from the real
    /// state. Without this the fly would walk through walls while the renderer
    /// and the sensory sampler disagreed about where it was.
    private func resolveSceneCollision() {
        guard let scene else { return }
        var p = dynamics.position
        // A fly body is about 2.5 mm long; use it as the contact radius so the
        // animal cannot intersect an obstacle by more than its own size.
        let radius = min(max(dynamics.parameters.bodyLengthMm * 0.5, 0.1), 1.0)
        let before = p
        scene.resolveCollision(position: &p, radius: radius)
        guard p != before else { return }
        dynamics.setPosition(p)
        // Momentum was absorbed by the obstacle: drop the component of the
        // velocity that pointed into the surface rather than letting the body
        // keep its speed while standing still (which would be free energy).
        let push = p - before
        let n = FlyMath.normalize(push)
        let into = FlyMath.dot(dynamics.body.velocity, n)
        if into < 0 {
            dynamics.setVelocity(dynamics.body.velocity - n * into)
        }
    }

    /// Mechanosensory reafference from the real physical state (spec #15):
    /// tarsal load, haltere-derived body rotation, and wing strain feed
    /// ascending pathways. These are measurements of the body, not commands.
    private func emitMechanosensoryReafference(dt: Double) {
        let load = dynamics.groundLoadFraction        // 0..1 from contact solver
        if load > 0.01 {
            for t in sensory.touchInput(side: 1, intensity: Float(load) * 0.6) {
                engine.injectCurrent(into: t.neuron, current: t.current,
                                     at: engine.currentTimeMs)
            }
        }
        // Haltere input is proportional to actual angular velocity and to the
        // Coriolis force the vibrating halteres experience while rotating —
        // this is the feedback that stabilises flight (spec #16).
        let rate = dynamics.angularRateMagnitude        // deg/s — magnitude only
        if rate > 1 {
            // The haltere organ reports a SIGNED rotation rate (left/right
            // turns drive opposite sets of stabilising muscles), so the sign of
            // the body's yaw rate is preserved and only the magnitude is
            // normalised into the organ's operating range.
            let signed = dynamics.yawRateDegPerS
            let intensity = min(max(signed / 500, -1), 1)
            for m in sensory.haltereInput(rotationRate: intensity) {
                engine.injectCurrent(into: m.neuron, current: m.current,
                                     at: engine.currentTimeMs)
            }
        }
        // Wing strain (campaniform sensilla) whenever the wings are beating.
        if motor.output.wingStrokeFreq > 1 {
            let strain = min(motor.output.wingStrokeFreq / 180, 1) * 0.3
            for m in sensory.wingStrainInput(intensity: strain) {
                engine.injectCurrent(into: m.neuron, current: m.current,
                                     at: engine.currentTimeMs)
            }
        }
    }

    /// Energetic cost of activity (0..1) — placeholder for body work.
    public var activityLevel: Float {
        // healthy proxy: some neurons in leg/wing neuropils fired recently.
        // `recentSpikes > 0` is exactly what the engine's recent active set
        // tracks, so walk that (O(active)) instead of scanning all neurons.
        var active = 0
        for idx in engine.recentActiveNeurons {
            guard engine.recentSpikes(of: idx) > 0 else { continue }
            let r = RegionID(rawValue: Int(connectome.neurons[Int(idx)].region)) ?? .unknown
            if r == .legNeuromere || r == .wingNeuropil { active += 1 }
        }
        return min(Float(active) / 8, 1)
    }

    // MARK: - Embodiment

    /// Place the fly in the world (spawn, teleport). This must move the RIGID
    /// BODY, not just the published pose: `integrateLocomotion` overwrites the
    /// published pose from `dynamics` on the very next step, so setting only
    /// the core's copy would silently snap the fly back to the solver state.
    public func setPose(position: SIMD3<Float>, forward: SIMD3<Float>, up: SIMD3<Float>) {
        self.position = position
        let f = FlyMath.normalize(forward)
        let u = FlyMath.normalize(up)
        self.forward = f
        self.up = u
        dynamics.teleport(position: position, forward: f, up: u)
        // Velocity belonged to the old place; carrying it across a teleport
        // would make the next step meaningless.
        dynamics.setVelocity(SIMD3(0, 0, 0))
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
        // Walk the engine's active set, not all neurons: only it can have a
        // non-zero recent count, and the sweep was O(153,746) per observe().
        for idx in core.engine.recentActiveNeurons {
            guard core.engine.recentSpikes(of: idx) > 0 else { continue }
            switch RegionID(rawValue: Int(core.connectome.neurons[Int(idx)].region)) ?? .unknown {
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