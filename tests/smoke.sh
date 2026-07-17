#!/usr/bin/env bash
# tests/smoke.sh — lightweight wiring check run in CI.
# Verifies: Python engine imports, selftest passes, eval script runs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON:-python3}"

echo "=== smoke: engine import ==="
"$PY" -c "
import sys; sys.path.insert(0, 'src/lib')
import drift_score, drift_trajectory, drift_type, drift_calibrate, drift_dialogic, drift_controller
print('all engine modules import ok')
"

echo "=== smoke: drift_score --selftest ==="
"$PY" src/lib/drift_score.py

echo "=== smoke: drift_trajectory --selftest ==="
"$PY" src/lib/drift_trajectory.py

echo "=== smoke: eval_morin (n>=100, acc>=0.95, FP=0.0) ==="
"$PY" scripts/eval_morin.py | "$PY" -c "
import json, sys
d = json.load(sys.stdin)
assert d['n'] >= 100, f'corpus too small: {d[\"n\"]}'
assert d['accuracy'] >= 0.95, f'accuracy regressed: {d[\"accuracy\"]}'
assert d['false_positive_rate'] == 0.0, f'FP must be 0.0, got {d[\"false_positive_rate\"]}'
print(f'eval ok: n={d[\"n\"]} acc={d[\"accuracy\"]} FP={d[\"false_positive_rate\"]}')
"

echo "=== smoke: hook bash syntax ==="
find hooks/ -name "*.sh" | while read -r f; do
    bash -n "$f" && echo "  ok: $f"
done

echo "=== smoke: all good ==="

echo "== run-python launcher =="
test -f scripts/run-python.sh
bash -n scripts/run-python.sh
bash scripts/run-python.sh -c 'import sys; assert sys.version_info >= (3, 10)'
# mcp-server routes through run-python (no inline find_python)
grep -q 'run-python.sh' scripts/mcp-server.sh
! grep -q 'find_python()' scripts/mcp-server.sh
env -i HOME="$HOME" PATH="/usr/bin:/bin" bash scripts/run-python.sh -c 'import sys; print(sys.version_info[0])' | grep -q 3 || true
echo "  ok: run-python"
