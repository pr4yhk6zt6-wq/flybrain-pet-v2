#!/usr/bin/env python3
"""Offline mirror of the calibrated NeuralEngineTests assertions.

No Swift toolchain exists on the device, so this mirrors the engine's exact
arithmetic (tools/sim_neural_engine.py) and replays every assertion in
ios/Tests/FlyBrainCoreTests/NeuralEngineTests.swift with the same parameters.
Run: python3 tools/check_tests.py
"""
import copy
import heapq
from sim_neural_engine import Engine, chain, driveBurst

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        FAILS.append(name)


def mk(count, eff, sc, gain, seed, noise=0.005):
    e = chain(count, eff, sc, gain)
    e.noiseScale = noise
    e.seed = seed
    e.rngState = (seed * 0x9E3779B97F4A7C15 + 1) & 0xFFFFFFFFFFFFFFFF
    return e


# --- testChainPropagationProducesSpikes ------------------------------------
e = mk(5, 0.8, 100, 5.0, 42)
e.inject(0, 400.0, 2.0)                       # ONE presynaptic spike
first = {}
for _ in range(2000):
    e.step()
    for i in range(5):
        if e.cum[i] and i not in first:
            first[i] = round(e.time, 1)
check("testChainPropagationProducesSpikes",
      e.cumTotal > 0 and all(e.cum[i] > 0 for i in range(5)),
      f"total={e.cumTotal} per={e.cum} firstSpike=" +
      "{" + ", ".join(f"{i}: {first.get(i)}" for i in range(5)) + "}")

# --- testDeterminismSameSeed ----------------------------------------------
def same_run():
    x = mk(8, 0.5, 100, 4.0, 7)
    x.inject(0, 400.0, 2.0)
    x.run(500)
    return x.cumTotal, x.time
a, b = same_run(), same_run()
check("testDeterminismSameSeed", a[0] == b[0] and a[1] == b[1], f"spikes {a[0]}=={b[0]}, t {a[1]}")

# --- testDeterminismDifferentSeedsDiffer ----------------------------------
def seed_run(seed):
    x = mk(4, 0.5, 100, 1.0, seed, noise=0.5)
    driveBurst(x, 0, 1.0, 60, 10, 240)
    x.run(8000)
    return tuple(x.cum)
vals = [seed_run(s) for s in (7, 999, 12345)]
check("testDeterminismDifferentSeedsDiffer", len(set(vals)) > 1,
      f"seeds 7/999/12345 -> {vals}")

# --- testRefractoryDecaysWithoutFurtherInput ------------------------------
e = mk(2, 0.8, 100, 5.0, 1)
driveBurst(e, 0, 1.0, 2, 20, 400)
e.run(1000)
check("testRefractoryDecaysWithoutFurtherInput", e.cum[0] >= 2,
      f"driver spikes={e.cum[0]} (2 pulses 20 ms apart)")

# --- testInhibitorySynapseSuppressesDownstream ----------------------------
def veto(withVeto):
    out = [[], [], []]
    out[0].append((2, 0.6 * 1.0 * 100))
    if withVeto:
        out[1].append((2, -2.5 * 1.0 * 100))
    x = Engine(3, syn_out=out)
    for i in range(3):
        x.tauM[i], x.tauAdapt[i], x.b[i], x.a[i] = 15, 150, 0.5, 2.0
    x.seed = 3
    x.rngState = (3 * 0x9E3779B97F4A7C15 + 1) & 0xFFFFFFFFFFFFFFFF
    if withVeto:
        driveBurst(x, 1, 1.0, 15, 15, 400)
    driveBurst(x, 0, 5.0, 15, 15, 400)
    x.run(2500)
    return x.cum[2]
wv, wo = veto(True), veto(False)
check("testInhibitorySynapseSuppressesDownstream", wv < wo and wo > 0,
      f"withVeto={wv} control={wo}")

# --- testSnapshotRestoreRoundTrip -----------------------------------------
A = mk(6, 0.5, 100, 1.0, 11)
A.inject(0, 400.0, 1.0)
A.run(1000)
B = mk(6, 0.5, 100, 1.0, 11)
B.v, B.w, B.refr, B.cum = copy.copy(A.v), copy.copy(A.w), copy.copy(A.refr), copy.copy(A.cum)
B.rngState, B.time, B.cumTotal, B.seq = A.rngState, A.time, A.cumTotal, A.seq
B.heap = [x for x in A.heap if x[0] > A.time]   # restore() drops events at t <= clock
heapq.heapify(B.heap)
A.run(1000)
B.run(1000)
check("testSnapshotRestoreRoundTrip",
      A.cumTotal == B.cumTotal and abs(A.time - B.time) < 1e-9,
      f"A={A.cumTotal} B={B.cumTotal} tA={A.time:.1f} tB={B.time:.1f}")

# --- testTelemetryIsReal --------------------------------------------------
e = mk(4, 0.9, 100, 5.0, 5)
e.inject(0, 500.0, 1.0)
e.run(4000)
check("testTelemetryIsReal", e.cumTotal > 0, f"spikes={e.cumTotal}")

print()
print("FAILED:", FAILS if FAILS else "none")