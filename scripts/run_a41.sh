#!/bin/bash
# Amendment 4.1: the documented reading of checks corrected (a negative finding fits a contender elsewhere).
# Brings every agent arm to the 4.1 freeze by rerunning only the cases that reached the planner or a check; cases the
# agent committed at once are unchanged and copied. Baseline arms are copied whole. Arm L is rerun separately
# (scripts/run_a41_models.sh). Run from anywhere, after run_a4.sh has finished:
#     bash scripts/run_a41.sh
# Every step resumes where it stopped if the script is run again.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a41
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A41.json || { echo "code differs from the amendment 4.1 manifest: stopped"; exit 1; }
W=${A41_WORKERS:-6}
run_set () {   # $1 set name, $2 seeds, $3 arms
  python scripts/exp2_refresh.py --from results/a4/$1 --to results/a41/$1 --arms $3 --seeds $2 --jobs results/a41/jobs_$1.txt --provenance-from results/a4/provenance_$1 --provenance-to results/a41/provenance_$1
  for arm in $(cut -d' ' -f1 results/a41/jobs_$1.txt | sort -u); do
    seeds=$(awk -v a="$arm" '$1==a{print $2}' results/a41/jobs_$1.txt | paste -sd, -)
    if [ "$arm" = "A" ]; then
      caffeinate -i python scripts/analysis_provenance.py --seeds "$seeds" --workers $W --out results/a41/provenance_$1 --row-out results/a41/$1 2>&1 | tee -a results/a41_$1.log
    else
      caffeinate -i python scripts/exp2_run.py --arms "$arm" --seeds "$seeds" --workers $W --out results/a41/$1 2>&1 | tee -a results/a41_$1.log
    fi
  done
}
# which sets: "confirm", "orig" or both (default). The confirmation set can run while run_a4.sh is still finishing
# the original set; the original set must wait until run_a4.sh has finished.
SETS=${1:-"confirm orig"}
for S in $SETS; do
  if [ "$S" = "confirm" ]; then
    run_set confirm 2160-2319 A,B,A-G,G,A-ts0,G-no-twin
    echo "confirmation set refreshed" >> results/a41_confirm.log
  elif [ "$S" = "orig" ]; then
    grep -q "finished in" results/a4_orig.log 2>/dev/null || { echo "run_a4.sh has not finished the original set: stopped"; exit 1; }
    run_set orig 2000-2159 A,B,A-once,A-no-twin,A-no-c1,A-twin-eq,A-perfect,A-2x,A-uncal,A-rc,B-graph
    echo "original set refreshed" >> results/a41_orig.log
  fi
done
echo "amendment 4.1 finished: $SETS"
