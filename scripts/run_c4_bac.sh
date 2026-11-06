#!/bin/bash
# Amendment 11: today's practice as handed off. Arm B-ac (the procedure for alarm correlation's root alarm, started at
# its unit and followed as arm B follows its procedures) and B-ac-fut, on the sealed cases 7000-7319, under the C4
# manifest. No API. Results under results/campaign/c4, log results/campaign/c4/run_c4_bac.log. Resumes if run again.
# Run from anywhere:   caffeinate -is bash scripts/run_c4_bac.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
mkdir -p $OUT
LOG=$OUT/run_c4_bac.log
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4.json || { echo "code differs from the C4 manifest: stopped" | tee -a $LOG; exit 1; }
python -m pytest -q tests/test_baseline_ac.py 2>&1 | tail -n 2 | tee -a $LOG
[ "${PIPESTATUS[0]}" = "0" ] || { echo "B-ac tests failed: stopped" | tee -a $LOG; exit 1; }
python scripts/exp2_run.py --arms B-ac,B-ac-fut --seeds 7000-7319 --workers ${REC_WORKERS:-6} --out $OUT 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
echo "B-ac on the sealed cases: finished" | tee -a $LOG
