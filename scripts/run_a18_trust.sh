#!/bin/bash
# Amendment 18 development, second step: trust rules for added modes (scripts/a18_run.py --trust). No API calls.
# The first development step showed gap filling alone recovers the signature degradation fully (34 of 40, as the
# oracle) while the added modes cost cases (sig with additions 29, complete documentation with additions 32 of 34).
# Two rules, each on complete documentation, signatures removed and modes removed, cases 3000-3039, development draw:
#   sibling: add only modes the documentation as held gives for another type of the same element class;
#   graded:  add those at half the stated rate and the others at a twentieth of it.
# Every step skips finished cases. Run from anywhere:   caffeinate -is bash scripts/run_a18_trust.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=results/campaign/run_a18_trust.log
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4H.json || { stamp "code differs from the C4H manifest: stopped"; exit 1; }
for t in sibling graded; do for k in modes none sig; do
  stamp "start a18 A-full-cd-kg-$k-comp dev trust $t"
  python scripts/a18_run.py --arms A-full-cd-kg-$k-comp --seed dev --trust $t --seeds 3000-3039 --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
done; done
stamp "all finished"
