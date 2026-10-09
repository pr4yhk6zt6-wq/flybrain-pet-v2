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
    /// Taste acceptance at a point (+ = phagostimulant, − = aversive, 0 =
    /// nothing contactable there). Separate from `odorConcentration` because
    /// contact chemoreception is a CONTACT sense: it requires the proboscis to
    /// reach the substrate, so it falls off far more sharply than the volatile
    /// odor field the antennae sample from a distance.
    func tasteAcceptance(atX: Float, y: Float, z: Float) -> Float
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
    /// Per-group motor drive for the current step (leg 0-5). Internal rather than
    /// private so the readout tests can assert on the command the body receives,
    /// not just on the composition counters.
    var neuralDrive: [Float] = []

    /// Leg-group firing rate (Hz) at which the drive saturates. INFERRED: a
    /// single explicit reference scale replacing the three inconsistent
    /// implicit ones (0.25-per-spike, /6, /4) that made identical activity
    /// mean different things for legs, wings and proboscis.
    public var motorDriveReferenceHz: Float = 100

    // MARK: - Tarsal-load reafference (spec #15)

    /// How long the load sensilla take to adapt to a steady support force, in
    /// ms of neural time. INFERRED: campaniform/trichoid afferents are phasic
    /// and adapt quickly, and a value that is short relative to the time it
    /// takes the body to change load keeps the loop free of a self-sustaining
    /// tonic drive. `tools/mirror_regional_fixture.py` is the gate for that
    /// property, not for the exact number.
    public var reafferenceAdaptationTauMs: Double = 1.0
    /// Receptor gain: intensity per unit load deviation (load is expressed as a
    /// fraction of body weight). INFERRED. Published so the offline gate reads
    /// the SAME value the sim uses instead of keeping a copy that can drift.
    public var reafferenceGain: Float = 0.6
    /// Dead band below which a deviation is not signalled at all. Without it
    /// the contact solver's sub-microNewton resting ripple would be transduced
    /// into a permanent current. INFERRED.
    public var reafferenceDeadband: Float = 0.01
    /// Adaptive baseline of the load high-pass. Derived state, not an input:
    /// it is re-initialised from the first sample after a reset.
    private var loadBaseline: Float = 0
    private var loadBaselineInitialised = false
    /// The leg afferent the reafference arm injects into, resolved once from
    /// the cell-class labels (nil until the first step).
    private var touchAfferentNeuron: Int32?

    /// Whether the loaded connectome carries a motor-cell label, so the drive
    /// readout could select motor neurons instead of summing every neuron in
    /// the neuropil. False for assets written before the class byte existed
    /// (flags == 0 everywhere), where the readout falls back to region-only.
    /// Surfaced so the UI can say which of the two it is doing rather than
    /// implying the stronger claim either way.
    public private(set) var motorClassified: Bool = false

    /// Last readout's total, and the part of it that came from cells actually
    /// carrying the motor label. They are equal whenever the asset is read by
    /// class; they diverge only under the region-only fallback. Exposed for the
    /// readout tests, which need to see WHICH cells were summed rather than
    /// just that some number changed.
    var motorDriveTotalForTesting: Float = 0
    var motorDriveFromLabelledOnlyForTesting: Float = 0

    // Gustatory channel observation (see GustatoryPathwayTests). These report
    // what the sampling rule actually did this step — a test that cannot see
    // whether the channel fired cannot tell a working sense from a silent one.
    /// True when a taste sample was taken from either chemoreceptive site.
    public private(set) var tasteSampledForTesting = false
    /// The signed acceptance of the most recent sample (0 = none).
    public private(set) var lastTasteAcceptanceForTesting: Float = 0
    /// True when the proboscis was open far enough for the labellar route.
    public private(set) var proboscisReachedForTesting = false

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
        // ...and the ray caster, so the eye can see a body that is nearer than
        // the luminance sample range. Without it the eye reads the background
        // through the approaching body and no loom is ever produced.
        self.vision.rayProvider = { [weak self] origin, dir in
            self?.scene?.raycast(origin: origin, direction: dir)
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

        // 0) Advance world time and integrate moving bodies BEFORE the eye
        //    samples. Sampling first would compare two samples of the same
        //    instant, so the covered area would never change and no loom could
        //    ever be measured — the detector would be looking at a still frame.
        scene?.step(dtSeconds: dt / 1000)

        // 1) Sensory sampling (biological transduction, spec #9)
        let pose = FlyPose(forwardX: forward.x, forwardY: forward.y, forwardZ: forward.z,
                           upX: up.x, upY: up.y, upZ: up.z)
        let visualEvents = vision.sample(position: (position.x, position.y, position.z),
                                         pose: pose, dt: dt)

        // 2) Inject sensory inputs into connectome neurons
        var sensoryInputs: [SensoryInput] = []
        for ev in visualEvents {
            // A loom is not one more edge like any other: it is the escape
            // drive (spec #23), so it goes through the dedicated looming
            // channel to the lobula plate. Folding it into the generic
            // mapToInput path would scale it to 1/8 strength (strength×10 via
            // a strength/80 readout) and it would no longer be the threat
            // signal the giant-fibre route is wired for.
            if ev.pathway == .looming {
                sensoryInputs += sensory.loomingInput(intensity: ev.strength)
                continue
            }
            if let (target, current) = vision.mapToInput(event: ev, connectome: connectome) {
                sensoryInputs.append(SensoryInput(neuron: target, current: current,
                                                  modality: .vision, strength: abs(current) / 80))
            }
        }
        // olfaction (biological sampling, spec #13)
        if let w = world {
            let (oL, oR) = w.odorConcentration(atX: position.x, y: position.y, z: position.z)
            sensoryInputs += sensory.odorInput(concentrationL: oL, concentrationR: oR)

            // Taste is a CONTACT sense, so it is sampled at the proboscis TIP
            // and only when the proboscis is actually extended to the point of
            // contact. Sampling it at the body centre — or sampling it at all
            // while the proboscis is retracted — would let the fly "taste"
            // food it is standing a body-length away from, which is just a
            // second odor channel wearing a contact sensor's name.
            //
            // The reach gate reads the PROBOSCIS JOINT ANGLE that the motor
            // drive produced earlier in this same step, so the sense is
            // downstream of the nervous system, not a scripted proximity test.
            //
            // Tarsal taste is sampled FIRST and separately, because the two are
            // not the same sensor and only one of them can bootstrap the other.
            // The proboscis is driven by SEZ activity, and SEZ had no input
            // except labellar taste — so gating labellar taste on the proboscis
            // being open made the channel unable to ever open it. Drosophila
            // breaks exactly this circle with tarsal sensilla: the fly tastes
            // the substrate through its FEET, which is what extends the
            // proboscis, and the labellum then confirms. Sampling tarsal taste
            // at the ground contact point (gated on real stance, not on the
            // mouth) is what makes the first taste possible.
            let tarsalTaste = tasteAtTarsus(w)
            if tarsalTaste != 0 {
                sensoryInputs += sensory.gustatoryInput(acceptance: tarsalTaste)
                tasteSampledForTesting = true
                lastTasteAcceptanceForTesting = tarsalTaste
            }
            let labellarTaste = tasteAtProboscisTip(w)
            if labellarTaste != 0 {
                sensoryInputs += sensory.gustatoryInput(acceptance: labellarTaste)
                tasteSampledForTesting = true
                lastTasteAcceptanceForTesting = labellarTaste
            }
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

    /// Where the proboscis tip is in the world right now (mm).
    ///
    /// Built from the articulated body: the segment origin is fixed in
    /// body-frame, and the labellum extends along the local proboscis axis as
    /// the joint opens. The distance is a stated approximation of the extended
    /// proboscis (spec #18 allows a skeleton approximation here), but the
    /// DIRECTION and the dependence on the joint angle are real, which is what
    /// the reach gate needs.
    public var proboscisTipPosition: SIMD3<Float> {
        let headPos = body.head.position
        let angle = body.proboscis.angle
        // +x body axis is forward, so the proboscis points forward and down as
        // the joint opens; at full extension it reaches `proboscisLengthMm`.
        let out = SIMD3<Float>(cos(angle), -sin(angle), 0)
        let tip = headPos + out * (FlyBody.proboscisLengthMm * min(max(angle / 1.4, 0), 1))
        return dynamics.position + dynamics.rotate(tip)
    }

    /// Taste acceptance at the tarsus (feet on substrate).
    ///
    /// Flies carry gustatory sensilla on the tarsi and taste what they stand
    /// on; in *Drosophila* a tarsal sugar taste is itself enough to elicit
    /// proboscis extension. This is the input that lets the feeding loop start
    /// from nothing — it depends on stance, not on the proboscis the loop is
    /// trying to open.
    ///
    /// Sampled at the ground contact point, gated on `isGrounded`. The contact
    /// point is the body position projected down to the substrate; the leg
    /// tarsi are what carry the load the physics solver already integrates.
    private func tasteAtTarsus(_ w: WorldProvider) -> Float {
        guard dynamics.isGrounded else { return 0 }
        return w.tasteAcceptance(atX: position.x,
                                 y: dynamics.parameters.groundY,
                                 z: position.z)
    }

    /// Taste acceptance at the proboscis tip, gated by real contact.
    ///
    /// Both conditions are physical, neither is a decision: the tip must be
    /// within the substrate's reach (checked by the world, at the tip), and
    /// the proboscis must be open far enough to touch (`proboscisReachAngle`).
    /// A retracted proboscis cannot taste even if the tip position would
    /// overlap the source — the animal's mouth is not out.
    private func tasteAtProboscisTip(_ w: WorldProvider) -> Float {
        guard FlyBody.proboscisReaches(body.proboscis.angle) else { return 0 }
        proboscisReachedForTesting = true
        let tip = proboscisTipPosition
        return w.tasteAcceptance(atX: tip.x, y: tip.y, z: tip.z)
    }

    /// Read per-group motor drive from current VNC/SEZ activity (real spikes).
    private func readMotorDrive() {
        var drive = [Float](repeating: 0, count: 6)  // 6 legs
        var wing: Float = 0
        var sez: Float = 0
        // Drive comes from the leaky firing-rate estimate, NOT `recentSpikes`.
        // That counter is a tumbling inspection window: it reports 0 for most
        // of every window and then a lump, so the value depends on WHEN it is
        // sampled — and with the neural clock running ~42x slower than real
        // time (tools/measure_time_scale.py) a 1 s neural window is ~42 s of
        // wall time, i.e. the fly could not change its leg drive in under 40 s.
        // The leaky rate is sampleable at any instant and follows a
        // sensorimotor timescale (`motorRateTauMs`).
        //
        // Walking the firing active set keeps this O(active) rather than a
        // sweep of all 153,746 neurons per step.
        //
        // MOTOR CELLS ONLY. Region alone is not a motor label: on the real BANC
        // release the ingest puts 9,954 neurons in legNeuromere of which only 187
        // (1.9%) are motor — 4,770 of the rest (47.9%) are the sensory afferents that
        // report tarsal load and joint angle into that same neuromere, plus
        // local interneurons (tools/measure_motor_readout_composition.py).
        // Summing all of them makes the "motor command" a sum of the fly's own
        // sensory input, so it would track the stimulus instead of the motor
        // output and the closed loop would partly measure its own input.
        //
        // Coverage, measured on banc_cns.fbpack: 289 of the release's 805 motor
        // neurons fall in these three regions; the other 516 sit in
        // ventralNerveCord / abdominalNeuromere, which the readout does not
        // consult. So this reads a real subset of the motor pool, not all of
        // it, and `motorClassified` says only that the selection was by class.
        //
        // Assets written before the class byte existed carry flags == 0 for
        // every neuron. Falling back to region-only in that case keeps such an
        // asset running exactly as it did; it does not invent a motor label it
        // does not have.
        //
        // The mode is decided from the ASSET, not from which cells happen to be
        // firing this step. Reading it off `firingActiveNeurons` made the
        // readout flip to region-only on any frame where no labelled cell
        // fired, and it then summed the 4,770 sensory afferents sitting in
        // `legNeuromere` — an intermittent short circuit that fires MORE often
        // the quieter the fly is, which is backwards for a motor command.
        let classifyByRegion = !connectome.hasCellClasses
        var motorDriveTotal: Float = 0
        var motorDriveFromLabel: Float = 0
        for idx in engine.firingActiveNeurons {
            let rate = engine.rateHz(of: idx)
            guard rate > 0 else { continue }
            let n = connectome.neurons[Int(idx)]
            let region = RegionID(rawValue: Int(n.region)) ?? .unknown
            switch region {
            case .legNeuromere, .wingNeuropil, .subesophagealZone:
                if !classifyByRegion && !n.isMotorNeuron { continue }
            default:
                continue
            }
            motorDriveTotal += rate
            if n.isMotorNeuron { motorDriveFromLabel += rate }
            switch region {
            case .legNeuromere:
                let slot = (n.side == 1 ? 0 : 1) * 3 + (Int(n.type) % 3)
                if slot < drive.count { drive[slot] += rate }
            case .wingNeuropil:
                wing += rate
            case .subesophagealZone:
                sez += rate
            default:
                break
            }
        }
        motorDriveTotalForTesting = motorDriveTotal
        motorDriveFromLabelledOnlyForTesting = motorDriveFromLabel
        motorClassified = !classifyByRegion
        // The raw rate is in Hz and the CPG expects a normalised drive. There
        // were three competing implicit scales (0.25 per spike, a /6 and a /4
        // denominator) so the same activity meant different things per group.
        // One explicit conversion: a group at `driveReferenceHz` saturates.
        let reference = max(motorDriveReferenceHz, 1)
        let d = drive.map { min($0 / reference, 1) }
        neuralDrive = d
        motor.wingMuscleDrive = min(wing / reference, 1)
        motor.proboscisDrive = min(sez / reference, 1)
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
        // Load sensilla (campaniform/trichoid) signal CHANGES in leg load, not
        // the static load: a standing fly's sensors adapt to the constant
        // support force and stop firing. Injecting the ABSOLUTE load instead
        // made standing a permanent tonic drive, so the fly walked in a dark,
        // odourless world. CI's log for commit fe65764 shows the symptom: the
        // no-odour control moved 3.265094 mm on its own, and with odour the
        // same fixture moved only a little further (3.92 mm offline) because
        // the readout's leg drive was already pinned at saturation — the
        // stimulus was no longer the thing setting the behaviour.
        //
        // The fix is a rectifying high-pass with ZERO DC gain: only POSITIVE
        // deviation above the adapting baseline is signalled, so a steady load
        // yields no sustained drive. `tools/mirror_regional_fixture.py` gates
        // this: it reproduces the pre-fix cut-path number against Swift's CI
        // log, then asserts that a load held constant produces no signal, that
        // a load STEP still does, and that the no-odour/cut-path controls go
        // quiet while the odour run stays loud.
        //
        // NOTE this is a MODEL choice, not a measured receptor transfer
        // function: the adaptation time constant and gain below are INFERRED.
        let load = dynamics.groundLoadFraction        // 0..1 from contact solver
        if !loadBaselineInitialised {
            loadBaseline = load
            loadBaselineInitialised = true
        }
        // The baseline adapts to the RAW load even while airborne, so that the
        // moment the tarsi touch down is reported as a positive step rather than
        // being absorbed by a baseline that had frozen at zero.
        loadBaseline += (load - loadBaseline)
            * Float(min(1, dt / reafferenceAdaptationTauMs))
        // A tarsal sensillum can only report load while the tarsus is on the
        // substrate. Gating on the real contact flag keeps the channel from
        // signalling during flight, where there is no load to report — and it
        // matters offline too: it is what stops the channel from becoming a
        // second drive path when a synthetic fixture fires its motor pool
        // without the body ever taking a step.
        let deviation = Float(load) - loadBaseline
        let intensity = deviation * reafferenceGain
        if dynamics.isGrounded, intensity > reafferenceDeadband {
            // Resolve the afferent ONCE (it walks the region index) and pick a
            // cell that is actually labelled sensory. `touchInput(side:)` would
            // otherwise take the region's first neuron, and in the fixtures that
            // neuron is simultaneously the target of this current AND a cell the
            // motor readout sums — the "sensory" arm then enters the readout
            // directly and bypasses the connectome. Measured offline
            // (`tools/mirror_regional_fixture.py`): with the region fallback the
            // current lands on a neuron the readout sums, and switching to the
            // class-resolved afferent removes that overlap entirely.
            if touchAfferentNeuron == nil {
                touchAfferentNeuron = sensory.touchAfferent(side: 1)
            }
            if let n = touchAfferentNeuron {
                for t in sensory.touchInput(neuron: n, intensity: intensity) {
                    engine.injectCurrent(into: t.neuron, current: t.current,
                                         at: engine.currentTimeMs)
                }
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
        // Wing strain (campaniform sensilla at the wing base) whenever the wings
        // are actually beating. The load these report is set by the wing's
        // MOTION, so the intensity comes from the measured tip speed — the
        // command would make this channel a copy of the efference, i.e.
        // feedforward dressed as sensory feedback, and it would be a constant
        // for as long as the wings were told to beat.
        //
        // The strain the campaniform fields report scales with the square of
        // the wing velocity, so the normalisation is a reference TIP SPEED, not
        // a commanded frequency. `strokeResponseRef` is that reference: the RMS
        // tip speed at the calibrated maximum stroke, which both implementations
        // compute as 2·π·180 Hz·0.9 rad·2.5 mm/√2 = 1799.4 mm/s
        // (`tools/verify_body_physics.py` asserts it, and the published range is
        // ~2.0-2.5 m/s for a 1.4 rad stroke at ~180 Hz — this model's stroke is
        // a little slower and the gate reports it rather than hiding it).
        //
        // What matters for the loop is the CONSEQUENCE of the choice: at the
        // calibrated stroke the channel carries 1799.4/1799.4 = 1.0 of the
        // reference, so the sensillum's reading is a graded fraction of the
        // normal beat and it saturates only if a future stroke exceeds what the
        // physics was calibrated against. The 0.3 ceiling is INFERRED and
        // matches the strength the previous version applied at full drive, so
        // nothing else re-tunes.
        //
        // The reference is named rather than inlined because the number IS the
        // physics: `tools/verify_body_physics.py` asserts the calibrated tip
        // speed equals it, so the two cannot drift apart silently.
        let strainRef = max(dynamics.measuredWingTipSpeed, 0)
        if strainRef > 0 {
            let strokeResponseRefMmPerSec: Float = 1799.4
            let strain = min(strainRef / strokeResponseRefMmPerSec, 1) * 0.3
            for m in sensory.wingStrainInput(intensity: strain) {
                engine.injectCurrent(into: m.neuron, current: m.current,
                                     at: engine.currentTimeMs)
            }
        }
    }

    /// Energetic cost of activity (0..1) — placeholder for body work.
    public var activityLevel: Float {
        // Healthy proxy: leg/wing neurons firing right now. Uses the leaky
        // firing active set for the same reason the motor readout does — this
        // is consumed once per step, and the tumbling recent-spike window both
        // lags by up to its full length and reports 0 for most of it.
        var active = 0
        for idx in engine.firingActiveNeurons {
            guard engine.rateHz(of: idx) > 0 else { continue }
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

        // The wings are not beating where the old pose left them, and the
        // actuator state is derived from the command anyway.
        dynamics.resetWingActuatorState()

        // The load high-pass holds a baseline measured at the OLD pose, and the
        // afferent was resolved against whatever connectome existed when it was
        // first needed. Both are derived state tied to where the fly is, so a
        // teleport re-derives them; otherwise the first steps after a spawn
        // would signal the load step of the teleport itself as tactile input.
        loadBaselineInitialised = false
        touchAfferentNeuron = nil
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
        // Walk the firing active set, not all neurons: only it can have a
        // non-zero rate, the sweep was O(153,746) per observe(), and the
        // tumbling recent-spike window would report 0 for most of every window.
        for idx in core.engine.firingActiveNeurons {
            guard core.engine.rateHz(of: idx) > 0 else { continue }
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