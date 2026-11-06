#!/bin/bash
# The LLM-built graph pilot on the held-out vendor VC (manuals written after surface v3 was fixed): extraction, then
# the lift with surface v2 and with surface v3, then reconciliation of VC with the operator (three reference
# conditions) and of VA with VC (no reference), for each lift. API; about 30 to 40 minutes; under $10.
# Safe to run beside the record run (it touches no agent code).
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
L=results/pilot_vc.log
python knowledge_pilot/run_extract.py --only VC 2>&1 | tee -a $L && \
python knowledge_pilot/run_lift.py --only VC --tag _v2 2>&1 | tee -a $L && \
python knowledge_pilot/run_lift.py --only VC --surface v3 --tag _v3 2>&1 | tee -a $L && \
for T in _v2 _v3; do
  python knowledge_pilot/run_reconcile.py --tag $T --pairs VC-op --conditions none,constructed,given 2>&1 | tee -a $L
  python knowledge_pilot/run_reconcile.py --tag $T --pairs VA-VC --conditions none 2>&1 | tee -a $L
done
echo "pilot VC done (exit $?)" | tee -a $L
