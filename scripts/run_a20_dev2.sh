#!/bin/bash
# Amendment 20 development, step 2 (no API). The completed arms again, with --nodup (scripts/a20b_run.py): an
# unattested addition on one-of-a-kind equipment (fibre spans, OMS inputs) is dropped when directly connected
# equipment already documents the same fault mode, or when the completion adds it there with higher stated confidence.
# Development cases 3000-3079, draw dev4, as step 1. Run from anywhere:   caffeinate -is bash scripts/run_a20_dev2.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=results/campaign/run_a20_dev2.log
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4H.json || { stamp "code differs from the C4H manifest: stopped"; exit 1; }
for k in modes sig none; do
  stamp "start a20b A-full-cd-kg-$k-comp dev4 nodup"
  python scripts/a20b_run.py --arms A-full-cd-kg-$k-comp --seed dev4 --nodup --seeds 3000-3079 --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
done
stamp "all finished"
