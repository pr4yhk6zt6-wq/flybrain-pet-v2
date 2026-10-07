#!/usr/bin/env python3
"""Is `input_neuropils[0]` really the neuron's dominant neuropil?

flybrain/banc.py's `_dominant_region` claims it picks "the dominant
(highest-synapse) tag" but it actually weights every tag as 1, so it returns
`input_neuropils[0]` (the input list wins over the output list). That is only
the dominant neuropil if the release's lists are ordered by descending synapse
count. The connections file carries a per-connection `neuropil` column with the
same 111-tag vocabulary, so the counts are measurable: for a sample of neurons,
aggregate their incoming synapses per tag and compare the argmax against the
first-listed tag.
"""
from __future__ import annotations

import gzip
import pickle
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/raw/flywire/banc"
SAMPLE = 20000


def main() -> int:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    with gzip.open(DATA / "neuron_attributes.pickle.gz", "rb") as fh:
        attrs = pickle.load(fh)

    rng = random.Random(seed)
    ids = list(attrs)
    sample = set(rng.sample(ids, min(SAMPLE, len(ids))))

    counted: dict[int, Counter] = defaultdict(Counter)
    rows = 0
    with gzip.open(DATA / "connections_princeton.csv.gz", "rt", newline="") as fh:
        for line in fh:
            a = line.find(","); b = line.find(",", a + 1)
            c = line.find(",", b + 1); d = line.find(",", c + 1)
            if d < 0:
                continue
            rows += 1
            try:
                post = int(line[a + 1:b])
                syn = int(line[c + 1:d])
            except ValueError:
                continue
            if post in sample:
                counted[post][line[b + 1:c].strip()] += syn
    print(f"rows scanned          : {rows}")
    print(f"sampled neurons       : {len(sample)}")
    print(f"with measured synapses: {len(counted)}")

    agree_in = agree_out = 0
    n = 0
    no_counts = 0
    desc_input = desc_output = 0
    multi = 0
    for rid, c in counted.items():
        a = attrs[rid]
        ins = a.get("input_neuropils") or []
        outs = a.get("output_neuropils") or []
        if not ins and not outs:
            continue
        n += 1
        top = c.most_common(1)[0][0]
        if ins and ins[0] == top:
            agree_in += 1
        if outs and outs[0] == top:
            agree_out += 1
        if not ins:
            no_counts += 1
        # is the input list itself sorted by the measured counts (descending)?
        if len(ins) > 1:
            multi += 1
            vals = [c.get(t, 0) for t in ins]
            if all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)):
                desc_input += 1
        if len(outs) > 1:
            vals = [c.get(t, 0) for t in outs]
            if all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)):
                desc_output += 1
    print(f"\ncompared              : {n}")
    print(f"input_neuropils[0] == measured argmax : {agree_in} "
          f"({100*agree_in/max(n,1):.1f}%)")
    print(f"output_neuropils[0] == measured argmax: {agree_out} "
          f"({100*agree_out/max(n,1):.1f}%)")
    print(f"neurons with no input list            : {no_counts}")
    print(f"\nmulti-tag input lists   : {multi}, descending by measured count: "
          f"{desc_input} ({100*desc_input/max(multi,1):.1f}%)")
    print(f"multi-tag output lists  : descending: {desc_output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())