#!/bin/bash
# Amendments 13 and 14 (spec/PROTOCOL_CAMPAIGN.md). In order, stopping at the first failure:
#   checks, once (no API): the full test suite; the identity check (A-full-cd under the refactored advisor reproduces
#     results/c4d on six development cases); the proposer wrapper's control (with the proposer abstaining, the arm
#     with it reproduces the arm without it on three development cases);
#   smoke test (API, up to $8): L-full-cd on six development cases, no errors;
#   freeze: spec/CAMPAIGN_MANIFEST_C4F.json written once the checks pass, and checked on every start after;
#   runs: L-full-cd on the 99 sealed cases A-full-cd planned (API, up to $100); then on the development cases
#     2000-2159 the agent with and without its LLM proposer under the documentation with 30% of fault signatures
#     removed, and under 20% of fault modes removed (API for the two proposer arms, about $25).
# Run from anywhere:   caffeinate -is bash scripts/run_c4f.sh      Resumes where it stopped if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
export ADAGENT_RECORD_RUN_C4=1
OUT=results/campaign/c4
LOG=$OUT/run_c4f.log
W=${REC_WORKERS:-6}
M=spec/CAMPAIGN_MANIFEST_C4F.json
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }

if [ ! -f $M ]; then
  stamp "checks: full test suite"
  python -m pytest -q tests 2>&1 | tail -n 3 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "tests failed: stopped"; exit 1; }
  stamp "checks: identity of A-full-cd with results/c4d"
  python scripts/identity_c4f.py --seeds 2004,2013,2017,2021,2028,2029 --workers $W 2>&1 | tee -a $LOG
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "identity check failed: stopped"; exit 1; }
  stamp "checks: the proposer wrapper's control (proposer abstaining)"
  python scripts/kgx_llm_run.py --arms A-full-cd-kg-sig,A-full-cd-llm-kg-sig --seeds 2001,2006,2009 --workers 3 \
      --out results/campaign/kgx_c4_control --fake-empty 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  python - <<'PY' 2>&1 | tee -a $LOG
import json, sys
from pathlib import Path
d = Path("results/campaign/kgx_c4_control"); bad = []
for s in (2001, 2006, 2009):
    a = json.loads((d / "A-full-cd-kg-sig" / f"s{s}.json").read_text())
    b = json.loads((d / "A-full-cd-llm-kg-sig" / f"s{s}.json").read_text())
    if a["score"] != b["score"] or a["loop"].get("decision") != b["loop"].get("decision"):
        bad.append(s)
print("control:", "identical" if not bad else f"DIFFERS {bad}")
sys.exit(1 if bad else 0)
PY
  [ "${PIPESTATUS[0]}" = "0" ] || { stamp "control failed: stopped"; exit 1; }
  stamp "smoke: L-full-cd on six development cases"
  python scripts/exp2_run.py --arms L-full-cd --seeds 2004,2013,2017,2021,2028,2029 --workers 3 --out $OUT/smoke_cd 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
  n=$(ls $OUT/smoke_cd/L-full-cd/s*.json 2>/dev/null | grep -vc error | tr -d " ")
  e=$(ls $OUT/smoke_cd/L-full-cd/*.error.json 2>/dev/null | wc -l | tr -d " ")
  stamp "smoke: $n results, $e errors"
  { [ "$n" = "6" ] && [ "$e" = "0" ]; } || { stamp "smoke test failed: stopped"; exit 1; }
  python -c "import json,glob;print('smoke: costly checks withheld by the step:',sum(len(json.load(open(f))['loop'].get('decide_refused') or []) for f in glob.glob('$OUT/smoke_cd/L-full-cd/s*.json')))" 2>&1 | tee -a $LOG
  python scripts/exp2_freeze.py --manifest $M && stamp "frozen: $M"
fi
python scripts/exp2_freeze.py --check --manifest $M || { stamp "code differs from the C4F manifest: stopped"; exit 1; }

stamp "start L-full-cd on the sealed cases A-full-cd planned"
python scripts/exp2_run.py --arms L-full-cd --seeds 7000-7319 --workers $W --out $OUT --only-planned --plan-arm A-full-cd 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "done  L-full-cd"
stamp "start the proposer under the degraded documentation (2000-2159)"
python scripts/kgx_llm_run.py --arms A-full-cd-kg-sig,A-full-cd-llm-kg-sig,A-full-cd-kg-modes,A-full-cd-llm-kg-modes --seeds 2000-2159 --workers $W 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
stamp "amendments 13 and 14 runs finished"
