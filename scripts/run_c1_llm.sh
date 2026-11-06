#!/bin/bash
# Campaign to the NOMS 2027 paper, stage 1, part 2 (spec/PROTOCOL_CAMPAIGN.md, section 3.3): the LLM diagnoses and
# decides alone. API calls to the study's main model (OpenAI; key in secrets/openai.env, never printed).
#   LLM-1   single shot, on the 320 diagnosis cases (about $65);
#   LLM-chk with checks, on 80 diagnosis cases, 10 of each kind (seeds 2000 to 2079; about $40 to $80).
# The first 20 single-shot cases run alone and the script stops, so the spend can be checked before the rest:
#     bash scripts/run_c1_llm.sh pilot
#     bash scripts/run_c1_llm.sh full
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
mkdir -p results/campaign
LOG=results/campaign/run_c1_llm.log
W=${C1_LLM_WORKERS:-4}
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C1L.json || { stamp "code differs from the stage 1 LLM manifest: stopped"; exit 1; }
[ -f secrets/openai.env ] || { stamp "secrets/openai.env not found: stopped"; exit 1; }
run () {
  stamp "start $1 on $2"
  caffeinate -i python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out results/campaign/llm 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  stamp "done  $1 on $2"
}
case "$1" in
  pilot) run LLM-1 2000-2019
         python scripts/analysis_llm_spend.py results/campaign/llm/LLM-1 | tee -a $LOG ;;
  full)  run LLM-1 2000-2319
         run LLM-chk 2000-2079
         python scripts/analysis_llm_spend.py results/campaign/llm/LLM-1 results/campaign/llm/LLM-chk | tee -a $LOG ;;
  *) echo "usage: bash scripts/run_c1_llm.sh pilot|full" ;;
esac
