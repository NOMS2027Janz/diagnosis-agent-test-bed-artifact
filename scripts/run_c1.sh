#!/bin/bash
# Campaign to the NOMS 2027 paper, stage 1, part 1 (spec/PROTOCOL_CAMPAIGN.md, section 3). No API calls.
#   1. the "can this decide?" step: A-full-cd and A-full-cd-fut on the diagnosis cases (results/x3/exp2b, beside
#      A-full and A-full-fut); A-tr-cd and A-tr-cd-fut on the fresh cases (results/x3/fresh, beside A-tr, A-tr-fut);
#   2. alarm correlation alone: AC and AC-fut on both case sets;
#   3. the twin with structural error: A-full-tm1, B-tm1, then A-full-tm2, B-tm2 on the diagnosis cases
#      (results/campaign/tm).
# Run from anywhere:   bash scripts/run_c1.sh        Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
mkdir -p results/campaign
LOG=results/campaign/run_c1.log
W=${C1_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C1.json || { stamp "code differs from the stage 1 manifest: stopped"; exit 1; }
pgrep -f "run_x3(_tr|_full)?[.]sh" >/dev/null && { stamp "an Experiment 3 part is still running: stop it first"; exit 1; }

if [ ! -f results/campaign/tests_passed_c1 ]; then
  stamp "tests"
  python -m pytest -q tests 2>&1 | tail -n 5 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  touch results/campaign/tests_passed_c1
fi

run () {   # $1 arms, $2 seeds, $3 output directory
  stamp "start $1 on $2"
  caffeinate -i python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out "$3" 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $2"
}

run A-full-cd,A-full-cd-fut 2000-2319 results/x3/exp2b
run A-tr-cd,A-tr-cd-fut 3000-3319 results/x3/fresh
run AC,AC-fut 2000-2319 results/campaign/ac
run AC,AC-fut 3000-3319 results/campaign/ac
run A-full-tm1,B-tm1 2000-2319 results/campaign/tm
run A-full-tm2,B-tm2 2000-2319 results/campaign/tm
stamp "stage 1, part 1, finished"
