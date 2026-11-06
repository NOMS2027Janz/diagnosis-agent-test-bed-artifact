#!/bin/bash
# The LLM-built graph pilot, steps 3 and 4 with surface v3 (wordings grouped by category, groups reviewed, relations
# built from the extracted record), into knowledge_pilot/out/lifted_v3 and reconciled_v3; v1 and v2 are kept for
# comparison. API; about 30 to 45 minutes; under $10. Safe to run beside the record run (it touches no agent code).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
python knowledge_pilot/run_lift.py --surface v3 --tag _v3 2>&1 | tee results/pilot_lift_v3.log && \
python knowledge_pilot/run_reconcile.py --tag _v3 2>&1 | tee results/pilot_reconcile_v3.log
echo "pilot v3 done (exit $?)"
