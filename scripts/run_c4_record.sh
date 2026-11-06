#!/bin/bash
# Campaign to the NOMS 2027 paper under C4 (spec/PROTOCOL_CAMPAIGN.md, amendments 8 and 9): the record run of the
# fixed agent on the new sealed set (seeds 7000-7319), never seen in development. Code as frozen under
# spec/CAMPAIGN_MANIFEST_C4.json. Results under results/campaign/c4. In order:
#   0. the full test suite, once;
#   1. a smoke test of the LLM orchestrator on the full agent (arm L-full) on six development cases (API, up to $8);
#   2. the agent with the "can this decide?" step and manual troubleshooting, status quo: A-full-cd and B (no API);
#   3. the LLM-orchestrated agent (L-full) on the sealed cases A-full-cd had to plan (API, up to $100), only if the
#      smoke test ran without error;
#   4. alarm correlation alone on both branches: AC and AC-fut;
#   5. every capability of the ladder, future branch: A-Lall and B-Lall.
# The future-branch arms without the ladder (A-full-cd-fut, B-fut) and the LLM proposer follow on a later night.
# Run from anywhere:   caffeinate -is bash scripts/run_c4_record.sh      Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
mkdir -p $OUT
LOG=$OUT/run_c4_record.log
W=${REC_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4.json || { stamp "code differs from the C4 manifest: stopped"; exit 1; }

if [ ! -f $OUT/tests_passed ]; then
  stamp "tests"
  python -m pytest -q tests 2>&1 | tail -n 5 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  touch $OUT/tests_passed
fi

run () {   # $1 arms, $2 seeds, $3 label, $4 extra flags
  stamp "start $1 on $3"
  python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out $OUT $4 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $3"
}

if [ ! -f $OUT/smoke_ok ]; then
  stamp "start L-full smoke test on six development cases"
  python scripts/exp2_run.py --arms L-full --seeds 2004,2013,2017,2021,2028,2029 --workers 3 --out $OUT/smoke 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  n=$(ls $OUT/smoke/L-full/s*.json 2>/dev/null | grep -vc error)
  e=$(ls $OUT/smoke/L-full/*.error.json 2>/dev/null | wc -l | tr -d " ")   # macOS pads wc (amendment 12)
  stamp "smoke test: $n results, $e errors"
  [ "$n" = "6" ] && [ "$e" = "0" ] && touch $OUT/smoke_ok
fi

run A-full-cd,B 7000-7319 "the sealed cases, status quo"
if [ -f $OUT/smoke_ok ]; then
  run L-full 7000-7319 "the sealed cases A-full-cd planned, LLM orchestrator" "--only-planned --plan-arm A-full-cd"
else
  stamp "L-full skipped: the smoke test did not pass"
fi
run AC,AC-fut 7000-7319 "the sealed cases, alarm correlation"
run A-Lall,B-Lall 7000-7319 "the sealed cases, every capability"
stamp "the C4 record run (night 1), finished"
