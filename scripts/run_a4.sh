#!/bin/bash
# Amendment 4: the clean rerun of every Experiment 2 offline arm (no API calls). Run from anywhere:
#     bash scripts/run_a4.sh
# Order: the confirmation set's arm A first (the model comparison waits for it), then the rest of the confirmation
# set, then the original set. Arm A runs through the provenance analysis, which also writes A's result rows.
# Every step resumes where it stopped if the script is run again.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a4
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A4.json || { echo "code differs from the amendment 4 manifest: stopped"; exit 1; }
W=${A4_WORKERS:-6}
caffeinate -i python scripts/analysis_provenance.py --seeds 2160-2319 --workers $W --out results/a4/provenance_confirm --row-out results/a4/confirm 2>&1 | tee -a results/a4_confirm_A.log
caffeinate -i python scripts/exp2_run.py --arms B,A-G,G,A-ts0,G-no-twin --seeds 2160-2319 --workers $W --out results/a4/confirm 2>&1 | tee -a results/a4_confirm.log
caffeinate -i python scripts/analysis_provenance.py --seeds 2000-2159 --workers $W --out results/a4/provenance_orig --row-out results/a4/orig 2>&1 | tee -a results/a4_orig_A.log
caffeinate -i python scripts/exp2_run.py --arms B,A-once,A-no-twin,A-no-c1,A-twin-eq,A-perfect,A-2x,A-uncal,A-rc,B-graph --seeds 2000-2159 --workers $W --out results/a4/orig 2>&1 | tee -a results/a4_orig.log
python scripts/analysis_exp2.py > /dev/null && echo "analysis written: results/a4/ANALYSIS.md"
echo "amendment 4 offline chain finished"
