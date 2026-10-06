#!/bin/sh
# Generate the synthetic demo dataset and validate it.
set -e
cd "$(dirname "$0")/.."

PY=python3
echo "== pipeline tests =="
$PY -m pytest python/tests -q

echo "== generate demo asset =="
$PY tools/gen_synth.py data/generated/demo_micro.fbpack

echo "== validate asset =="
$PY tools/tests/validate_fbpack.py data/generated/demo_micro.fbpack

echo "OK: synthetic dataset pipeline passed"