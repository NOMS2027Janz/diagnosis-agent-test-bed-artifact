#!/bin/bash
# Spectral monitoring (docs/campaign/spectral_design.md): one portion a night, each under 8 h at the upper end.
# Spectral mode for the whole run (ADAGENT_SPECTRAL=1): cascade filtering in the world and the twin, the new fault
# modes documented. The filter-focused case set, 160 cases, seeds 5000-5159 (development 5900-5915). No API calls.
#   bash scripts/run_spectral.sh <portion>        portions 1 to 4; resumes where it stopped if run again
#   1: A-sp (the agent in spectral mode, no new instrument) and A-S (span-end spectra)
#   2: A-R (receiver shape with the learned penalty model) and the manual arms B-sp, B-SR
#   3: A-SR (both)
#   4: A-R-nopm (receiver shape without the penalty model)
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_SPECTRAL=1
OUT=results/campaign/spectral
mkdir -p $OUT
LOG=$OUT/run_spectral.log
W=${SP_WORKERS:-6}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C3.json || { stamp "code differs from the C3 manifest: stopped"; exit 1; }
if [ ! -f $OUT/tests_passed ]; then
  stamp "tests"
  # the suite tests the default mode (tests/test_spectral.py sets spectral mode itself), so it runs with the flag off;
  # in spectral mode the new fault modes add look-alikes (a filter shift's class grows from 2 to 3), by design
  env -u ADAGENT_SPECTRAL python -m pytest -q tests 2>&1 | tail -n 5 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  touch $OUT/tests_passed
fi
run () {
  stamp "start $1"
  caffeinate -is python scripts/exp2_run.py --arms "$1" --seeds 5000-5159 --workers $W --out $OUT 2>&1 | tee -a $LOG \
    | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1"
}
case "$1" in
  1) run A-sp,A-S ;;
  2) run A-R,B-sp,B-SR ;;
  3) run A-SR ;;
  4) run A-R-nopm ;;
  *) echo "portion 1 to 4"; exit 1 ;;
esac
