#!/bin/bash
# Amendment 16 (spec/PROTOCOL_CAMPAIGN.md): the LLM framer end to end, interpreting the question, expressing the
# answer. Checks first (full test suite; identity of A-full-cd with results/c4d; smoke tests of each part), then the
# freeze (spec/CAMPAIGN_MANIFEST_C4H.json), then the runs. Stops at the first failure.
# Run from anywhere:   caffeinate -is bash scripts/run_c4h.sh      Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
LOG=$OUT/run_c4h.log
W=${REC_WORKERS:-6}
M=spec/CAMPAIGN_MANIFEST_C4H.json
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

if [ ! -f $M ]; then
  stamp "checks: full test suite"
  python -m pytest -q tests 2>&1 | tail -n 3 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  stamp "checks: identity of A-full-cd with results/c4d"
  python scripts/identity_c4f.py --seeds 2004,2013,2017,2021,2028,2029 --workers $W 2>&1 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "identity check failed: stopped"; exit 1; }
  stamp "smoke: A-full-cd-lf on three development cases"
  python scripts/exp2_run.py --arms A-full-cd-lf --seeds 2001,2004,2013 --workers 3 --out $OUT/smoke_lf 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  n=$(ls $OUT/smoke_lf/A-full-cd-lf/s*.json 2>/dev/null | grep -vc error | tr -d " ")
  stamp "smoke framer: $n of 3"
  [ "$n" = "3" ] || { stamp "smoke test failed: stopped"; exit 1; }
  stamp "smoke: question reading and answer writing on three sealed cases"
  python scripts/qi_run.py --seeds 7000-7002 --workers 3 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished|qi s"
  python scripts/ae_run.py --seeds 7000-7002 --workers 3 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished|ae s"
  q=$(ls $OUT/qi/s*.json 2>/dev/null | grep -vc error | tr -d " "); a=$(ls $OUT/ae/s*.json 2>/dev/null | grep -vc error | tr -d " ")
  stamp "smoke: question $q of 3, answer $a of 3"
  { [ "$q" = "3" ] && [ "$a" = "3" ]; } || { stamp "smoke test failed: stopped"; exit 1; }
  python scripts/exp2_freeze.py --manifest $M && stamp "frozen: $M"
fi
python scripts/exp2_freeze.py --check --manifest $M || { stamp "code differs from the C4H manifest: stopped"; exit 1; }
stamp "start the answer writer on 7000-7079"
python scripts/ae_run.py --seeds 7000-7079 --workers $W 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "start question reading on 7000-7319"
python scripts/qi_run.py --seeds 7000-7319 --workers $W 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "start A-full-cd-lf on 7000-7079"
python scripts/exp2_run.py --arms A-full-cd-lf --seeds 7000-7079 --workers $W --out $OUT 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "amendment 16 runs finished"
