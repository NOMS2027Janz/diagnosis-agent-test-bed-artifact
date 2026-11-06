#!/bin/bash
# The LLM-built graph pilot, steps 3 and 4 again with the second surface prompt (wordings merged more fully), into
# knowledge_pilot/out/lifted_v2 and reconciled_v2; the first version is kept for comparison (API; about 20 minutes;
# under $10).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
python knowledge_pilot/run_lift.py --tag _v2 2>&1 | tee results/pilot_lift_v2.log && \
python knowledge_pilot/run_reconcile.py --tag _v2 2>&1 | tee results/pilot_reconcile_v2.log
echo "pilot v2 done (exit $?)"
