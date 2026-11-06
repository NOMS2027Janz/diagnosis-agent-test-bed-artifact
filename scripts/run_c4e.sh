#!/bin/bash
# Amendment 12 (spec/PROTOCOL_CAMPAIGN.md): the port-monitoring fix. Under spec/CAMPAIGN_MANIFEST_C4E.json, on the
# sealed cases 7000-7319, in order:
#   1. the ladder tests;
#   2. the earlier A-Lall results are moved to results/campaign/c4_superseded/A-Lall (not reported; kept);
#   3. the LLM-orchestrated agent (L-full) on the sealed cases A-full-cd had to plan (API, up to $100); it does not
#      read the port monitors, so the fix does not touch it;
#   4. the agent with every capability (A-Lall), rerun with the fix (no API).
# Every other C4 arm stands as run. Run from anywhere:   caffeinate -is bash scripts/run_c4e.sh
# Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
LOG=$OUT/run_c4e.log
W=${REC_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4E.json || { stamp "code differs from the C4E manifest: stopped"; exit 1; }
if [ ! -f $OUT/c4e_tests_passed ]; then
  stamp "ladder tests"
  python -m pytest -q tests/test_ladder.py 2>&1 | tail -n 2 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  touch $OUT/c4e_tests_passed
fi
if [ ! -f $OUT/c4e_moved ]; then
  mkdir -p results/campaign/c4_superseded
  [ -d $OUT/A-Lall ] && mv $OUT/A-Lall results/campaign/c4_superseded/A-Lall
  touch $OUT/c4e_moved
  stamp "earlier A-Lall moved to results/campaign/c4_superseded"
fi
run () {   # $1 arms, $2 label, $3 extra flags
  stamp "start $1 on $2"
  python scripts/exp2_run.py --arms "$1" --seeds 7000-7319 --workers $W --out $OUT $3 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $2"
}
run L-full "the sealed cases A-full-cd planned, LLM orchestrator" "--only-planned --plan-arm A-full-cd"
run A-Lall "the sealed cases, every capability, with the fix"
stamp "amendment 12 runs finished"
