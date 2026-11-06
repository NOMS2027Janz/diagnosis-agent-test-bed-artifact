#!/bin/bash
# The LLM-built graph pilot, steps 3 and 4, following the semantic reconciliation study: the vendors' lifted models
# (surface from the extracted manuals, then the study's lift), and the study's reconciliation of three pairs under
# its three reference conditions (API; about 15 minutes; under $10).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
mkdir -p results
python knowledge_pilot/run_lift.py 2>&1 | tee results/pilot_lift.log && \
python knowledge_pilot/run_reconcile.py 2>&1 | tee results/pilot_reconcile2.log
echo "pilot steps 3 and 4 done (exit $?)"
