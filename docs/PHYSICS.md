# Phase 6/7 — Body Physics & Flight (working plan)

Status: **in progress**. Owner: agent. Verify path: GitHub Actions CI
(`.github/workflows/ci.yml`) + offline Python mirror in `tools/`.

## Why

The closed loop currently stops being physical at the body: `MotorSystem`
advances a gait phase with a hardcoded timer term
(`dt * 0.004 * (0.3 + drive)`) and `SimulationCore.integrateLocomotion`
teleports the fly with `position += dir * walkSpeed * legDrive * dt`. Wings are
animated from wall-clock time (`sin(t · 2π · f)`), not driven by muscle state.
That is animation used as the controller, which spec §11 forbids, and it means
the "BODY → WORLD" arrow of the loop carries no physics.

## Increments (each independently testable, pushed separately)

1. **`BodyDynamics.swift`** — rigid-body state (position, velocity, quaternion
   orientation, angular velocity) with mm/mg/s units (force unit = nN), gravity,
   quadratic air drag, penalty ground contact, friction-limited leg propulsion,
   yaw torque from L/R imbalance. Kinematics only: forces come from the caller.
2. **Gait as a neural oscillator** (`MotorSystem`) — leg phase advances at a
   rate *set by leg-neuromere drive*, with tripod partner coupling. Silent
   connectome ⇒ no stepping. Delete the constant term.
3. **Wings/halteres as driven state** — stroke phase integrates from wing-muscle
   drive; haltere beat follows the same oscillator (inertial feedback).
4. **Closed loop** — `SimulationCore` feeds motor output into `BodyDynamics`;
   body state (velocity, ground contact) feeds mechanosensory input back into
   the VNC; world collision acts on the body, not on a teleported point.
5. **Flight (Phase 7)** — quasi-steady lift/thrust from wing state; takeoff when
   wing drive exceeds the weight margin; haltere-mediated stabilisation damps
   angular rate; landing when drive collapses.
6. **Validation tests** (spec §27) — walking stability, turning sign,
   flight initiation latency, landing, determinism under the new integrator.

## Aerodynamic calibration (increment 5, measured against the mirror)

The quasi-steady force law F = ½ρv²SC is applied to the **RMS** wing-tip speed,
not the mean absolute speed: the force is quadratic in v, so averaging v first
would under-count it. For θ(t) = A·sin(2πft) with A = 0.9 rad, f = 180 Hz and
L = 2.5 mm (measured wing length) the RMS tip speed is **1.80 m/s**, against a
published Drosophila tip speed of ~2.0-2.5 m/s — same order, as expected of a
reduced-order model.

With that speed, `effectiveLiftCoefficient = 1.1` and one 2.6 mm² wing per side
give a full-power lift of ≈ **1.45× body weight** (11100 nN vs 7650 nN). The
coefficient is therefore not a measured lift coefficient (a quasi-steady model
cannot produce leading-edge-vortex lift) but a number **calibrated so that the
model has the observed ~1.3-1.5× takeoff margin**: flight becomes possible
exactly when, and only when, the wing neuropil drives the wings. The mirror
`tools/sim_body_dynamics.py` prints this ledger on every run, and
`BodyDynamicsTests.testFlightLiftExceedsWeightButOnlyWhenDriven` pins it.

## Corrections applied during increment 4-6 (all were real bugs)

- **Time units.** The neural clock `parameters.dt` is in **milliseconds** (0.1),
  while the body solver is in **seconds** (mm/mg/s/nN). The dt was being passed
  straight through, so gravity and drag were 1000× too weak per step and the
  joint integrator in `MotorSystem` mixed ms into a rate term.
- **Time scale is not real time, and must not be implied.** The neural clock
  advances 0.4 ms per 60 Hz frame (4 steps × 0.1 ms), i.e. **~42× slow motion**
  relative to wall time (`tools/measure_time_scale.py`). Consequences that are
  easy to miss: any neural-timescale window (the 1 s recent-spike counter) takes
  ~42 s of real time to roll, so signals read from such windows cannot drive a
  per-frame loop — see MOTOR_SYSTEM.md. `AppModel.realTimeFactor` exposes the
  factor. Making the clock faster is a device budget question (true speed would
  need ~8,000 steps/frame), not a tuning constant.
- **Ground contact.** The old code clamped `position.y` back to the surface on
  every step, which deleted the penetration the normal force is derived from and
  applied the contact force only while already sinking — a resting fly received
  no support force at all. Now a proper penalty contact: penetration is kept, the
  unilateral normal force is applied to the velocity unconditionally, and
  position is never snapped.
- **World axes.** `World.swift` clamps `p.y >= 0` (substrate), the app spawns the
  fly at y = 0.2 and places lights/obstacles above y = 0, so the world is **Y-UP**.
  The body accessors, `up` pose and the torque axes were mixed z-up/y-up: yaw was
  being written into the pitch axis and `forward`/`up` disagreed with the world.
  The whole body basis is now y-up (+x forward, +y dorsal, +z right) and the
  inertia tensor is applied in the body frame (`worldToBody`) with the resulting
  angular acceleration rotated back into the world frame.
- **Motor command never delivered.** `SimulationCore` never set
  `command.legsInContact`, so `BodyDynamics.step` saw `legsInContact = true`
  unconditionally and leg drive/timeout made the fly walk while airborne.
  It is now passed through from the motor output.
- **`setPose` was a no-op for physics.** It wrote the core's published pose but
  not the rigid body, and `integrateLocomotion` overwrites the published pose
  from `dynamics` every step — so the app's spawn pose was silently discarded.
  `setPose` now teleports the rigid body (orthonormal basis from forward/up).
- **Scene collision was unreachable.** The doc claimed the world acts on the
  body, but nothing ever resolved obstacles against it. `resolveSceneCollision`
  now corrects the solver position and removes the momentum absorbed by the
  obstacle.
- **The walking gait lifted all six legs.** The stance→swing decision OR-ed a
  mechanical drift timeout with the support requirement, so once
  `maxStanceMs` had elapsed every leg timed out on the same tick. Support is
  now ABSOLUTE (the timeout may request a step, not grant one), the check
  additionally requires the remaining legs to straddle the body (a tripod of
  three legs on one side is not statically stable), and each leg's decision is
  published immediately so the next leg judges the real state rather than a
  stale snapshot. The alternating tripod now EMERGES from the constraint:
  over 400 ms the gait visits only `{left-front, left-mid, right-hind}` and
  `{right-front, right-mid, left-hind}` with 3 legs always planted. Verified by
  `tools/sim_motor_system.py` (gating) and
  `SensoryTests.testGaitAlwaysKeepsSupport` /
  `testGaitConvergesToAlternatingTripod`.
- **Y-up spawn leftovers.** `BodyDynamics.init` wrote the standing height into
  z (leaving y = 0, i.e. buried in the ground plane), and the app spawned with
  `up = +z`; both are now y-up.

## Why the offline mirrors exist

There is no Swift toolchain on the development device, so every physics change
is first reproduced line-by-line in Python (`tools/sim_*.py`), calibrated
against measured fly data, and only then written in Swift. The mirrors are
gating in CI, and they have found every bug listed above except the two API
mismatches that only the real compiler could see.

## Approximation ledger (must stay honest)

- Single rigid torso, massless leg actuators. Real legs are articulated chains;
  their segment inertias are small at walking speeds but this is an
  **APPROXIMATED** simplification, not measured.
- Ground contact is a **penalty (spring-damper)** model, not a complementarity
  solver: penetrations are elastic, contact is never rigid.
- Aerodynamics is **reduced-order quasi-steady** blade-element:
  it cannot reproduce unsteady leading-edge vortex lift. Any lift coefficient
  used is **APPROXIMATED**, never a measured value.
- Every numeric constant carries a provenance class in code
  (`PhysicsParameters`) and is listed in `docs/PHYSICS.md`.
- No behaviour thresholds: nothing here decides *to* walk or *to* fly. Forces
  are produced only from motor-neuron drive.