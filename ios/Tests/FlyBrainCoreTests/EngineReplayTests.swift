//
//  EngineReplayTests.swift
//  FlyBrainCoreTests
//
//  Spec #63: same seed + connectome + parameters + environment must reproduce
//  the same simulation, and a snapshot must be enough to resume one. These
//  tests are the EXECUTED half of that claim — the other half is the pinned
//  measurement in `tools/probe_snapshot_completeness.py`, which mirrors this in
//  Python. Neither half suffices alone: the Python side can print the window
//  arithmetic but never compiles Swift, and this side runs in CI but cannot
//  show the numbers behind a failure.
//
//  WHY THESE TESTS EXIST. `NeuralEngine` has had a snapshot since the first
//  commit and `testSnapshotRestoreRoundTrip` covers it — but that test compares
//  `spikeCount`, `currentTimeMs` and `simulationStep`, and none of those are
//  measured against a telemetry window. Every field that IS windowed had been
//  left out of the snapshot, and the test could not see it. So the snapshot was
//  free to be arbitrarily incomplete and stay green.
//

import XCTest
@testable import FlyBrainCore

final class EngineReplayTests: XCTestCase {

    /// The same chain the other engine tests use, with a seeded RNG, so a true
    /// run and a resumed run are genuinely equivalent inputs.
    private func makeEngine(seed: UInt64 = 11) -> NeuralEngine {
        var params = SimulationParameters()
        params.seed = seed
        return NeuralEngine(connectome: TestSupport.chainConnectome(count: 6),
                            parameters: params)
    }

    /// Kick neuron 0 hard on a schedule of ABSOLUTE step indices, so a resumed
    /// run receives exactly the stimulus the uninterrupted run received at that
    /// step. A schedule relative to the call would hand the resumed engine a
    /// different stimulus pattern and the comparison would be between two
    /// different experiments.
    private func drive(_ engine: NeuralEngine, steps: Int, from start: Int = 0) {
        for k in 0..<steps {
            if (k + start) % 37 == 0 {
                engine.injectCurrent(into: 0, current: 400,
                                     at: engine.currentTimeMs)
            }
            engine.step()
        }
    }

    /// The observables a resumed run must reproduce. Each is either measured
    /// against, or accumulated inside, a telemetry window.
    private func observables(_ e: NeuralEngine) -> [String: Double] {
        var out: [String: Double] = [
            "time": e.currentTimeMs,
            "spikeCount": Double(e.spikeCount),
            "spikesPerSecond": e.spikesPerSecond,
            "activeNeuronCount": Double(e.activeNeuronCount),
        ]
        for n in Int32(0)..<6 {
            out["recentSpikes\(n)"] = Double(e.recentSpikes(of: n))
            out["totalSpikes\(n)"] = Double(e.totalSpikes(of: n))
            out["rateHz\(n)"] = Double(e.rateHz(of: n))
        }
        return out
    }

    /// THE REGRESSION. A checkpoint taken past the 1 s telemetry boundary, then
    /// resumed in a fresh engine, must report the same observables as a run
    /// that was never interrupted.
    ///
    /// Before the fix this diverged on many of the observables:
    /// `totalSpikes` restarted from zero because `cumulativeSpikes` was not
    /// saved, and `spikesPerSecond` / `recentSpikes` were recomputed from
    /// windows whose start times were left at 0 against a restored clock, so
    /// the first resumed step rolled every window over at once.
    ///
    /// 11,000 steps x 0.1 ms = 1,100 ms of neural time, deliberately past the
    /// boundary: a checkpoint taken inside the first window cannot expose any of
    /// this, which is why the shipped test at 1,000 steps (100 ms) stayed green.
    func testResumePastTheWindowBoundaryReproducesEveryObservable() {
        let warm = 11_000
        let tail = 4_000

        let truth = makeEngine()
        drive(truth, steps: warm + tail)

        let source = makeEngine()
        drive(source, steps: warm)
        let snapshot = source.snapshot()

        let resumed = makeEngine()
        resumed.restore(snapshot)
        drive(resumed, steps: tail, from: warm)

        let a = observables(truth)
        let b = observables(resumed)
        for key in a.keys.sorted() {
            XCTAssertEqual(a[key]!, b[key]!, accuracy: 1e-6,
                           "\(key) diverged after a resume: uninterrupted "
                           + "\(a[key]!) vs resumed \(b[key]!)")
        }

        // The test must be able to fail, so assert the run produced something
        // to reproduce. A quiet engine would satisfy every comparison above for
        // free — the first version of the Python gate did exactly that and
        // reported a vacuous PASS.
        XCTAssertGreaterThan(a["spikeCount"]!, 0,
                             "the fixture fired no spikes, so this test proves "
                             + "nothing about resuming a live run")
        XCTAssertGreaterThan(a["recentSpikes0"]!, 0,
                             "the recent-spike ring is empty, so the window it "
                             + "lives in was never populated")
    }

    /// The fields must survive the round trip INDIVIDUALLY, not merely agree by
    /// coincidence after further stepping. Restoring with no steps in between
    /// isolates the transfer from any subsequent dynamics: a dropped field shows
    /// up here and nowhere else.
    func testSnapshotCarriesTheWindowStateItself() {
        let engine = makeEngine()
        drive(engine, steps: 11_000)

        let snapshot = engine.snapshot()
        let restored = makeEngine()
        restored.restore(snapshot)

        XCTAssertEqual(restored.currentTimeMs, engine.currentTimeMs)
        XCTAssertEqual(restored.spikeCount, engine.spikeCount)
        // These two are the ones that were silently reset. `spikesPerSecond` is
        // the last ROLLED window rate, so restoring it proves the window
        // bookkeeping travelled; `activeNeuronCount` depends on the per-neuron
        // bucket marks, so it proves those travelled too.
        XCTAssertEqual(restored.spikesPerSecond, engine.spikesPerSecond,
                       "the last rolled 1 s rate was not carried")
        XCTAssertEqual(restored.activeNeuronCount, engine.activeNeuronCount,
                       "the active-neuron bucket count was not carried, or the "
                       + "marks that decide it were reset to the sentinel")
        for n in Int32(0)..<6 {
            XCTAssertEqual(restored.recentSpikes(of: n), engine.recentSpikes(of: n),
                           "recent-spike window of neuron \(n) was not carried")
            XCTAssertEqual(restored.totalSpikes(of: n), engine.totalSpikes(of: n),
                           "cumulativeSpikes of neuron \(n) was not carried")
            XCTAssertEqual(restored.rateHz(of: n), engine.rateHz(of: n),
                           "the leaky rate estimate of neuron \(n) was not carried")
        }

        // The windows must still be OPEN at the checkpoint, or there would be
        // nothing to carry and the assertions above would be about zeros.
        XCTAssertLessThan(snapshot.windowStartTime, engine.currentTimeMs,
                          "the 1 s window is already closed at the checkpoint")
        XCTAssertLessThan(snapshot.recentWindowStartTime, engine.currentTimeMs,
                          "the recent-spike window is already closed")
        XCTAssertGreaterThan(snapshot.currentTimeMs, 1_000,
                             "the checkpoint must be past the 1 s boundary for "
                             + "the stale-window mechanism to be exercised")
    }

    /// Determinism proper (spec #63): two engines with the same seed, connectome
    /// and stimulus schedule must agree step for step. Separable from the
    /// snapshot bug — a snapshot can be perfect over a non-deterministic engine.
    func testSameSeedSameConnectomeSameStimulusIsBitIdentical() {
        let a = makeEngine(seed: 4_242)
        let b = makeEngine(seed: 4_242)
        for step in 0..<3_000 {
            if step % 37 == 0 {
                a.injectCurrent(into: 0, current: 400, at: a.currentTimeMs)
                b.injectCurrent(into: 0, current: 400, at: b.currentTimeMs)
            }
            a.step()
            b.step()
            if a.spikeCount != b.spikeCount {
                XCTFail("diverged at step \(step): \(a.spikeCount) vs "
                        + "\(b.spikeCount)")
                return
            }
        }
        XCTAssertEqual(a.spikeCount, b.spikeCount)
        XCTAssertEqual(a.currentTimeMs, b.currentTimeMs)
        XCTAssertGreaterThan(a.spikeCount, 0, "the pair never fired")
    }

    /// A checkpoint taken mid-window must resume mid-window. Stepping once past
    /// a restore must CONTINUE the 1 s window, not end it: if the window start
    /// were dropped, that single step would see a window older than the whole
    /// run and roll it immediately, changing `spikesPerSecond`.
    func testOneStepPastARestoreDoesNotRollTheWindow() {
        let engine = makeEngine()
        drive(engine, steps: 2_000)          // 200 ms: inside the 1 s window
        let snapshot = engine.snapshot()

        XCTAssertGreaterThan(snapshot.spikeEventsThisWindow, 0,
                             "nothing had been accumulated to carry")
        XCTAssertLessThan(snapshot.windowStartTime, snapshot.currentTimeMs,
                          "the window is already closed, so there is nothing to "
                          + "continue")

        let restored = makeEngine()
        restored.restore(snapshot)
        XCTAssertEqual(restored.spikesPerSecond, snapshot.lastWindowSpikesPerSecond)

        // One step: the window is still the same window, so the last rolled
        // rate is unchanged. (The next roll is still ~800 ms away.)
        let beforeTime = restored.currentTimeMs
        restored.step()
        XCTAssertEqual(restored.spikesPerSecond, snapshot.lastWindowSpikesPerSecond,
                       "one step past a restore rolled the 1 s window "
                       + "(clock \(beforeTime) -> \(restored.currentTimeMs))")
    }
}