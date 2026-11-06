#!/bin/bash
# Amendment 4.2b: arm A-twin-eq-doc on the original set. The equipment evidence is judged by a twin that simulates
# equipment behaviour, with the same charges and the same value for "none of these" as the documented equipment
# term; transmission and the reading of checks as in arm A; so only the precision of the equipment predictions
# differs from arm A (spec/PROTOCOL_EXP2_WINNOWING.md §15). No API calls.
# Launch now from anywhere: it waits until the amendment 4.2 runs have finished, then applies sync64 (from
# ~/Downloads/sync64.zip, unzipped into _sync/s64 and copied over in place), checks the freeze and runs the arm.
#     bash scripts/run_a42b.sh
cd "$(dirname "$0")/.." || exit 1
until grep -q "orig set refreshed" results/a42_orig.log 2>/dev/null && ! pgrep -f "exp2_run.py --arms L" >/dev/null \
      && ! pgrep -f "run_a42_models.sh" >/dev/null; do sleep 120; done
echo "amendment 4.2 finished; applying sync64 $(date)" | tee -a results/a42b.log
mkdir -p _sync/s64 && unzip -o ~/Downloads/sync64.zip -d _sync/s64 >/dev/null || { echo "sync64.zip not found in Downloads: stopped" | tee -a results/a42b.log; exit 1; }
(cd _sync/s64 && find . -type f | sed 's#^\./##') | while read f; do mkdir -p "$(dirname "$f")"; cat "_sync/s64/$f" > "$f"; done
source .venv/bin/activate
python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A42B.json || { echo "code differs from the amendment 4.2b manifest: stopped" | tee -a results/a42b.log; exit 1; }
W=${A42_WORKERS:-6}
caffeinate -i python scripts/exp2_run.py --arms A-twin-eq-doc --seeds 2000-2159 --workers $W --out results/a42/orig 2>&1 | tee -a results/a42b.log
echo "amendment 4.2b finished" | tee -a results/a42b.log
