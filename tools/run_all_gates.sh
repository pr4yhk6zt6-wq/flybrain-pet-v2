#!/bin/sh
# Run every Python gate the CI workflow runs, in CI order, and report failures.
cd "$(dirname "$0")/.."
fail=0
python3 - <<'PY' > /tmp/gates.txt
import re
src = open(".github/workflows/ci.yml").read()
# multi-line `run: |` step bodies
for m in re.finditer(r"^\s+- name: (.*)$\n\s+run: \|\n((?:\s{8,}.*\n)+)", src, re.M):
    for line in m.group(2).splitlines():
        line = line.strip()
        if line.startswith("python"):
            print(line)
# single-line `run: python ...` steps
for line in src.splitlines():
    s = line.strip()
    if s.startswith("run: python"):
        print(s[len("run: "):])
PY
while IFS= read -r cmd; do
    [ -z "$cmd" ] && continue
    out=$(sh -c "$cmd" 2>&1); rc=$?
    if [ $rc -ne 0 ]; then
        echo "FAIL($rc) $cmd"
        echo "$out" | tail -6 | sed 's/^/     /'
        fail=1
    else
        echo "ok   $cmd"
    fi
done < /tmp/gates.txt
echo "TOTAL_FAIL=$fail"
exit "$fail"