#!/bin/sh
# Run every gate the CI workflow runs, in CI order, and report failures.
#
# This runner exists so a local tree can reproduce CI's verdict. It therefore
# has to run the SAME commands CI does — including the `sh tools/...` mutation
# proofs, which an earlier version silently skipped by only collecting lines
# that start with "python". A runner that executes a subset of CI is worse than
# no runner: it reports green for a tree CI would fail.
cd "$(dirname "$0")/.."
fail=0
python3 - <<'PY' > /tmp/gates.txt
import re
src = open(".github/workflows/ci.yml").read()
# multi-line `run: |` step bodies
for m in re.finditer(r"^\s+- name: (.*)$\n\s+run: \|\n((?:\s{8,}.*\n)+)", src, re.M):
    for line in m.group(2).splitlines():
        line = line.strip()
        if line.startswith(("python", "sh tools/")):
            print(line)
# single-line `run: python ...` steps
for line in src.splitlines():
    s = line.strip()
    if s.startswith("run: python"):
        print(s[len("run: "):])
PY
while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    t0=$(date +%s)
    out=$(sh -c "$cmd" 2>&1); rc=$?
    t1=$(date +%s)
    if [ $rc -ne 0 ]; then
        echo "FAIL($rc) [$((t1-t0))s] $cmd"
        echo "$out" | tail -6 | sed 's/^/     /'
        fail=1
    else
        echo "ok   [$((t1-t0))s] $cmd"
    fi
done < /tmp/gates.txt
echo "TOTAL_FAIL=$fail"
exit "$fail"