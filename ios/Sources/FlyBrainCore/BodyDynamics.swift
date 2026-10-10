//
//  BodyDynamics.swift
//  FlyBrainCore
//
//  Phase 6/7 — real rigid-body integration for the fly (spec #18, #20, #30).
//
//  This file contains NO behaviour, NO thresholds and NO "modes". It consumes
//  motor output (real forces/torques produced from neural activity upstream)
//  and produces body motion. If the leg or wing neuropils are silent, this
//  code produces nothing — the fly does not walk or fly "because a mode says
//  so". That is what makes the BODY → WORLD arrow of the closed loop real.
//
//  UNITS (metric, scaled for float precision on a phone):
//    length  mm
//    mass    mg
//    time    s
//    force   nN      (1 mg·mm/s² = 1e-9 N = 1 nN)
//  A 1 mg fly therefore weighs 9806 nN ≈ 9.8 µN, which is the right order
//  (published Drosophila mass ≈ 1 mg).
//

import Foundation

/// Physical constants and coefficients for the body model.
/// Every entry is a documented approximation: see docs/PHYSICS.md for the
/// ledger and how each number was chosen. Nothing here is a measured value
/// unless the comment says MEASURED.
public struct PhysicsParameters: Sendable {
    /// Body mass (mg). APPROXIMATED — matches the segment masses in
    /// BodyModel (head 0.08 + thorax 0.40 + abdomen 0.30); a well-fed adult
    /// female weighs ~1.0-1.2 mg, so this is on the lean side.
    public var bodyMassMg: Float = 0.78
    /// Gravitational acceleration in mm/s². MEASURED (9.80665 m/s²).
    public var gravityMmS2: Float = 9806.65
    /// Height of the body origin (thorax centre) above the substrate while
    /// standing (mm). APPROXIMATED from leg segment lengths. This is the
    /// UNLOADED body height: standing legs hold the thorax here, and the
    /// contact penalty then lets it settle to `restHeightMm` under its own
    /// weight.
    public var standHeightMm: Float = 0.8

    /// The height at which the contact penalty spring exactly carries the
    /// body's weight, i.e. the body origin's settled rest height (mm).
    ///
    /// This is NOT a second free parameter — it is the force balance of the
    /// two constants above and below it:
    ///
    ///     normalForce = contactStiffness * penetration  (at rest, no damping)
    ///     weight      = bodyMassMg * gravityMmS2
    ///     restHeight  = groundY + standHeightMm - weight / contactStiffness
    ///
    /// It exists because `setPose` dropped the body at the caller's RAW y, and
    /// every caller writes a y at or below the balance height:
    ///
    ///   * `groundY + standHeightMm` (the tests) is the UNLOADED height, so the
    ///     body then sags to equilibrium and injects for 36 steps;
    ///   * `y = 0.2` (the app) and `y = 0` (the silence test) are 0.6-0.78 mm
    ///     INTO the floor — a 33-42 body-weight launch that injects for
    ///     118-67 steps.
    ///
    /// Measured (`tools/probe_rest_drive.py`). The load is overdamped (damping
    /// 2500 > critical 1117), so the launch decays in place rather than
    /// throwing the fly airborne — the load spike is the whole defect.
    ///
    /// The behaviour that leaks out of that transient is not the fly's, it is
    /// the spawn's — and `testTheConnectomeIsSilentWithNoStimulus` was reading
    /// it as a stimulus in a world that has none.
    ///
    /// Seeding the flight-relevant dynamic state at this height makes the
    /// spawn a pose rather than an impact, so the first step is as quiet as
    /// any later one. The unloaded `standHeightMm` is still the reference the
    /// proboscis geometry and the tarsal clamp are written against; only the
    /// spawn uses the balance height.
    public var restHeightMm: Float {
        groundY + standHeightMm - (bodyMassMg * gravityMmS2) / max(contactStiffness, 1)
    }

    /// Overall body length, head to abdomen tip (mm). MEASURED order: an adult
    /// Drosophila is ≈ 2.5 mm long. Used as the contact extent when the fly
    /// meets a solid obstacle, so it cannot intersect geometry deeper than its
    /// own size.
    public var bodyLengthMm: Float = 2.5

    /// Time constant (ms) with which the wings reach a commanded stroke.
    /// INFERRED. Muscle is slower than nerve, so the realised stroke trails the
    /// command over tens of milliseconds. It is NOT calibrated against a
    /// measured wing-step response; it exists so that the wing's motion and the
    /// motor command are distinguishable, which is what lets a strain receptor
    /// on the wing measure something other than an efference copy.
    public var strokeActuatorTauMs: Float = 15

    /// Traction gain of the planted legs: force per unit of speed error
    /// (nN per mm/s). APPROXIMATED. The legs are modelled as a
    /// traction-limited force source, NOT as an articulated chain: the tarsus
    /// pushes the substrate and can only transmit force up to
    /// `legMaxForceNn` before it slips. Kept modest relative to the cap so
    /// that load actually slows the fly down (a rigid velocity servo would
    /// make the body insensitive to obstacles, which is unphysical).
    public var legTractionGain: Float = 800
    /// Maximum propulsive force ONE leg can transmit while planted (nN).
    /// APPROXIMATED: ~2.5 µN ≈ a third of body weight per leg, so a tripod
    /// transmits about one body weight — the *propulsive* fraction of the
    /// published static single-leg load capacity (which is several body
    /// weights but not sustainable as continuous thrust).
    public var legMaxForceNn: Float = 2500
    /// Ground friction coefficient applied ONLY when the legs are not driving
    /// (to stop a coasting fly). REPORTED fly tarsal friction is roughly
    /// 0.5-1.0 — used as APPROXIMATED here because our contact is a penalty
    /// model, not a rigid constraint.
    public var groundFriction: Float = 0.6

    /// Linear aerodynamic drag on the body (nN per mm/s). APPROXIMATED and
    /// deliberately small: at walking speeds (tens of mm/s) air drag on a
    /// fly-sized body is negligible compared with leg force.
    public var linearDragNnPerMmS: Float = 6
    /// Angular drag (nN·mm per rad/s), from wing/haltere damping when idle.
    /// APPROXIMATED.
    public var angularDrag: Float = 60

    /// Ground contact: penalty spring-damper (nN per mm, nN per mm/s).
    /// APPROXIMATED — elastic contact, never rigid. Chosen stiff enough that
    /// penetration stays well below a leg length.
    public var contactStiffness: Float = 400000
    public var contactDamping: Float = 2500

    // MARK: Wings (reduced-order quasi-steady aerodynamics)
    /// Wing length (mm) — the moment arm of the stroke. MEASURED order:
    /// a Drosophila wing measures ≈ 2.5 mm root to tip.
    public var wingLengthMm: Float = 2.5
    /// Area of ONE wing (mm²). MEASURED order: ≈ 2.5 mm long by ~1 mm
    /// mean chord, so 2.5-3 mm².
    public var wingAreaMm2: Float = 2.6
    /// Air density (mg/mm³) = 1.2 kg/m³. MEASURED physical constant.
    public var airDensityMgPerMm3: Float = 1.2e-3
    /// Effective lift coefficient for THIS reduced-order model.
    /// APPROXIMATED, and explicitly NOT a measured aerodynamic coefficient:
    /// real flapping-flight lift relies on an unsteady leading-edge vortex
    /// that a quasi-steady blade-element model cannot produce, so this number
    /// cannot be stated as a lift coefficient. It is instead CALIBRATED so
    /// that a full-power stroke train produces ≈1.44x body weight — the
    /// margin above the ~1.3x needed to lift off — so flight becomes possible
    /// when, and only when, the wing neuropil drives the wings. See
    /// docs/PHYSICS.md (calibration) and testFlightLiftExceedsWeightButOnly
    /// WhenDriven in BodyDynamicsTests.
    public var effectiveLiftCoefficient: Float = 1.1
    /// Drag coefficient of the same quasi-steady model. APPROXIMATED; it sets
    /// the thrust / forward authority of the wing stroke.
    public var effectiveDragCoefficient: Float = 0.5
    /// Energy-free angular stabilisation gain from the halteres (spec #16):
    /// multiplies the angular drag in proportion to haltere drive, modelling
    /// the Coriolis-force feedback loop without pretending to simulate it.
    /// APPROXIMATED.
    public var haltereStabilisationGain: Float = 6

    public init() {}

    /// Weight of the fly (nN).
    public var weightNn: Float { bodyMassMg * gravityMmS2 }
    /// Ground plane height (mm).
    public var groundY: Float = 0
}

/// Full rigid-body state of the fly (single torso + massless actuators).
/// Quaternion order is (x, y, z, w).
public struct RigidBody: Sendable {
    /// Body origin (thorax centre) in world coordinates. Y-UP world. The default
    /// here is only a placeholder — `BodyDynamics.init` places the body on the
    /// substrate at the standing height, and `teleport` / the app's spawn call
    /// set the real value. A non-zero z here was a leftover z-up assumption.
    public var position: SIMD3<Float> = SIMD3(0, 0, 0)
    public var velocity: SIMD3<Float> = SIMD3(0, 0, 0)
    /// Orientation as a unit quaternion (x, y, z, w); level, facing +x,
    /// dorsal (body +y) aligned with world up. Y-up world — see `dorsal`.
    public var orientation: SIMD4<Float> = SIMD4(0, 0, 0, 1)
    /// Angular velocity in the WORLD frame (rad/s).
    public var angularVelocity: SIMD3<Float> = SIMD3(0, 0, 0)
    /// True while the tarsus plane is in contact with the substrate.
    public var grounded: Bool = true
    /// Penetration depth of the contact spring this step (mm, diagnostic).
    public var groundPenetration: Float = 0
    /// Magnitude of the contact normal force (nN, diagnostic — this is the
    /// real load used for friction, and is exposed as tactile feedback).
    public var normalForce: Float = 0

    public init() {}

    /// Body-axis basis in WORLD coordinates.
    ///
    /// Convention: the world — and this body — are Y-UP (World.swift clamps
    /// `p.y >= 0` for the substrate, the app spawns the fly at y=0.2 and the
    /// light/obstacle placements sit above y=0). The body's own axes are
    /// +x forward, +y dorsal, +z right, so the default orientation
    /// (SIMD4(0,0,0,1), level, facing +x) yields exactly those world vectors.
    public var forward: SIMD3<Float> { rotate(SIMD3(1, 0, 0)) }
    /// Body right (+z body axis) in world coordinates.
    public var lateral: SIMD3<Float> { rotate(SIMD3(0, 0, 1)) }
    /// Body dorsal (+y body axis) in world coordinates — points at the sky.
    public var dorsal: SIMD3<Float> { rotate(SIMD3(0, 1, 0)) }

    /// Yaw angle about the world vertical (radians, atan2 convention).
    /// Horizontal plane is x-z, so yaw is measured there.
    public var yaw: Float {
        let f = forward
        return atan2(f.z, f.x)
    }

    /// Ground speed (mm/s): horizontal (x-z) speed while the substrate is the
    /// y = groundY plane.
    public var groundSpeed: Float {
        (velocity.x * velocity.x + velocity.z * velocity.z).squareRoot()
    }

    /// Rotate a body-frame vector into the world frame.
    public func rotate(_ v: SIMD3<Float>) -> SIMD3<Float> {
        let q = orientation
        let u = SIMD3(q.x, q.y, q.z)
        let s = q.w
        let uv = FlyMath.cross(u, v)
        let uuv = FlyMath.cross(u, uv)
        return v + 2 * (s * uv + uuv)
    }
}

/// Motor command handed to the body. Every field is produced from neural
/// activity (muscle/actuator state) — nothing here decides behaviour.
public struct BodyMotorCommand: Sendable {
    // MARK: Legs
    /// Speed the planted legs are trying to drive the body to, along the body
    /// forward axis (mm/s, signed: negative = walking backwards). This is the
    /// stride speed implied by leg joint angular velocity and leg length.
    public var forwardSpeedTarget: Float = 0
    /// Lateral stride speed target (mm/s, signed: + = towards body +y).
    /// Produced by left/right leg phase asymmetry.
    public var lateralSpeedTarget: Float = 0
    /// Fraction of legs reporting substrate contact (0..1); scales how much
    /// force the legs can transmit. An airborne fly transmits nothing.
    public var legContactFraction: Float = 1
    /// True when any leg has ground contact (proprioceptive reflex gating).
    public var legsInContact: Bool = true

    // MARK: Wings
    /// Wing stroke frequency (Hz) produced by the wing neuropil drive.
    public var wingStrokeFrequency: Float = 0
    /// Stroke semi-amplitude (radians) — measured stroke amplitude is about
    /// ±70-90°, i.e. ≈1.3-1.6 rad.
    public var wingStrokeAmplitude: Float = 0
    /// Left/right stroke amplitude imbalance (-1..1, + = left stroke larger),
    /// which produces roll and yaw.
    public var wingAsymmetry: Float = 0
    /// Haltere drive (0..1) — scales the stabilising angular damping.
    public var haltereDrive: Float = 0
    /// Pitch command from the wing/abdomen musculature (-1..1).
    public var pitchBias: Float = 0

    public init() {}
}

/// Integrates the fly body under motor commands, gravity, drag and contact.
/// Deterministic: no randomness, no wall-clock time, no UI, no policy.
public struct BodyDynamics: Sendable {
    public var parameters: PhysicsParameters
    public var body: RigidBody

    /// Mean wing-tip speed of the last step (mm/s) — real derived value, used
    /// by the UI telemetry and by tests (never a decorative number).
    public private(set) var lastWingTipSpeed: Float = 0
    /// Aerodynamic force produced this step (nN) — diagnostic.
    public private(set) var lastLiftNn: Float = 0

    public init(parameters: PhysicsParameters = PhysicsParameters()) {
        self.parameters = parameters
        self.body = RigidBody()
        // Y-UP world: height above the substrate is the Y component. Writing
        // the standing height into z (as this used to) left y = 0, so the fly
        // spawned buried in the ground plane and the contact solver jettisoned
        // it on the first step.
        //
        // The height itself is `restHeightMm`, not `standHeightMm`: the leg
        // model holds the thorax at the unloaded stand height, but the penalty
        // contact has to compress by weight/stiffness before it can carry the
        // weight. Starting at the unloaded height means starting 0.78 mm into
        // the substrate — a 42-body-weight launch load that drives the tarsal
        // channel for 67 steps before the spring settles.
        self.body.position = SIMD3(0, parameters.restHeightMm, 0)
    }

    /// Position of the body centre (mm).
    public var position: SIMD3<Float> { body.position }
    /// Body forward (+x body axis) in world coordinates.
    public var forward: SIMD3<Float> { body.forward }
    /// Body dorsal (+y body axis) in world coordinates — points at the sky.
    public var up: SIMD3<Float> { body.dorsal }
    /// Tarsus plane is in contact with the substrate.
    public var isGrounded: Bool { body.grounded }
    /// Contact load as a fraction of body weight (0..1+). Frame-accurate
    /// measurement of the contact normal force — used for tactile reafference.
    public var groundLoadFraction: Float {
        body.normalForce / max(parameters.bodyMassMg * parameters.gravityMmS2, 1)
    }
    /// Magnitude of the angular velocity (deg/s) — what the halteres sense.
    public var angularRateMagnitude: Float {
        FlyMath.length(body.angularVelocity) * (180 / .pi)
    }
    /// SIGNED yaw rate about the world vertical (deg/s). The haltere organ
    /// distinguishes left from right turns, so the stabilising pathway needs
    /// the sign, not just the magnitude. Yaw axis is +y in this y-up world.
    public var yawRateDegPerS: Float {
        body.angularVelocity.y * (180 / .pi)
    }
    /// Simulation time of the body clock (ms).
    public var simulationTimeMs: Float { elapsedMs }
    /// Body orientation as a quaternion (x, y, z, w) for rendering.
    public var bodyQuaternion: SIMD4<Float> { body.orientation }

    // MARK: Wing stroke actuator — the wing is not the command

    /// What the wings are ACTUALLY doing, which is not what they were told to
    /// do: the actuator has a time constant, muscle cannot change stroke
    /// amplitude instantly, and during a manoeuvre the commanded and realised
    /// strokes differ for tens of ms. A wing-strain receptor measures the wing,
    /// so these are the fields a strain channel must read. Reading the motor
    /// command instead makes the "sensor" a copy of the efference — feedback
    /// wired out of the output rather than out of the physics. Measured tip
    /// speed is the strain proxy: the once-per-stroke load the campaniform
    /// fields at the wing base report scales with the square of the wing
    /// velocity, so their firing follows the tip speed.
    public private(set) var measuredStrokeAmplitude: Float = 0
    public private(set) var measuredStrokeFrequency: Float = 0
    public private(set) var measuredWingTipSpeed: Float = 0

    /// Drop the wings back to rest. Derived state tied to the pose, so a
    /// teleport re-derives it: the wings were not beating at the new place.
    public mutating func resetWingActuatorState() {
        measuredStrokeAmplitude = 0
        measuredStrokeFrequency = 0
        measuredWingTipSpeed = 0
        lastWingTipSpeed = 0
    }

    /// Simulation time accumulated by the body clock (ms). Advanced only in
    /// `step`, so it is the body's own time base, not the neural step count.
    public private(set) var elapsedMs: Float = 0

    /// Correct the body position after an external constraint (a solid
    /// obstacle) has been resolved. The solver cannot resolve scene geometry
    /// itself — it has no world — so the caller applies the correction here and
    /// the next step integrates from the constrained state.
    public mutating func setPosition(_ p: SIMD3<Float>) {
        body.position = p
    }

    /// Apply a momentum change from an external constraint. Used when an
    /// obstacle absorbs the component of velocity that pointed into it, so the
    /// body cannot keep its speed while being held still (free energy).
    public mutating func setVelocity(_ v: SIMD3<Float>) {
        body.velocity = v
    }

    /// Place the body at a pose (spawn / teleport). The orientation is built
    /// from the requested forward and dorsal (up) directions using an
    /// orthonormal basis, so the body is never left in a non-rigid frame:
    /// f = forward, r = normalise(f x up) (body right), d = r x f (body dorsal,
    /// re-orthogonalised against f). Y-up world — see `dorsal`.
    public mutating func teleport(position: SIMD3<Float>,
                                  forward: SIMD3<Float>,
                                  up: SIMD3<Float>) {
        var f = SIMD3(forward.x, forward.y, forward.z)
        var u = SIMD3(up.x, up.y, up.z)
        if FlyMath.length(f) < 1e-6 { f = SIMD3(1, 0, 0) }
        if FlyMath.length(u) < 1e-6 { u = SIMD3(0, 1, 0) }
        f = FlyMath.normalize(f)
        u = FlyMath.normalize(u)
        // Body right (+z) = f x u, body dorsal (+y) = right x f.
        let right = FlyMath.normalize(FlyMath.cross(f, u))
        let dorsal = FlyMath.normalize(FlyMath.cross(right, f))
        // A spawn is not an impact. `setPose` passes the position callers
        // *mean* — "on the substrate" is written as `groundY + standHeightMm`,
        // and the app writes an origin-ish `y = 0.2`. Both are at or below the
        // height the contact spring balances at, so taking them literally drops
        // the body into the floor and the solver launches it. Measured
        // (`tools/probe_rest_drive.py`): at `standHeightMm` the tarsal channel
        // injects for 36 steps, at y=0 for 67, at the app's y=0.2 for 118 —
        // and each of those is a transient the spawn created, not the fly.
        // The height a spawn stands at is the force-balance height; calling it
        // anything else leaves the transient to masquerade as stimulus.
        var spawnPosition = position
        if spawnPosition.y <= parameters.groundY + parameters.standHeightMm {
            spawnPosition.y = parameters.restHeightMm
        }
        body.position = spawnPosition
        body.orientation = BodyDynamics.quaternion(fromColumnX: f,
                                                   columnY: dorsal,
                                                   columnZ: right)
        body.velocity = SIMD3(0, 0, 0)
        body.angularVelocity = SIMD3(0, 0, 0)
        // A body at the balance height is IN CONTACT: the leg model holds the
        // thorax at `standHeightMm`, which is above it. `grounded` is derived
        // from the height the body was actually placed at, not from the
        // caller's raw y — deriving it from a buried y would disagree with the
        // solver that agrees it is penetrating — and not from `normalForce`,
        // which the solver has not computed yet on the spawn step.
        body.grounded = spawnPosition.y <= parameters.groundY + parameters.standHeightMm
        // Derived actuator state belongs to the pose it was measured at. A
        // teleport is a spawn, and carrying the stroke of the OLD flight over
        // would make the first frames of a spawned fly report a wing beat that
        // never happened — and the wing-strain channel feeds that back into the
        // connectome, so the fly would sense its own previous life. `setPose`
        // in `SimulationCore` reaches the physics through here, so this is also
        // what makes a spawned pose take effect instead of being overwritten by
        // the next integration step.
        measuredStrokeAmplitude = 0
        measuredStrokeFrequency = 0
        measuredWingTipSpeed = 0
    }

    /// Quaternion of the rotation whose body axes map onto the given world
    /// vectors (the rotation matrix is [x y z] columns). Uses the standard
    /// trace method, with the branch on the largest diagonal entry for
    /// numerical stability.
    static func quaternion(fromColumnX x: SIMD3<Float>,
                           columnY y: SIMD3<Float>,
                           columnZ z: SIMD3<Float>) -> SIMD4<Float> {
        let m00 = x.x, m10 = x.y, m20 = x.z
        let m01 = y.x, m11 = y.y, m21 = y.z
        let m02 = z.x, m12 = z.y, m22 = z.z
        let trace = m00 + m11 + m22
        if trace > 0 {
            let s = (trace + 1).squareRoot() * 2      // s = 4w
            return SIMD4((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
        } else if m00 > m11 && m00 > m22 {
            let s = (1 + m00 - m11 - m22).squareRoot() * 2   // s = 4x
            return SIMD4(0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
        } else if m11 > m22 {
            let s = (1 + m11 - m00 - m22).squareRoot() * 2   // s = 4y
            return SIMD4((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
        } else {
            let s = (1 + m22 - m00 - m11).squareRoot() * 2   // s = 4z
            return SIMD4((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
        }
    }

    // MARK: - Aerodynamics (reduced-order, quasi-steady)

    /// RMS wing-tip speed of a sinusoidal stroke (mm/s).
    ///
    /// For θ(t) = A·sin(2πft) the tip speed is 2πfA L·|cos(2πft)|, whose peak
    /// is 2πfAL and whose RMS is √2·π·fAL. The RMS — the second moment, not the
    /// mean absolute speed — is the number the quasi-steady force law needs,
    /// because F = ½ρv²SC is quadratic: averaging v first would under-count the
    /// force. With A = 0.9 rad, f = 180 Hz, L = 2.5 mm this gives ≈1.80 m/s,
    /// the right order for the measured ~2-2.5 m/s Drosophila tip speed.
    public static func wingTipSpeed(amplitude: Float, frequencyHz: Float,
                                    wingLengthMm: Float) -> Float {
        let twoPi = 2 * Float.pi
        return twoPi * abs(frequencyHz) * abs(amplitude) * wingLengthMm
            / Float(2).squareRoot()
    }

    /// Aerodynamic force of one wing (nN) = ½·ρ·v²·S·C.
    static func wingForce(speed: Float, areaMm2: Float, coeff: Float,
                          density: Float) -> Float {
        0.5 * density * speed * speed * areaMm2 * coeff
    }

    // MARK: - Integration

    /// Advance one step. `dt` in seconds. Returns net world acceleration
    /// (mm/s², diagnostics only).
    @discardableResult
    public mutating func step(command: BodyMotorCommand, dt: Float) -> SIMD3<Float> {
        guard dt > 0 else { return .zero }
        let p = parameters
        let fwd = body.forward
        let lat = body.lateral
        let dor = body.dorsal
        let mass = p.bodyMassMg
        let legsPlanted = command.legsInContact && command.legContactFraction > 0

        var force = SIMD3<Float>(0, 0, 0)

        // ---- 1. Legs: traction-limited force source ----------------------
        // The legs try to reach a stride speed; force is proportional to the
        // speed error and clipped at the value a planted tarsus can transmit.
        // Silently produce zero when no leg has contact: an airborne fly has
        // nothing to push against.
        if legsPlanted {
            let vFwd = FlyMath.dot(body.velocity, fwd)
            let vLat = FlyMath.dot(body.velocity, lat)
            let contact = min(max(command.legContactFraction, 0), 1)
            let maxF = p.legMaxForceNn * 3 * contact          // tripod: 3 legs
            // Braking is allowed (flies brake with their legs), so the force
            // is clamped symmetrically.
            let fLong = clamp(p.legTractionGain * (command.forwardSpeedTarget - vFwd),
                              -maxF, maxF)
            let fSide = clamp(p.legTractionGain * (command.lateralSpeedTarget - vLat),
                              -maxF, maxF)
            force += fwd * fLong + lat * fSide
        } else {
            // No contact: legs cannot push, and we fall.
        }

        // ---- 2. Wings: lift/drag from actual stroke kinematics -----------
        // The airborne force uses the MEASURED stroke, not the commanded one:
        // the wings cannot snap to a new amplitude, so a manoeuvre command
        // takes effect over `strokeActuatorTauMs` instead of in one step. This
        // is also the state the wing-strain channel reads, so that channel
        // measures the wing rather than eavesdropping on the motor command.
        let alpha = min(1, dt * 1000 / max(p.strokeActuatorTauMs, 1e-3))
        measuredStrokeAmplitude += (command.wingStrokeAmplitude - measuredStrokeAmplitude) * alpha
        measuredStrokeFrequency += (command.wingStrokeFrequency - measuredStrokeFrequency) * alpha
        // Stroke frequency and amplitude cannot be negative; a body told to
        // beat backwards is still beating.
        measuredStrokeAmplitude = max(0, measuredStrokeAmplitude)
        measuredStrokeFrequency = max(0, measuredStrokeFrequency)
        measuredWingTipSpeed = BodyDynamics.wingTipSpeed(
            amplitude: measuredStrokeAmplitude,
            frequencyHz: measuredStrokeFrequency,
            wingLengthMm: p.wingLengthMm)

        let tipSpeed = measuredWingTipSpeed
        lastWingTipSpeed = tipSpeed
        var lift: Float = 0
        var thrust: Float = 0
        var rollTorque: Float = 0
        var yawTorque: Float = 0
        if tipSpeed > 0 {
            // Faster stroke => more force per wing. Asymmetry changes each
            // wing's stroke velocity, and since force goes as v² the effect
            // is genuinely quadratic: a 10% stroke difference gives ~21%
            // force difference, which is what rolls and yaws the fly.
            let asym = min(max(command.wingAsymmetry, -1), 1)
            let vLeft = tipSpeed * (1 + 0.5 * asym)
            let vRight = tipSpeed * (1 - 0.5 * asym)
            let fLeft = BodyDynamics.wingForce(speed: vLeft, areaMm2: p.wingAreaMm2,
                                               coeff: p.effectiveLiftCoefficient,
                                               density: p.airDensityMgPerMm3)
            let fRight = BodyDynamics.wingForce(speed: vRight, areaMm2: p.wingAreaMm2,
                                                coeff: p.effectiveLiftCoefficient,
                                                density: p.airDensityMgPerMm3)
            lift = fLeft + fRight
            // Flapping drag tilted forward gives thrust; pitch bias tilts the
            // stroke plane and trades lift for thrust.
            let dragOne = BodyDynamics.wingForce(speed: tipSpeed,
                                                 areaMm2: p.wingAreaMm2,
                                                 coeff: p.effectiveDragCoefficient,
                                                 density: p.airDensityMgPerMm3)
            thrust = (dragOne * 2) * max(0.2, 1 - abs(command.pitchBias)) * 0.35
            let arm = p.wingLengthMm * 0.6
            rollTorque = (fLeft - fRight) * arm
            yawTorque = (fLeft - fRight) * arm * 0.4
            lift *= max(0.25, 1 - abs(command.pitchBias) * 0.6)
        }
        lastLiftNn = lift
        force += dor * lift + fwd * thrust

        // ---- 3. Weight ---------------------------------------------------
        // Gravity acts down the world vertical, which is -y (y-up world).
        force += SIMD3(0, -p.gravityMmS2 * mass, 0)

        // ---- 4. Body drag -------------------------------------------------
        force += -p.linearDragNnPerMmS * body.velocity

        // ---- 5. Integrate linear motion ----------------------------------
        var accel = force / mass
        let maxAccel: Float = 200000     // mm/s² — envelope guard, not a policy
        accel = SIMD3(min(max(accel.x, -maxAccel), maxAccel),
                      min(max(accel.y, -maxAccel), maxAccel),
                      min(max(accel.z, -maxAccel), maxAccel))
        body.velocity += accel * dt
        var position = body.position + body.velocity * dt

        // ---- 6. Ground contact (penalty spring-damper) --------------------
        // Penalty model, honestly: the substrate is COMPLIANT, so the body is
        // allowed to penetrate it and the contact force is the elastic response
        // to that penetration. Clamping the position back onto the surface
        // would delete the very quantity the normal force is computed from (and
        // would pump energy in on every step). What the ground does to the fly
        // is therefore only ever a FORCE, applied to the vertical velocity here.
        let contactLevel = p.groundY + p.standHeightMm
        let penetration = contactLevel - position.y
        if penetration > 0 {
            body.groundPenetration = penetration
            // Unilateral: a substrate can only push, never pull, so the normal
            // force is clamped at zero (no adhesion, no sticking).
            let elastic = p.contactStiffness * penetration
            let damping = p.contactDamping * body.velocity.y
            body.normalForce = max(0, elastic - damping)
            // The contact force changes momentum unconditionally — this is the
            // ONLY path by which the ground acts on the body. (The previous
            // version applied it only while already sinking, so a resting fly
            // received no support force at all and simply fell through.)
            body.velocity.y += (body.normalForce / mass) * dt
            body.grounded = true
            // Friction applies only while no leg is planted to brake with: when the
            // legs push, the propulsion term already models tarsal traction,
            // and subtracting another mu*N would double-count the same force.
            if !legsPlanted {
                let mu = p.groundFriction
                let decel = mu * p.gravityMmS2 * dt
                let horizSpeed = body.groundSpeed
                if horizSpeed > 0 {
                    let scale = max(0, horizSpeed - decel) / horizSpeed
                    body.velocity.x *= scale
                    body.velocity.z *= scale
                }
            }
        } else {
            body.grounded = false
            body.groundPenetration = 0
            body.normalForce = 0
        }
        body.position = position

        // ---- 7. Angular motion -------------------------------------------
        // Moment axes in a Y-UP world: roll is about the body long axis (world x
        // while flying level), yaw is about the world vertical (y), and the
        // pitch couple from the stroke-plane tilt acts about the body's right
        // axis (z). Mixing these is what silently turns yaw into pitch.
        var torque = SIMD3<Float>(rollTorque,
                                  yawTorque,
                                  command.pitchBias * lift * p.wingLengthMm)
        // Halteres: real inertial feedback, here as a rate damping whose gain
        // comes from the haltere neuropil drive (spec #16).
        let stability = 1 + p.haltereStabilisationGain * min(max(command.haltereDrive, 0), 1)
        torque += -p.angularDrag * stability * body.angularVelocity
        // Angular inertia (mg*mm^2) about the body's roll / yaw / pitch
        // axes: rolls about the long axis are the cheapest, so I_xx is the
        // smallest. APPROXIMATED from the segment inertias in BodyModel.
        let inertia = SIMD3<Float>(0.05, 0.12, 0.12)
        // Rotational Newton-Euler: the inertia tensor is BODY-frame, so the
        // moment is brought into the body frame, scaled, and the resulting
        // angular acceleration rotated back into the world frame before it is
        // integrated into the world-frame angular velocity.
        let torqueBody = worldToBody(torque)
        let angAccelBody = SIMD3(torqueBody.x / inertia.x,
                                 torqueBody.y / inertia.y,
                                 torqueBody.z / inertia.z)
        let angAccelWorld = body.rotate(angAccelBody)
        body.angularVelocity += angAccelWorld * dt
        let maxRate: Float = 60          // rad/s
        body.angularVelocity = SIMD3(min(max(body.angularVelocity.x, -maxRate), maxRate),
                                     min(max(body.angularVelocity.y, -maxRate), maxRate),
                                     min(max(body.angularVelocity.z, -maxRate), maxRate))
        integrateOrientation(dt: dt)
        // The body owns its own clock (spec #22): neural dt never drives this.
        elapsedMs += dt * 1000
        return accel
    }

    // MARK: - Helpers

    @inline(__always)
    func clamp(_ v: Float, _ lo: Float, _ hi: Float) -> Float {
        min(max(v, lo), hi)
    }

    /// Integrate the orientation quaternion from the world-frame angular
    /// velocity, then renormalise (no quaternion drift over long runs).
    private mutating func integrateOrientation(dt: Float) {
        let w = body.angularVelocity
        let rate = FlyMath.length(w)
        guard rate > 1e-6 else { return }
        let axisWorld = w / rate
        let axis = worldToBody(axisWorld)
        let half = rate * dt * 0.5
        let s = sin(half)
        let dq = SIMD4<Float>(axis.x * s, axis.y * s, axis.z * s, cos(half))
        body.orientation = normalizeQuat(multiplyQuat(body.orientation, dq))
    }

    /// Rotate a world-frame vector into the body frame.
    func worldToBody(_ v: SIMD3<Float>) -> SIMD3<Float> {
        let q = body.orientation
        let u = SIMD3(-q.x, -q.y, -q.z)
        let uv = FlyMath.cross(u, v)
        let uuv = FlyMath.cross(u, uv)
        return v + 2 * (q.w * uv + uuv)
    }

    func multiplyQuat(_ a: SIMD4<Float>, _ b: SIMD4<Float>) -> SIMD4<Float> {
        let av = SIMD3(a.x, a.y, a.z), bv = SIMD3(b.x, b.y, b.z)
        let w = a.w * b.w - (av.x * bv.x + av.y * bv.y + av.z * bv.z)
        let v = a.w * bv + b.w * av + FlyMath.cross(av, bv)
        return SIMD4(v.x, v.y, v.z, w)
    }

    func normalizeQuat(_ q: SIMD4<Float>) -> SIMD4<Float> {
        let l = (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w).squareRoot()
        return l > 1e-6 ? q / l : SIMD4(0, 0, 0, 1)
    }
}