#!/bin/bash
# Campaign to the NOMS 2027 paper: the capability ladder (spec/PROTOCOL_CAMPAIGN.md, amendment "the capability
# ladder"). No API calls. Each rung adds one telemetry capability to the world and its documented use to the agent,
# on the future instrumentation branch; results under results/campaign/ladder, paired with A-full-cd-fut
# (results/x3/exp2b) and B-fut (results/x3/exp2).
#   1. L1 stream liveness: A-L1 and B-L1 on the 320 diagnosis cases (seeds 2000-2319);
#   2. every rung: A-Lall and B-Lall on the 320 diagnosis cases;
#   3. each further rung alone, A-L2 .. A-L6, on the diagnosis cases of the kinds it can affect
#      (adagent.harness.exp2.LADDER_KINDS; kinds cycle by seed; L6 on the monitoring faults only).
# Optional control (C2_CONTROL=1): A-full-cd-fut and B-fut rerun under this code into results/campaign/ladder, to
# confirm case for case that the arms without a rung are unchanged.
# Run from anywhere:   bash scripts/run_c2_ladder.sh        Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
OUT=results/campaign/ladder
mkdir -p $OUT
LOG=$OUT/run_c2.log
W=${C2_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C2.json || { stamp "code differs from the ladder manifest: stopped"; exit 1; }
pgrep -f "run_c1b?[.]sh" >/dev/null && { stamp "a stage 1 part is still running: stop it first"; exit 1; }

if [ ! -f $OUT/tests_passed_c2 ]; then
  stamp "tests"
  python -m pytest -q tests 2>&1 | tail -n 5 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  touch $OUT/tests_passed_c2
fi

seeds () {   # the diagnosis cases of the kinds rung $1 can affect
  python -c "import sys; sys.path.insert(0, 'src'); from adagent.harness.exp2 import ladder_seeds; print(','.join(map(str, ladder_seeds('$1'))))"
}
run () {   # $1 arms, $2 seeds, $3 label
  stamp "start $1 on $3"
  caffeinate -i python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out $OUT 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $3"
}

if [ "${C2_CONTROL:-0}" = "1" ]; then
  run A-full-cd-fut,B-fut 2000-2319 "the diagnosis cases (control)"
fi
run A-L1,B-L1 2000-2319 "the diagnosis cases"
run A-Lall,B-Lall 2000-2319 "the diagnosis cases"
for k in L2 L3 L5 L4 L6; do
  run A-$k "$(seeds $k)" "the kinds $k can affect"
done
stamp "the capability ladder, finished"
