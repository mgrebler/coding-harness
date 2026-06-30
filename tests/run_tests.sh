#!/usr/bin/env bash
# Run all regression test suites.
# Usage:
#   bash tests/run_tests.sh             # unit + evals (requires Ollama)
#   bash tests/run_tests.sh --skip-evals  # unit only, fast
#   OLLAMA_MODEL=llama3.2 bash tests/run_tests.sh

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0
FAIL=0
SKIP_EVALS=false

for arg in "$@"; do
  [[ "$arg" == "--skip-evals" ]] && SKIP_EVALS=true
done

run_suite() {
  local name="$1"
  local cmd="$2"
  echo ""
  echo "=== $name ==="
  if eval "$cmd"; then
    echo "  PASSED: $name"
    PASS=$((PASS + 1))
  else
    echo "  FAILED: $name"
    FAIL=$((FAIL + 1))
  fi
}

run_suite "unit" "python3 -m unittest discover -s $REPO/tests/unit -p 'test_*.py' -v"

if [[ "$SKIP_EVALS" == false ]]; then
  run_suite "evals" "python3 -m unittest discover -s $REPO/tests/evals -p 'test_*_eval.py' -v"
else
  echo ""
  echo "=== evals (skipped) ==="
fi

echo ""
echo "Results: $PASS suite(s) passed, $FAIL suite(s) failed."
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
