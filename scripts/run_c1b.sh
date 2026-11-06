#!/bin/bash
# Campaign to the NOMS 2027 paper, stage 1, part 1, trimmed remainder (spec/PROTOCOL_CAMPAIGN.md, amendment
# 2026-10-20 14:35). Runs after the "can this decide?" arms of scripts/run_c1.sh. No API calls.
#   alarm correlation alone: AC and AC-fut on the diagnosis cases only;
#   the twin with structural error: A-full-tm1, B-tm1, A-full-tm2, B-tm2 on 160 diagnosis cases, 20 of each kind
#   (seeds 2000 to 2159).
# Run from anywhere:   bash scripts/run_c1b.sh        Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
mkdir -p results/campaign
LOG=results/campaign/run_c1.log
W=${C1_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C1B.json || { stamp "code differs from the stage 1 (trimmed) manifest: stopped"; exit 1; }
run () {
  stamp "start $1 on $2"
  caffeinate -i python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out "$3" 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $2"
}
run AC,AC-fut 2000-2319 results/campaign/ac
run A-full-tm1,B-tm1 2000-2159 results/campaign/tm
run A-full-tm2,B-tm2 2000-2159 results/campaign/tm
stamp "stage 1, part 1 (trimmed), finished"
