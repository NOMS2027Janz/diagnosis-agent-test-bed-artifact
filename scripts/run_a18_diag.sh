#!/bin/bash
# Amendment 18, the frozen method (trust rule "sibling", frozen 2 Nov 2026 on development cases 3000-3039) on the
# diagnosis cases. No API calls; the completions were generated once (results/campaign/a18/completion_*_diag.json).
# 1. Signatures removed and modes removed, completed, status quo, cases 2000-2159 (against kgx_c4: degraded and control).
# 2. Condition 2: what the rule still adds to complete documentation (11 modes), case by case on 2000-2319, status quo
#    (against results/c4d/A-full-cd) and future instrumentation (against results/campaign/c4_dev/A-full-cd-fut).
# Every step skips finished cases. Run from anywhere:   caffeinate -is bash scripts/run_a18_diag.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=results/campaign/run_a18_diag.log
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4H.json || { stamp "code differs from the C4H manifest: stopped"; exit 1; }
a18 () {  # arms seeds
  stamp "start a18 $1 diag sibling on $2"
  python scripts/a18_run.py --arms "$1" --seed diag --trust sibling --seeds "$2" --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
}
a18 A-full-cd-kg-sig-comp 2000-2159
a18 A-full-cd-kg-modes-comp 2000-2159
a18 A-full-cd-kg-none-comp 2000-2319
a18 A-full-cd-fut-kg-none-comp 2000-2319
stamp "all finished"
