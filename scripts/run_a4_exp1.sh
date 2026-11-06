#!/bin/bash
# Amendment 4: Experiment 1 (generation) rerun on the corrected code, into results/a4/exp1/. API calls (the LLM arms
# L0 three repeats, L1, L3 with gpt-5.6 Sol): about $90, about 10 hours. Run from anywhere, alongside the other chains:
#     bash scripts/run_a4_exp1.sh
# Every step resumes where it stopped if the script is run again.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a4/exp1
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A4.json || { echo "code differs from the amendment 4 manifest: stopped"; exit 1; }
export EXP1_OUT=results/a4/exp1 EXP1_MANIFEST=spec/EXP2_MANIFEST_A4.json
caffeinate -i python scripts/exp1_offline.py 2>&1 | tee -a results/a4_exp1.log
caffeinate -i python scripts/exp1_llm.py L0 1 2>&1 | tee -a results/a4_exp1.log
caffeinate -i python scripts/exp1_llm.py L0 2 2>&1 | tee -a results/a4_exp1.log
caffeinate -i python scripts/exp1_llm.py L0 3 2>&1 | tee -a results/a4_exp1.log
caffeinate -i python scripts/exp1_llm.py L1 1 2>&1 | tee -a results/a4_exp1.log
caffeinate -i python scripts/exp1_llm.py L3 1 2>&1 | tee -a results/a4_exp1.log
for k in 0 1 2 3; do caffeinate -i python scripts/exp1_equivalence.py --part $k/4 >> results/a4_exp1_eq$k.log 2>&1 & done; wait
python scripts/exp1_analysis.py > /dev/null && echo "analysis written: results/a4/exp1/ANALYSIS.md"
echo "amendment 4 Experiment 1 finished"
