#!/bin/bash
# Amendment 15 (spec/PROTOCOL_CAMPAIGN.md): L-full-cd2, the LLM orchestrator shown the advisor's own check groups.
# Waits for run_c4f.sh to finish (it must not see its code change under it), then applies _sync/s98 (the code of
# amendment 15), runs the checks (full test suite; identity of A-full-cd with results/c4d; smoke test of L-full-cd2 on
# six development cases, no errors and at least one "none of these" group shown), freezes
# spec/CAMPAIGN_MANIFEST_C4G.json, and runs L-full-cd2 on the 99 sealed cases A-full-cd planned (API, about $5).
# Launched from _sync/s98 while run_c4f.sh is still running (so that no new file appears under scripts/ before it ends):
#   caffeinate -is bash _sync/s98/scripts/run_c4g.sh      Resumes where it stopped if run again.
cd "$(dirname "$0")/../../.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
LOG=$OUT/run_c4g.log
W=${REC_WORKERS:-6}
M=spec/CAMPAIGN_MANIFEST_C4G.json
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

if ! grep -q "amendments 13 and 14 runs finished" $OUT/run_c4f.log 2>/dev/null; then
  stamp "waiting for run_c4f.sh to finish"
  while ! grep -q "amendments 13 and 14 runs finished" $OUT/run_c4f.log 2>/dev/null; do sleep 60; done
fi
if [ ! -f $OUT/c4g_applied ]; then
  for f in src/adagent/tools/orchestrator_llm.py src/adagent/harness/exp2.py spec/PROTOCOL_CAMPAIGN.md scripts/exp2_freeze.py scripts/run_c4g.sh; do
    cat "_sync/s98/$f" > "$f" || { stamp "could not apply $f: stopped"; exit 1; }
  done
  touch $OUT/c4g_applied
  stamp "amendment 15 code applied from _sync/s98"
fi
if [ ! -f $M ]; then
  stamp "checks: full test suite"
  python -m pytest -q tests 2>&1 | tail -n 3 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  stamp "checks: identity of A-full-cd with results/c4d"
  python scripts/identity_c4f.py --seeds 2004,2013,2017,2021,2028,2029 --workers $W 2>&1 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "identity check failed: stopped"; exit 1; }
  stamp "smoke: L-full-cd2 on six development cases"
  python scripts/exp2_run.py --arms L-full-cd2 --seeds 2004,2013,2017,2021,2028,2029 --workers 3 --out $OUT/smoke_cd2 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  n=$(ls $OUT/smoke_cd2/L-full-cd2/s*.json 2>/dev/null | grep -vc error | tr -d " ")
  e=$(ls $OUT/smoke_cd2/L-full-cd2/*.error.json 2>/dev/null | wc -l | tr -d " ")
  g=$(python -c "import json,glob;print(sum(t.get('none_groups_shown',0) for f in glob.glob('$OUT/smoke_cd2/L-full-cd2/s*.json') for t in (json.load(open(f))['loop'].get('planner_trace') or [])))")
  stamp "smoke: $n results, $e errors, $g 'none of these' groups shown"
  { [ "$n" = "6" ] && [ "$e" = "0" ] && [ "$g" -gt 0 ]; } || { stamp "smoke test failed: stopped"; exit 1; }
  python scripts/exp2_freeze.py --manifest $M && stamp "frozen: $M"
fi
python scripts/exp2_freeze.py --check --manifest $M || { stamp "code differs from the C4G manifest: stopped"; exit 1; }
stamp "start L-full-cd2 on the sealed cases A-full-cd planned"
python scripts/exp2_run.py --arms L-full-cd2 --seeds 7000-7319 --workers $W --out $OUT --only-planned --plan-arm A-full-cd 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "amendment 15 run finished"
