#!/usr/bin/env python3
"""Measure the simulation's real-time relationship, straight from the source.

The neural clock and the wall clock are only loosely related: `parameters.dt`
is in MILLISECONDS of neural time, while the app advances a fixed number of
steps per display frame. This tool reads those numbers out of the Swift sources
instead of restating them, so documentation and any gate cannot drift from the
code, and prints the two quantities that matter:

  1. how much simulated time passes per second of real time (the slow-motion
     factor), and
  2. how long, in REAL seconds, the motor readout needs to observe a change —
     i.e. the reaction latency the animal pays for reading a neural-timescale
     window.

That second number is the interesting one: it is what a reflex costs, and it is
what makes a 1 s neural window a strange thing to drive a leg with.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "ios/Sources/FlyBrainCore/NeuralEngine.swift"
APP = ROOT / "ios/Sources/FlyBrainPetApp/AppModel.swift"
CORE = ROOT / "ios/Sources/FlyBrainCore/SimulationCore.swift"


def read_dt_ms() -> float:
    m = re.search(r"public var dt: Double = ([0-9.]+)", ENGINE.read_text())
    if not m:
        raise SystemExit("could not find `parameters.dt` in NeuralEngine.swift")
    return float(m.group(1))


def read_steps_per_frame() -> int:
    m = re.search(r"core\.run\(steps: ([0-9]+)\)", APP.read_text())
    if not m:
        raise SystemExit("could not find `core.run(steps:)` in AppModel.swift")
    return int(m.group(1))


def read_frame_hz() -> float:
    m = re.search(r"timeInterval: 1\.0 / ([0-9.]+)", APP.read_text())
    if not m:
        raise SystemExit("could not find the ticker interval in AppModel.swift")
    return float(m.group(1))


def read_recent_window_ms() -> float:
    m = re.search(r"recentWindowSeconds: Double = ([0-9.]+)", ENGINE.read_text())
    if m:
        return float(m.group(1)) * 1000.0
    m = re.search(r"recentWindowMs = ([0-9.]+)", ROOT.joinpath("tools/sim_neural_engine.py").read_text())
    if m:
        return float(m.group(1))
    raise SystemExit("could not find the recent-spike window length")


def read_motor_tau_ms() -> float:
    """The motor drive's rate time constant (ms of neural time)."""
    m = re.search(r"motorRateTauMs: Float = ([0-9.]+)", ENGINE.read_text())
    if not m:
        raise SystemExit("could not find `motorRateTauMs` in NeuralEngine.swift")
    return float(m.group(1))


def read_motor_readout() -> str:
    """Which signal the motor drive actually samples. This is the invariant the
    gate protects: the drive must NOT come from the tumbling window."""
    src = CORE.read_text()
    m = re.search(r"private func readMotorDrive\(\) \{(.*?)\n    \}", src, re.S)
    if not m:
        raise SystemExit("could not find `readMotorDrive` in SimulationCore.swift")
    return m.group(1)


def main() -> int:
    dt_ms = read_dt_ms()
    steps = read_steps_per_frame()
    hz = read_frame_hz()
    window_ms = read_recent_window_ms()
    tau_ms = read_motor_tau_ms()
    drive_body = read_motor_readout()

    per_frame_ms = dt_ms * steps
    neural_ms_per_real_s = per_frame_ms * hz
    slowmo = 1000.0 / neural_ms_per_real_s
    real_ms_per_neural_ms = 1000.0 / neural_ms_per_real_s

    window_real_ms = window_ms * real_ms_per_neural_ms
    tau_real_ms = tau_ms * real_ms_per_neural_ms

    print("Simulation time base (read from source, not restated)")
    print(f"  dt                     : {dt_ms} ms of neural time per step")
    print(f"  steps per display frame: {steps}")
    print(f"  display frame rate     : {hz:g} Hz")
    print()
    print(f"  neural time per frame  : {per_frame_ms:g} ms")
    print(f"  neural time per real s : {neural_ms_per_real_s:g} ms")
    print(f"  slow-motion factor     : {slowmo:.1f}x "
          f"(1 s of neural time takes {slowmo:.0f} s of real time)")
    print()
    print("Response latency, in REAL time")
    print(f"  1 s recent-spike window: {window_real_ms/1000:6.1f} s to observe a change")
    print(f"  motor rate tau ({tau_ms:g} ms)  : {tau_real_ms:6.0f} ms "
          f"({tau_real_ms/1000:.2f} s) to observe a change")

    fails = []

    # The window is an inspection tool, but nobody may DRIVE anything from it:
    # at this clock speed it needs tens of seconds of wall time to move.
    if "recentSpikes(of:" in drive_body:
        fails.append("readMotorDrive samples the tumbling recent-spike window")
    if "rateHz(of:" not in drive_body:
        fails.append("readMotorDrive does not sample the leaky rate")
    if "firingActiveNeurons" not in drive_body:
        fails.append("readMotorDrive does not walk the O(active) firing set")

    # A drive signal must be quicker than a neural window, or the animal cannot
    # react: the rate constant should be a sensorimotor timescale, well under
    # the inspection window.
    if tau_ms >= window_ms:
        fails.append(f"motor rate tau ({tau_ms} ms) is not shorter than the "
                     f"{window_ms} ms inspection window")

    if window_real_ms > 1000:
        print("\n  NOTE: reading the 1 s window as a drive signal could not react\n"
              f"        in under {window_real_ms/1000:.1f} s of wall time — not a property\n"
              "        of the fly, but of reading a neural window as if instantaneous.")

    print()
    for f in fails:
        print(f"[FAIL] {f}")
    print("FAILED:", fails if fails else "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())