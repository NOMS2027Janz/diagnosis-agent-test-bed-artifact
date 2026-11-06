#!/bin/bash
# C4d validation: A-full-cd on the development cases 2000-2319 with the guards on the two-leaders compound
# (COMPOUND_MIN_LEAD 0.2) and on the cause grain (CAUSE_ALONE_MIN 0.2), and none on peeling. Waits for the C4c run
# to finish if it is still going. No API. Results under results/c4d, log results/c4d.log. Resumes if run again.
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
until grep -q "^finished in" results/c4c.log; do sleep 60; done
# the C4d code (guard split) goes in only once C4c has finished, so C4c runs to the end on its own code
cat _sync/s90/src/adagent/tools/loop.py > src/adagent/tools/loop.py
[ "$(md5 -q src/adagent/tools/loop.py)" = "03227b1ae5d30b8af32c0413d35eae66" ] || { echo "loop.py did not install: stopped"; exit 1; }
python scripts/exp2_run.py --arms A-full-cd --seeds 2000-2319 --workers ${REC_WORKERS:-6} --out results/c4d 2>&1 | tee -a results/c4d.log
