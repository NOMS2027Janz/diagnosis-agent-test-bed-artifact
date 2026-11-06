#!/bin/bash
# Amendment 4.2 (spec/PROTOCOL_EXP2_WINNOWING.md §14): series of readings in the accuracy standard; transient
# durations fitted. Reruns, in every agent arm, the cases that reached the planner or a check and those with a
# transient candidate (spec/EXP2_A42_AFFECTED.json); every other row is copied from results/a41. Baseline arms are
# copied whole. Run from anywhere:
#     bash scripts/run_a42.sh confirm      then      bash scripts/run_a42.sh orig
# and, in a second window, the model comparison (about $10): bash scripts/run_a42_models.sh
# Every step resumes where it stopped if the script is run again.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a42
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A42.json || { echo "code differs from the amendment 4.2 manifest: stopped"; exit 1; }
W=${A42_WORKERS:-6}
run_set () {   # $1 set name, $2 arms
  python scripts/exp2_scope.py --from results/a41 --to results/a42 --set $1 --affected spec/EXP2_A42_AFFECTED.json --also-reached --arms $2 --jobs results/a42/jobs_$1.txt
  for arm in $(cut -d' ' -f1 results/a42/jobs_$1.txt | sort -u); do
    seeds=$(awk -v a="$arm" '$1==a{print $2}' results/a42/jobs_$1.txt | paste -sd, -)
    if [ "$arm" = "A" ]; then
      caffeinate -i python scripts/analysis_provenance.py --seeds "$seeds" --workers $W --out results/a42/provenance_$1 --row-out results/a42/$1 2>&1 | tee -a results/a42_$1.log
    else
      caffeinate -i python scripts/exp2_run.py --arms "$arm" --seeds "$seeds" --workers $W --out results/a42/$1 2>&1 | tee -a results/a42_$1.log
    fi
  done
  echo "$1 set refreshed" >> results/a42_$1.log
}
for S in ${1:-confirm orig}; do
  [ "$S" = "confirm" ] && run_set confirm A,B,A-G,G,A-ts0,G-no-twin
  [ "$S" = "orig" ] && run_set orig A,B,A-once,A-no-twin,A-no-c1,A-twin-eq,A-perfect,A-2x,A-uncal,A-rc,B-graph
done
echo "amendment 4.2 finished: ${1:-confirm orig}"
