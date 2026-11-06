#!/bin/bash
# Amendment 4: the LLM orchestrator (arm L) on the confirmation set's planned cases (those where arm A did not commit
# at once), with four models. API calls: about $10 in all. Run from anywhere, alongside run_a4.sh:
#     bash scripts/run_a4_models.sh
# It waits until run_a4.sh has finished the confirmation set's arm A (which defines the planned cases).
# Every step resumes where it stopped if the script is run again.
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a4
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A4.json || { echo "code differs from the amendment 4 manifest: stopped"; exit 1; }
until grep -q "finished in" results/a4_confirm_A.log 2>/dev/null; do sleep 120; done
W=${A4_LLM_WORKERS:-3}
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a4/confirm --model gpt-5.6-sol --workers $W --out results/a4/models/sol 2>&1 | tee -a results/a4_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a4/confirm --model gpt-5.6-luna --workers $W --out results/a4/models/luna 2>&1 | tee -a results/a4_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a4/confirm --model gpt-5.4-mini --workers $W --out results/a4/models/mini 2>&1 | tee -a results/a4_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a4/confirm --provider deepseek --model deepseek-v4-pro --workers $W --out results/a4/models/dsv4pro 2>&1 | tee -a results/a4_models.log
echo "amendment 4 model comparison finished"
