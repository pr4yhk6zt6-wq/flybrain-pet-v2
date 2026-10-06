#!/usr/bin/env python3
"""
Faithful Python port of ios/Sources/FlyBrainCore/NeuralEngine.swift (+NeuronCell,
+TestSupport) used ONLY to tune test parameters offline: there is no Swift
toolchain on the device, so the only local ground truth is a mirror of the
engine's arithmetic. Determinism is preserved (xorshift64* RNG, (time,seq) heap).

Mirrored exactly:
  dv = ((rest - v) + I*10 - w) / tauM * dt
  dw = (b*(v - rest) - w) / tauAdapt * dt
  spike -> v = reset; refractory = tauRefractory; w += spikeTriggeredAdaptation
  noise = (rand*2-1) * noiseScale * tauM   (consumed once per touched neuron)
  synapse current = efficacy * gain * synapseCount * sign
  retain(): drop events with t <= currentTime; keep the rest (parent rebuilds heap)
"""
import heapq


class Engine:
    def __init__(self, n, regions=None, syn_out=None, params=None, resting=-60.0,
                 threshold=-50.0, reset=-65.0):
        self.n = n
        p = params or {}
        self.dt = p.get('dt', 0.1)
        self.gain = p.get('synapticGain', 1.0)
        self.noiseScale = p.get('noiseScale', 0.005)
        self.seed = p.get('seed', 0x5EED)
        self.rngState = (self.seed * 0x9E3779B97F4A7C15 + 1) & 0xFFFFFFFFFFFFFFFF
        self.v = [resting] * n
        self.w = [0.0] * n
        self.resting = resting
        self.threshold = threshold
        self.reset = reset
        self.tauM = [10.0] * n
        self.tauAdapt = [100.0] * n
        self.b = [0.0] * n
        self.a = [0.0] * n
        self.tauRef = [2.0] * n
        self.refr = [0.0] * n
        self.cum = [0] * n
        self.lastSpike = [-1e18] * n
        self.time = 0.0
        self.cumTotal = 0
        self.seq = 1
        self.heap = []
        # out-neighbours: list of (post, current)
        self.out = syn_out or [[] for _ in range(n)]
        self.touched = set()

    # --- xorshift64* -------------------------------------------------------
    def nextRandom(self):
        x = self.rngState
        x ^= (x >> 12)
        x ^= (x << 25) & 0xFFFFFFFFFFFFFFFF
        x ^= (x >> 27)
        self.rngState = x & 0xFFFFFFFFFFFFFFFF
        return (self.rngState * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF

    def randomDouble(self):
        return (self.nextRandom() >> 11) * (1.0 / 9007199254740992.0)

    # --- event queue -------------------------------------------------------
    def push(self, t, post, cur):
        heapq.heappush(self.heap, (t, self.seq, post, cur))
        self.seq += 1

    def inject(self, neuron, current, at):
        self.push(at, neuron, current)

    def step(self):
        dt = self.dt
        stepTime = self.time + dt
        touched = self.touched
        touched.clear()
        acc = {}
        # FIXED ENGINE: refractory counters decay every step, not only when
        # the neuron happens to receive input (sparse integration otherwise
        # leaves counters stalled and silently discards burst input).
        self.refr = [max(0.0, r - dt) if r > 0 else 0.0 for r in self.refr]
        while self.heap and self.heap[0][0] <= stepTime:
            t, s, post, cur = heapq.heappop(self.heap)
            acc[post] = acc.get(post, 0.0) + cur
            touched.add(post)
        for i in touched:
            cur = acc.get(i, 0.0)
            noise = (self.randomDouble() * 2 - 1) * self.noiseScale * self.tauM[i]
            if self.refr[i] > 0:
                continue
            I = cur + noise
            self.v[i] += ((self.resting - self.v[i]) + I * 10 - self.w[i]) / self.tauM[i] * dt
            self.w[i] += (self.b[i] * (self.v[i] - self.resting) - self.w[i]) / self.tauAdapt[i] * dt
            if self.v[i] >= self.threshold:
                self.v[i] = self.reset
                self.refr[i] = self.tauRef[i]
                self.w[i] += self.a[i]
                self.emit(i, stepTime)
        self.time = stepTime

    def emit(self, neuron, time):
        self.cum[neuron] += 1
        self.cumTotal += 1
        for (post, cur) in self.out[neuron]:
            self.push(time + self.dt, post, cur)   # delaySteps=1 -> dt ms

    def retain(self):
        """Mirror of the parent's retain-before-restore heuristic."""
        self.heap = [e for e in self.heap if e[0] > self.time]
        heapq.heapify(self.heap)

    def run(self, steps):
        for _ in range(steps):
            self.step()


# --- TestSupport.chainConnectome -------------------------------------------
def chain(count, efficacy=0.5, synapseCount=100, gain=1.0, dt=0.1):
    """neurons 0..n-1 chain 0->1->...->n-1 plus feedback last->0, all
    cholinergic excitatory, region centralComplex (tauM 15, tauAdapt 150,
    b 0.5, a 2.0)."""
    edges = []
    for i in range(count):
        lst = []
        if i < count - 1:
            lst.append((i + 1, efficacy * gain * synapseCount))
        else:
            lst.append((0, efficacy * gain * synapseCount))
        edges.append(lst)
    e = Engine(count, syn_out=edges)
    for i in range(count):
        e.tauM[i] = 15.0
        e.tauAdapt[i] = 150.0
        e.b[i] = 0.5
        e.a[i] = 2.0
    return e


def driveBurst(e, neuron, startMs, pulses, intervalMs, current):
    for i in range(pulses):
        e.inject(neuron, current, startMs + i * intervalMs)