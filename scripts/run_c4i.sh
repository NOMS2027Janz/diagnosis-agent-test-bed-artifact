#!/bin/bash
# Amendment 17 (spec/PROTOCOL_CAMPAIGN.md): completing the C4 reruns, the laptop's part. Runs after
# scripts/run_c4h.sh has frozen the C4H manifest, and checks the code against it first. In order of priority:
# the LLM deciding alone and manual troubleshooting on the diagnosis cases; the capability ladder; the LLM proposer
# with full documentation; the future-instrument arms on the sealed cases. Every step skips finished cases, so the
# script resumes where it stopped if run again.
# Run from anywhere:   caffeinate -is bash scripts/run_c4i.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
DEV=results/campaign/c4_dev
REC=results/campaign/c4
LOG=$DEV/run_c4i.log
W=${REC_WORKERS:-6}
M=spec/CAMPAIGN_MANIFEST_C4H.json
mkdir -p $DEV
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
run () {  # arms seeds out
  stamp "start $1 on $2"
  python scripts/exp2_run.py --arms "$1" --seeds "$2" --workers $W --out "$3" 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
}
seeds () { PYTHONPATH=src python -c "from adagent.harness.exp2 import ladder_seeds; print(','.join(map(str, ladder_seeds('$1'))))"; }

[ -f $M ] || { stamp "no C4H manifest: run scripts/run_c4h.sh first"; exit 1; }
python scripts/exp2_freeze.py --check --manifest $M || { stamp "code differs from the C4H manifest: stopped"; exit 1; }

# 1. the LLM deciding alone (API, about $27) and manual troubleshooting, diagnosis cases
run LLM-1 2000-2319 $DEV
run AC,B,B-fut,B-L1,B-Lall 2000-2319 $DEV
# the generation study repeated: the LLM proposer's reference arm L0, one repeat (API, about $15); the offline lines
# and the equivalence classes are computed in the cloud workspace
stamp "start the generation study L0 repeat 1 (results/exp1_c4)"
EXP1_OUT=results/exp1_c4 EXP1_MANIFEST=$M python scripts/exp1_llm.py L0 1 2>&1 | tee -a $LOG | tail -n 2
# 2. the capability ladder, diagnosis cases (A-full-cd-fut is each rung's reference)
run A-full-cd-fut 2000-2319 $DEV
run A-L1 "$(seeds L1)" $DEV
run A-Lall 2000-2319 $DEV
run A-L3 "$(seeds L3)" $DEV
run A-L2 "$(seeds L2)" $DEV
run A-L4,A-L5 "$(seeds L4)" $DEV
run A-L6 "$(seeds L6)" $DEV
# 3. the LLM proposer with full documentation (API, about $13), diagnosis cases 2000-2159
stamp "start A-full-cd-llm-kg-none on 2000-2159"
python scripts/kgx_llm_run.py --arms A-full-cd-llm-kg-none --seeds 2000-2159 --workers $W 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
# 3b. the graph degraded (types and SRLGs lost; flat graph), agent and manual, diagnosis cases 2000-2159. Moved here
# from the cloud workspace on 30 Oct: its 2 CPUs take about 6 min a case, too slow to finish these by Sunday.
stamp "start the graph degraded: kg-inst, kg-flat, kg-none (agent and manual) on 2000-2159"
python scripts/kgx_run.py --arms A-full-cd-kg-inst,A-full-cd-kg-flat,A-full-cd-kg-none,B-kg-none,B-kg-inst,B-kg-flat --seeds 2000-2159 --workers $W --out results/campaign/kgx_c4 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
# 4. future instruments on the sealed cases
export ADAGENT_RECORD_RUN_C4=1
run A-full-cd-fut,B-fut 7000-7319 $REC
stamp "amendment 17 laptop runs finished"
