#!/bin/bash
# Amendment 4, Experiment 1 without new calls: the offline lines rerun and the LLM arms' stored proposals grounded
# again on the current code, into results/a4/exp1_rg/, then equivalence classes and the analysis. Run from anywhere:
#     bash scripts/run_a4_exp1_reground.sh
cd "$(dirname "$0")/.." || exit 1
[ -f .venv/bin/activate ] && source .venv/bin/activate
mkdir -p results/a4/exp1_rg
python3 scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A4.json || { echo "code differs from the amendment 4 manifest: stopped"; exit 1; }
export EXP1_OUT=results/a4/exp1_rg EXP1_MANIFEST=spec/EXP2_MANIFEST_A4.json
python3 scripts/exp1_offline.py 2>&1 | tee -a results/a4_exp1_rg.log
python3 scripts/exp1_reground.py 2>&1 | tee -a results/a4_exp1_rg.log
for k in 0 1; do python3 scripts/exp1_equivalence.py --part $k/2 >> results/a4_exp1_rg_eq$k.log 2>&1 & done; wait
python3 scripts/exp1_analysis.py > /dev/null && echo "analysis written: results/a4/exp1_rg/ANALYSIS.md"
echo "amendment 4 Experiment 1 (re-grounded) finished"
