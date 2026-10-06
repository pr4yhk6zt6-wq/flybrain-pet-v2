#!/bin/sh
# Full repo health check: Python tests + asset validation + Swift static check.
set -e
cd "$(dirname "$0")/.."

echo "== python pipeline tests =="
python3 -m pytest python/tests -q

echo "== regenerate assets =="
python3 tools/gen_synth.py data/generated/demo_micro.fbpack >/dev/null
python3 tools/tests/validate_fbpack.py data/generated/demo_micro.fbpack

echo "== swift source present =="
ls ios/Sources/FlyBrainCore/*.swift | wc -l | xargs echo "swift files:"
echo "ALL CHECKS PASSED"