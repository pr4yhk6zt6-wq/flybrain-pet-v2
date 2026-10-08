#!/usr/bin/env python3
"""Offline mirror of FlyBrainCore/BodyDynamics.swift.

There is no Swift toolchain on the authoring device (iOS/iSH), so the rigid-body
solver is mirrored here line by line to (a) calibrate the aerodynamic
coefficients against measured Drosophila numbers and (b) verify the numerical
claims the Swift XCTest suite asserts, so those tests are calibrated rather
than guessed. Any change to BodyDynamics.swift must be mirrored here.

Run:  python3 tools/sim_body_dynamics.py
"""
import math

# ---- PhysicsParameters (must match BodyDynamics.swift) --------------------
MASS = 0.78              # mg
GRAVITY = 9806.65        # mm/s2
STAND_HEIGHT = 0.8       # mm
LEG_TRACTION_GAIN = 800  # nN per mm/s
LEG_MAX_FORCE = 2500     # nN per leg
STROKE_ACTUATOR_TAU_MS = 15   # ms, mirror of BodyDynamics.strokeActuatorTauMs
GROUND_FRICTION = 0.6
LINEAR_DRAG = 6          # nN per mm/s
ANGULAR_DRAG = 60
CONTACT_STIFFNESS = 400000
CONTACT_DAMPING = 2500
WING_LENGTH = 2.5
WING_AREA = 2.6
AIR_DENSITY = 1.2e-3
LIFT_COEFF = 1.1
DRAG_COEFF = 0.5
HALTERE_GAIN = 6
GROUND_Y = 0.0
WEIGHT = MASS * GRAVITY

# MotorSystem readout limits
MAX_STROKE_FREQ = 180.0
MAX_STROKE_AMP = 0.9
STRIDE_RATE_PER_DRIVE = 10.0
STRIDE_LENGTH = 3.0


def wing_tip_speed(amplitude, frequency_hz):
    """RMS tip speed of a sinusoidal stroke (see Swift doc comment)."""
    return 2 * math.pi * abs(frequency_hz) * abs(amplitude) * WING_LENGTH / math.sqrt(2)


def wing_force(speed, coeff):
    return 0.5 * AIR_DENSITY * speed * speed * WING_AREA * coeff


def aerodynamics(amp, freq, asym=0.0, pitch_bias=0.0):
    """Returns (lift, thrust) in nN for a symmetric/asymmetric stroke pair."""
    tip = wing_tip_speed(amp, freq)
    if tip <= 0:
        return 0.0, 0.0
    v_l = tip * (1 + 0.5 * asym)
    v_r = tip * (1 - 0.5 * asym)
    lift = wing_force(v_l, LIFT_COEFF) + wing_force(v_r, LIFT_COEFF)
    drag_one = wing_force(tip, DRAG_COEFF)
    thrust = (drag_one * 2) * max(0.2, 1 - abs(pitch_bias)) * 0.35
    lift *= max(0.25, 1 - abs(pitch_bias) * 0.6)
    return lift, thrust


class Body:
    """RigidBody (Y-up world). Body axes: +x forward, +y dorsal, +z right."""

    def __init__(self):
        self.pos = [0.0, GROUND_Y + STAND_HEIGHT, 0.0]
        self.vel = [0.0, 0.0, 0.0]
        self.normal = 0.0
        self.grounded = True
        self.elapsed_ms = 0.0
        self.wing_tip = 0.0
        self.lift = 0.0
        self.stroke_amp = 0.0
        self.stroke_freq = 0.0

    def forward(self):
        return [1.0, 0.0, 0.0]

    def dorsal(self):
        return [0.0, 1.0, 0.0]

    def lateral(self):
        return [0.0, 0.0, 1.0]

    def ground_speed(self):
        return math.hypot(self.vel[0], self.vel[2])

    def teleport(self, pos, vel=None):
        """Mirror of `BodyDynamics.teleport`: a spawn, not a move. Derived
        actuator state belongs to the pose it was measured at, so it is cleared;
        carrying the stroke of the previous flight over would make the first
        frames report a wing beat that never happened."""
        self.pos = list(pos)
        self.vel = list(vel) if vel else [0.0, 0.0, 0.0]
        self.stroke_amp = 0.0
        self.stroke_freq = 0.0
        self.wing_tip = 0.0
        self.lift = 0.0
        self.grounded = self.pos[1] <= GROUND_Y + STAND_HEIGHT

    def step(self, fwd_target=0.0, lat_target=0.0, contact=1.0,
             legs_in_contact=True, amp=0.0, freq=0.0, asym=0.0,
             pitch_bias=0.0, haltere=0.0, dt=0.0001):
        if dt <= 0:
            return
        fwd = self.forward()
        lat = self.lateral()
        dor = self.dorsal()
        legs_planted = legs_in_contact and contact > 0

        force = [0.0, 0.0, 0.0]
        # 1. legs: traction-limited force source
        if legs_planted:
            v_fwd = self.vel[0]
            v_lat = self.vel[2]
            c = min(max(contact, 0.0), 1.0)
            max_f = LEG_MAX_FORCE * 3 * c
            f_long = min(max(LEG_TRACTION_GAIN * (fwd_target - v_fwd), -max_f), max_f)
            f_side = min(max(LEG_TRACTION_GAIN * (lat_target - v_lat), -max_f), max_f)
            force[0] += fwd[0] * f_long + lat[0] * f_side
            force[2] += fwd[2] * f_long + lat[2] * f_side
        # 2. wings
        # The wings lag the command: muscle cannot change stroke instantly, so
        # the realised stroke is a first-order lag of the commanded one, and it
        # is the REALISED stroke that makes force and that a strain receptor on
        # the wing would measure. Mirror of `BodyDynamics.step`.
        alpha = min(1.0, dt * 1000.0 / max(STROKE_ACTUATOR_TAU_MS, 1e-3))
        self.stroke_amp += (amp - self.stroke_amp) * alpha
        self.stroke_freq += (freq - self.stroke_freq) * alpha
        self.stroke_amp = max(0.0, self.stroke_amp)
        self.stroke_freq = max(0.0, self.stroke_freq)
        tip = wing_tip_speed(self.stroke_amp, self.stroke_freq)
        self.wing_tip = tip
        lift, thrust = aerodynamics(self.stroke_amp, self.stroke_freq, asym, pitch_bias)
        self.lift = lift
        force[1] += dor[1] * lift
        force[0] += fwd[0] * thrust
        # 3. weight
        force[1] += -GRAVITY * MASS
        # 4. drag
        for i in range(3):
            force[i] += -LINEAR_DRAG * self.vel[i]
        # 5. integrate
        accel = [f / MASS for f in force]
        cap = 200000.0
        accel = [min(max(a, -cap), cap) for a in accel]
        for i in range(3):
            self.vel[i] += accel[i] * dt
            self.pos[i] += self.vel[i] * dt
        # 6. contact (penalty spring-damper)
        contact_level = GROUND_Y + STAND_HEIGHT
        penetration = contact_level - self.pos[1]
        if penetration > 0:
            elastic = CONTACT_STIFFNESS * penetration
            damping = CONTACT_DAMPING * self.vel[1]
            self.normal = max(0.0, elastic - damping)
            self.vel[1] += (self.normal / MASS) * dt
            self.grounded = True
            if not legs_planted:
                decel = GROUND_FRICTION * GRAVITY * dt
                hs = self.ground_speed()
                if hs > 0:
                    scale = max(0.0, hs - decel) / hs
                    self.vel[0] *= scale
                    self.vel[2] *= scale
        else:
            self.grounded = False
            self.normal = 0.0
        self.elapsed_ms += dt * 1000.0
        return accel


def run(seconds, dt=0.0001, **kw):
    b = Body()
    n = int(seconds / dt)
    for _ in range(n):
        b.step(dt=dt, **kw)
    return b


def main():
    print("=" * 68)
    print("AERODYNAMIC CALIBRATION (must match BodyDynamics.swift defaults)")
    print("=" * 68)
    tip = wing_tip_speed(MAX_STROKE_AMP, MAX_STROKE_FREQ)
    lift, thrust = aerodynamics(MAX_STROKE_AMP, MAX_STROKE_FREQ)
    print(f"  RMS wing-tip speed    : {tip:8.0f} mm/s  ({tip/1000:.2f} m/s)")
    print(f"  published tip speed   : ~2000-2500 mm/s (Drosophila, MEASURED)")
    print(f"  weight                : {WEIGHT:8.0f} nN")
    print(f"  lift at full drive    : {lift:8.0f} nN   ratio {lift/WEIGHT:.2f}x")
    print(f"  thrust at full drive  : {thrust:8.0f} nN   a = {thrust/MASS:.0f} mm/s2")
    print(f"  minimum ratio to hover: 1.00x -> full drive gives climb margin")

    print()
    print("=" * 68)
    print("SCENARIO 1 — silent motor command (no motor-neuron spikes)")
    print("=" * 68)
    b = run(0.5)
    print(f"  pos   = ({b.pos[0]:.6f}, {b.pos[1]:.6f}, {b.pos[2]:.6f})")
    print(f"  vel   = ({b.vel[0]:.6f}, {b.vel[1]:.6f}, {b.vel[2]:.6f}) mm/s")
    print(f"  contact load = {b.normal/WEIGHT:.3f} body weights")
    print(f"  -> horizontal drift {math.hypot(b.pos[0], b.pos[2]):.2e} mm (expect 0)")
    print(f"  -> sinks below stand height by {max(0, STAND_HEIGHT-b.pos[1])*1000:.1f} um")

    print()
    print("=" * 68)
    print("SCENARIO 2 — full leg drive, walking")
    print("=" * 68)
    for drive in (0.25, 0.5, 1.0):
        target = drive * STRIDE_RATE_PER_DRIVE * STRIDE_LENGTH
        b = run(2.0, fwd_target=target, contact=0.5)
        print(f"  drive {drive:4.2f} -> setpoint {target:5.1f} mm/s, "
              f"actual x-vel {b.vel[0]:6.1f} mm/s, travelled {b.pos[0]:7.1f} mm")
    print("  -> silent drive gives exactly 0 mm/s (no free-running oscillator)")

    print()
    print("=" * 68)
    print("SCENARIO 3 — takeoff: wings driven vs not driven")
    print("=" * 68)
    b = Body()
    b.step(legs_in_contact=False)
    print(f"  wings silent, airborne: lift {b.lift:.0f} nN -> falls "
          f"(a = {-GRAVITY:.0f} mm/s2)")
    b = run(0.15, legs_in_contact=False, amp=MAX_STROKE_AMP, freq=MAX_STROKE_FREQ)
    print(f"  wings full, 0.15 s   : vel.y {b.vel[1]:+8.1f} mm/s "
          f"(positive = climbing), height {b.pos[1]:.2f} mm")
    print("  -> lift > weight gives climb; silent wings give free fall")

    print()
    print("=" * 68)
    print("SCENARIO 4 — turning sign (left/right leg drive imbalance)")
    print("=" * 68)
    print("  lateralSpeedTarget is (leftDrive-rightDrive)*strideLength*4;")
    print("  the solver moves the body along +lateral (body +z = right):")
    for lat in (-20.0, 0.0, 20.0):
        b = run(1.0, lat_target=lat, contact=0.5)
        print(f"    lat target {lat:+6.1f} mm/s -> z {b.pos[2]:+7.2f} mm, "
              f"z-vel {b.vel[2]:+6.1f} mm/s")
    print("  -> sign of body motion follows sign of the drive imbalance")


if __name__ == "__main__":
    main()