#!/bin/bash
# Amendment 20, the test, run once (spec/memos/AMENDMENT_20.md). Frozen: scripts/a20_complete.py (prompt
# doc-completion-a20-0.1), scripts/a20b_run.py with --nodup, trust a20, one-of-a-kind rate 0.5.
# 1. Completions for the test draw (seed 20261102, never examined): signatures removed, modes removed, complete.
# 2. Fresh cases 3160-3319, test draw: each degradation not completed and completed, and the control.
# 3. Condition 2 of amendment 18: what the frozen completion adds to complete documentation, on the diagnosis cases
#    2000-2319 under status quo and future instrumentation (compared afterwards with results/c4d/A-full-cd and
#    results/campaign/c4_dev/A-full-cd-fut).
# Every step skips finished work. Run from anywhere:   caffeinate -is bash scripts/run_a20_test.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=results/campaign/run_a20_test.log
A=results/campaign/a20
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4H.json || { stamp "code differs from the C4H manifest: stopped"; exit 1; }
stamp "completions, test draw"
for k in sig modes none; do
  python scripts/a20_complete.py --kind $k --draw test >> $A/log_${k}_test.txt 2>&1 &
done; wait
n=$(grep -l "^finished" $A/log_sig_test.txt $A/log_modes_test.txt $A/log_none_test.txt | wc -l | tr -d " ")
stamp "completions finished: $n of 3"
[ "$n" = "3" ] || { stamp "some completions did not finish: rerun this command"; exit 1; }
for k in none sig modes; do
  f=$A/completion_${k}_test_empty.json
  [ -f $f ] || echo "{\"kind\": \"$k\", \"draw\": \"test\", \"label\": \"empty\", \"types\": {}}" > $f
done
t () {  # arms label seeds
  stamp "start a20b $1 test$2 on $3"
  python scripts/a20b_run.py --arms "$1" --seed test --label "$2" --nodup --seeds "$3" --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
}
t A-full-cd-kg-sig-comp _empty 3160-3319
t A-full-cd-kg-sig-comp "" 3160-3319
t A-full-cd-kg-modes-comp _empty 3160-3319
t A-full-cd-kg-modes-comp "" 3160-3319
t A-full-cd-kg-none-comp _empty 3160-3319
stamp "test arms finished"
t A-full-cd-kg-none-comp "" 2000-2319
t A-full-cd-fut-kg-none-comp "" 2000-2319
stamp "all finished"
