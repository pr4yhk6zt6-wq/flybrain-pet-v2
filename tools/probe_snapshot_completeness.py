#!/usr/bin/env python3
"""Does restoring an engine snapshot reproduce the run it replaced?

Spec #63: same seed + connectome + parameters + environment must reproduce the
same simulation. That is a claim about OBSERVABLES, so this gate compares what a
restored run reports against the run it was taken from.

WHY THIS GATE EXISTS. `EngineSnapshot` has existed since the first commit and
its test passed, but that test compared `spikeCount` — a monotone counter no
window touches. Every field measured against a WINDOW was left out, and none of
them appears in that test, so the snapshot could be arbitrarily incomplete and
stay green. (Same shape as `probe_shipped_asset_channels.py` before it was
fixed: a gate that reports and cannot fail.)

The omitted fields matter because they are measured against `currentTimeMs`,
which the snapshot DID restore. So a restore produced an engine whose clock said
500 ms while its windows still believed they started at 0 ms:

  * `windowStartTime` stale -> the first step sees the 1 s window as overdue by
    the entire age of the run, rolls it early, computes
    `lastWindowSpikesPerSecond` by dividing by a window length that never
    existed, and zeroes the counter that had just been accumulated.
  * `recentWindowStartTime` stale -> the recent-spike ring, which is the motor
    system's only real input (spec #20), is wiped on the first step.
  * `activeWindowMark` / `activeNeuronsThisWindow` -> every neuron that fires is
    counted as newly active again.
  * `cumulativeSpikes` -> `totalSpikes(of:)` restarts from zero, so a counter
    the inspector reads cannot be compared with itself across a checkpoint.
  * `modelLevels` -> not derived from the connectome at all (the LOD scheduler
    mutates them), and they select which integration equations run, so guessing
    them integrates different math (spec #48).

HOW THIS IS EVIDENCED. The numbers below come from the Python mirror
(`tools/sim_neural_engine.py`), which is line-by-line with `NeuralEngine.step`
and `emitSpike` — including the window rollovers added alongside this gate. The
[PASS] lines that follow also pin the Swift source with regexes, but a regex is
not execution: the semantic claim is carried by `EngineReplayTests.swift`, which
runs only in CI on a macOS runner. Saying which half is measured and which half
is pinned is the point.

USAGE: python3 tools/probe_snapshot_completeness.py
Exit 0 = the restore reproduces the run it replaced. Non-zero = it diverged.
"""

import heapq
import re
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from sim_neural_engine import Engine, chain  # noqa: E402

ROOT = __file__.rsplit("/", 1)[0] + "/.."
FAILURES = []


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    if not ok:
        FAILURES.append(label)


class Snapshot:
    """What `EngineSnapshot` captures. `complete=False` is the field set the
    engine shipped before this change; `complete=True` is after. The two differ
    ONLY in the window/telemetry fields, so the delta between the runs is the
    effect of that change and nothing else.

    The baseline set is deliberately everything the SHIPPED snapshot already
    had — clock, RNG, event sequence, the pending event heap, and the
    per-neuron dynamics the stay-alive fields need (voltage, adaptation,
    refractory, last spike, leaky rate). If the baseline omitted any of those,
    the gate would report them as the change's effect."""

    #: per-neuron dynamics present in the shipped snapshot (NeuronDynamicsSnapshot)
    DYNAMICS = ("v", "w", "refr", "lastSpike", "rate")

    def __init__(self, e, complete):
        self.complete = complete
        # --- baseline: what the engine already captured ---
        self.time = e.time
        self.cumTotal = e.cumTotal
        self.rngState = e.rngState
        self.seq = e.seq
        self.heap = list(e.heap)
        self.dyn = {k: list(getattr(e, k)) for k in self.DYNAMICS}
        # --- the fields this change adds ---
        if complete:
            self.cum = list(e.cum)
            self.recent = list(e.recent)
            self.windowStart = e.windowStart
            self.spikeEventsThisWindow = e.spikeEventsThisWindow
            self.lastWindowSps = e.lastWindowSps
            self.recentWindowStart = e.recentWindowStart
            self.activeMark = list(e.activeMark)
            self.activeCount = e.activeCount

    def restore_into(self, fresh):
        """Restore into a DIFFERENT instance holding the same connectome — the
        actual use case (load a save, resume a run). Derived sets are rebuilt
        from the restored scalars, exactly as `NeuralEngine.restore` does."""
        fresh.time = self.time
        fresh.cumTotal = self.cumTotal
        fresh.rngState = self.rngState
        fresh.seq = self.seq
        fresh.heap = list(self.heap)
        heapq.heapify(fresh.heap)
        for k, vals in self.dyn.items():
            setattr(fresh, k, list(vals))
        # active sets are derived data — rebuilt, never stored (spec #63)
        fresh.refractoryRing = [i for i, v in enumerate(fresh.refr) if v > 0]
        fresh.rateRing = [i for i, v in enumerate(fresh.rate) if v != 0.0]

        if self.complete:
            fresh.cum = list(self.cum)
            fresh.recent = list(self.recent)
            fresh.recentRing = [i for i, v in enumerate(self.recent) if v]
            fresh.windowStart = self.windowStart
            fresh.spikeEventsThisWindow = self.spikeEventsThisWindow
            fresh.lastWindowSps = self.lastWindowSps
            fresh.recentWindowStart = self.recentWindowStart
            fresh.activeMark = list(self.activeMark)
            fresh.activeCount = self.activeCount


def drive(e, steps, already=0, kick_every=37):
    """Kick neuron 0 hard every `kick_every` steps so the chain keeps firing
    across window boundaries. Deterministic: the kick is scheduled at absolute
    step indices, so the true run and the restored run see the SAME stimulus
    schedule — the restored run is handed `already`, the step index its
    checkpoint was taken at.

    The kick is 400 nA because that is what the other engine gates use to make
    this chain fire at all; a token 2.0 nA injection produces no spikes and
    would make every observable here trivially equal (the first version of this
    gate did exactly that and reported a vacuous PASS)."""
    for k in range(steps):
        if (k + already) % kick_every == 0:
            e.inject(0, 400.0, e.time)
        e.step()


def mk(seed):
    """Same construction as the other engine gates: noise on, seeded RNG."""
    e = chain(6)
    e.noiseScale = 0.005
    e.seed = seed
    e.rngState = (seed * 0x9E3779B97F4A7C15 + 1) & 0xFFFFFFFFFFFFFFFF
    return e


def observables(e):
    """The public telemetry a caller or the inspector can read."""
    return {
        "time": round(e.time, 6),
        "cumulativeSpikes": e.cumTotal,
        "totalSpikes(0)": e.cum[0],
        "lastWindowSpikesPerSecond": round(e.lastWindowSps, 9),
        "activeNeuronCount": e.activeCount,
        "recentSpikes(0)": e.recent[0],
        "recentSpikes(1)": e.recent[1],
    }


def run(seed, warm, tail, complete):
    """True run: drive warm+tail. Restored run: drive warm, snapshot, continue
    in a fresh engine. Same connectome, same stimuli, so spec #63 requires the
    two to agree exactly."""
    true = mk(seed)
    drive(true, warm + tail)

    src = mk(seed)
    drive(src, warm)
    snap = Snapshot(src, complete)
    resumed = mk(seed)
    snap.restore_into(resumed)
    drive(resumed, tail, already=warm)

    return observables(true), observables(resumed)


WARM, TAIL = 11000, 4000

print("=== the restore is measured against the run it replaced ===")
print(f"dt 0.1 ms; warm={WARM} steps ({WARM * 0.1:.0f} ms) and tail={TAIL} "
      f"({TAIL * 0.1:.0f} ms). The checkpoint lands PAST the 1 s telemetry "
      f"boundary, which is the condition the bug needs: a restored clock of "
      f"{WARM * 0.1:.0f} ms against a window start left at 0 looks overdue.")

old_true, old_restored = run(7, WARM, TAIL, complete=False)
print()
print("BEFORE (snapshot without the window fields — what the engine shipped):")
for k in old_true:
    a, b = old_true[k], old_restored[k]
    print(f"    {k:26s} true={a!r:>16} restored={b!r:>16}"
          f"{'' if a == b else '   <-- DIVERGED'}")

diverged = [k for k in old_true if old_true[k] != old_restored[k]]
check("the incomplete snapshot LOSES observables a restored run must keep "
      "(so the gate can fail)",
      diverged,
      f"diverged: {', '.join(diverged) if diverged else 'none — the gate would be vacuous'}")

new_true, new_restored = run(7, WARM, TAIL, complete=True)
print()
print("AFTER (snapshot with the window fields — the fix):")
for k in new_true:
    a, b = new_true[k], new_restored[k]
    print(f"    {k:26s} true={a!r:>16} restored={b!r:>16}"
          f"{'' if a == b else '   <-- DIVERGED'}")

still = [k for k in new_true if new_true[k] != new_restored[k]]
check("the complete snapshot reproduces every observable exactly",
      not still,
      f"{len(new_true)} observables agree" if not still
      else f"diverged: {', '.join(still)}")

print()
print("=== the mechanism, isolated (not inferred from the above) ===")
src = mk(7)
drive(src, WARM)
age = src.time - 0.0
check("the checkpoint clock is past the 1 s boundary, which is the condition "
      "the missing window start needs",
      src.time >= 1000.0,
      f"clock={src.time:.0f} ms; a window 'started' at 0 ms would be "
      f"{age:.0f} ms long, so the first restored step rolls it immediately")

# Measure what that early roll reports. Both numbers are the same counter
# divided by two different denominators: the honest 1 s window the engine
# intends, and the age of the run it would actually divide by.
events = src.spikeEventsThisWindow
early_hz = events * 1000.0 / age
honest_hz = events * 1000.0 / 1000.0
check("the early roll reports a rate scaled by the age of the run, not a "
      "1 s window",
      abs(early_hz - honest_hz) > 1e-9 and events > 0,
      f"{events} spikes -> {early_hz:.4f} Hz over {age:.0f} ms instead of "
      f"{honest_hz:.4f} Hz over 1000 ms "
      f"(ratio {1000.0 / age:.6f} = 1000/{age:.0f})")

# And the ring the missing `recentWindowStartTime` wipes, which is the motor
# system's only real input (spec #20): show the counter is non-zero just before
# the roll, so wiping it is a real loss and not a no-op on a quiet engine.
fresh = mk(7)
drive(fresh, WARM)
live = sum(1 for v in fresh.recent if v)
check("the recent-spike ring is non-empty at the checkpoint, so losing it is "
      "a real loss",
      live > 0,
      f"{live} neuron(s) hold a recent-spike count at {fresh.time:.0f} ms")

print()
print("=== the Swift side really carries the fields (pinned, not executed) ===")
swift = open(f"{ROOT}/ios/Sources/FlyBrainCore/NeuralEngine.swift").read()

required = ["spikeEventsThisWindow", "windowStartTime", "lastWindowSpikesPerSecond",
            "recentSpikesPerNeuron", "recentWindowStartTime", "cumulativeSpikes",
            "activeNeuronsThisWindow", "activeWindowMark", "modelLevels"]

snap_decl = re.search(r"public struct EngineSnapshot.*?\n    \}", swift, re.S)
declared = [f for f in required
            if re.search(rf"\bvar {f}\b", snap_decl.group(0) if snap_decl else "")]
check("EngineSnapshot declares every window/telemetry field",
      len(declared) == len(required),
      f"{len(declared)}/{len(required)}; missing: "
      f"{[f for f in required if f not in declared]}")

# Declared-but-forgotten is the classic shape of this bug, so check the writer
# AND the reader separately rather than trusting the struct.
body = re.search(r"EngineSnapshot\(\s*currentTimeMs:.*?\n        \)", swift, re.S)
body_src = body.group(0) if body else ""
populated = [f for f in required if f"{f}:" in body_src]
check("snapshot() populates them in the returned EngineSnapshot",
      len(populated) == len(required),
      f"{len(populated)}/{len(required)}; forgotten: "
      f"{[f for f in required if f not in populated]}")

restore = re.search(r"public func restore\(_ snap: EngineSnapshot\)"
                    r".*?\n    \}", swift, re.S)
restore_src = restore.group(0) if restore else ""
applied = [f for f in required if f"= snap.{f}" in restore_src]
check("restore() writes every one of them back",
      len(applied) == len(required),
      f"{len(applied)}/{len(required)}; not restored: "
      f"{[f for f in required if f not in applied]}")

guarded = len(re.findall(r"if snap\.\w+\.count == \w+\.count", restore_src))
check("per-neuron arrays are count-guarded so a stale snapshot cannot resize "
      "the engine",
      guarded >= 4, f"{guarded} guarded assignments (expected >= 4)")

# The LOD field must not be re-derived on restore: that is the whole reason it
# is in the snapshot.
check("modelLevels is restored rather than re-derived (it is not connectome "
      "data)",
      "modelLevels = snap.modelLevels" in restore_src,
      "present" if "modelLevels = snap.modelLevels" in restore_src
      else "absent — a restore would re-integrate with different equations")

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s)")
    print("FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("0 failure(s)")
print("snapshot completeness: the restore reproduces the run it replaced")