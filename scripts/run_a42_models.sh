#!/bin/bash
# Amendment 4.2: arm L (the LLM orchestrator) with four models on the confirmation set's planned cases. API calls,
# about $10. Run from anywhere, alongside run_a42.sh; it waits until run_a42.sh has refreshed the confirmation set.
#     bash scripts/run_a42_models.sh
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
mkdir -p results/a42
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A42.json || { echo "code differs from the amendment 4.2 manifest: stopped"; exit 1; }
until grep -q "confirm set refreshed" results/a42_confirm.log 2>/dev/null; do sleep 120; done
W=${A42_LLM_WORKERS:-3}
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a42/confirm --model gpt-5.6-sol --workers $W --out results/a42/models/sol 2>&1 | tee -a results/a42_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a42/confirm --model gpt-5.6-luna --workers $W --out results/a42/models/luna 2>&1 | tee -a results/a42_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a42/confirm --model gpt-5.4-mini --workers $W --out results/a42/models/mini 2>&1 | tee -a results/a42_models.log
caffeinate -i python scripts/exp2_run.py --arms L --seeds 2160-2319 --only-planned --plan-from results/a42/confirm --provider deepseek --model deepseek-v4-pro --workers $W --out results/a42/models/dsv4pro 2>&1 | tee -a results/a42_models.log
echo "amendment 4.2 model comparison finished"
