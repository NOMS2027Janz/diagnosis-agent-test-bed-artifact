#!/bin/bash
# Amendment 22: the twin with structural error, rerun with the mismatch carried to the case-day network.
# Four arms on the 160 diagnosis cases 2000-2159 (20 of each kind), status-quo instruments. Every step skips finished
# work, so the command can be rerun after an interruption. Run from anywhere:   caffeinate -is bash scripts/run_c4t.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
# a fresh memo of equivalence fits: the memo's version does not cover history.py, so the C4H fits of these mismatched
# worlds (computed with the fault) must not be reused
export EXP2_FIT_CACHE="$PWD/results/_fitcache_c4t"
LOG=results/campaign/run_c4t.log
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4T.json || { stamp "code differs from the C4T manifest: stopped"; exit 1; }
for arm in B-tm1 B-tm2 A-full-cd-tm1 A-full-cd-tm2; do
  stamp "start $arm"
  python scripts/exp2_run.py --arms $arm --seeds 2000-2159 --out results/campaign/c4t --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
done
stamp "all finished"
