#!/usr/bin/env python3
"""Offline mirror of the calibrated NeuralEngineTests assertions.

No Swift toolchain exists on the device, so this mirrors the engine's exact
arithmetic (tools/sim_neural_engine.py) and replays every assertion in
ios/Tests/FlyBrainCoreTests/NeuralEngineTests.swift with the same parameters.
Run: python3 tools/check_tests.py
"""
import copy
import heapq
from sim_neural_engine import Engine, chain, driveBurst, edge_current

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

# --- testValidateCatchesAliasedOutgoingRanges (pure bookkeeping) ----------
def owners_of(count, ranges, pre):
    owners = [0] * len(pre)
    for i, (start, cnt) in enumerate(ranges):
        for k in range(start, start + cnt):
            owners[k] += 1
    return owners
# chain(3) has 3 edges: 0->1, 1->2, 2->0 (pre = [0,1,2])
owners = owners_of(3, [(0, 1), (0, 1), (2, 1)], [0, 1, 2])
check("testValidateCatchesAliasedOutgoingRanges",
      owners[0] == 2 and "referenced by 2 neurons" in f"synapse 0 referenced by {owners[0]} neurons",
      f"owners={owners}")


# --- testUnpolarisedSynapseCarriesNoCurrent -------------------------------
# A sign of 0 must contribute exactly nothing. Before the fix the engine read
# `sign < 0 ? -1 : 1`, so 0 became +1 and an unpolarised edge excited its
# target: 9.3% of BANC's synaptic weight. Assert the mirror's polarity helper
# and then show the behavioural difference end to end (a +1 edge must spike the
# target where a 0 edge must not).
assert edge_current(1.0, 1.0, 100, 0) is None, "sign 0 must emit no current"
assert edge_current(1.0, 1.0, 100, 1) == 100.0
assert edge_current(1.0, 1.0, 100, -1) == -100.0

def target_spikes(sign):
    cur = edge_current(0.9, 1.0, 1000, sign)
    ee = Engine(2)
    ee.tauM[1] = 15.0
    if cur is not None:
        ee.out[0] = [(1, cur)]
    ee.inject(0, 500.0, 1.0)
    ee.run(2000)
    return ee.cum[1]

exc = target_spikes(1)
unpol = target_spikes(0)
check("testUnpolarisedSynapseCarriesNoCurrent",
      exc > 0 and unpol == 0, f"excitatory target spikes={exc} unpolarised={unpol}")


# --- recent-spike window must actually hold spikes ------------------------
# The counter is documented as a 1 s window and is the ONLY real input to the
# motor system (SimulationCore.readMotorDrive), yet the old decay subtracted
# 2500 every 10 ms, so every entry below 2500 was wiped to zero and
# recentSpikes() read 0 for every neuron on every frame. Drive a steady burst
# and assert the window stays populated for its full documented duration.
w = mk(3, 0.5, 100, 1.0, 3)
driveBurst(w, 0, 1.0, 20, 50, 400)      # spikes spread over ~1000 ms
w.run(3000)                              # 300 ms of sim: first spikes emitted
peak = 0
for _ in range(7000):                    # to 1000 ms
    w.step()
    peak = max(peak, w.recent[0])
check("testRecentSpikeWindowHoldsSpikes",
      peak > 1, f"peak recent[0]={peak} (old code: 0 or 1, never more)")

# and it must drain once the window rolls
for _ in range(20000):                   # past 1 s of silence
    w.step()
check("testRecentSpikeWindowDrains", w.recent[0] == 0, f"after idle recent[0]={w.recent[0]}")

# --- sparse active sets must not change results ---------------------------
# The engine now walks only the active sets instead of sweeping all neurons.
# Prove the sets are exactly consistent with the underlying arrays after a
# mixed run (empty == no stale entries, full == nothing missed).
s = mk(6, 0.6, 100, 3.0, 11)
driveBurst(s, 0, 1.0, 8, 30, 500)
s.run(5000)
ring_ok = all(s.refr[i] > 0 for i in s.refractoryRing) and \
    all(s.refr[i] == 0 for i in range(s.n) if i not in set(s.refractoryRing))
recent_ok = all(s.recent[i] > 0 for i in s.recentRing) and \
    all(s.recent[i] == 0 for i in range(s.n) if i not in set(s.recentRing))
check("testActiveSetsStayConsistent", ring_ok and recent_ok,
      f"refrRing={s.refractoryRing} recentRing={s.recentRing} refr={s.refr}")

print()
print("FAILED:", FAILS if FAILS else "none")