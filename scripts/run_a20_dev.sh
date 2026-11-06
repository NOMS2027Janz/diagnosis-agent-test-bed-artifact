#!/bin/bash
# Amendment 20 development (spec/memos/AMENDMENT_20.md). Uses the API for step 2 only.
# 1. Check the frozen agent code against the C4H manifest.
# 2. Completions (scripts/a20_complete.py): signatures removed and modes removed on six development draws (dev1, dev2,
#    dev4, dev6, dev7, dev10; chosen because each removes outside-plant documentation), and complete documentation.
# 3. Score them against the true documentation (scripts/a20_score.py).
# 4. The agent on development cases 3000-3079, draw dev4: completed and empty, for each degradation and for complete
#    documentation; and modes completed with one-of-a-kind additions at a fifth of the half rate (--single 0.1).
# Every step skips finished work. Run from anywhere:   caffeinate -is bash scripts/run_a20_dev.sh
cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/.venv/bin:$PATH"
LOG=results/campaign/run_a20_dev.log
A=results/campaign/a20
mkdir -p $A
stamp () { echo "[$(date '+%a %H:%M')] $*" | tee -a $LOG; }
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4H.json || { stamp "code differs from the C4H manifest: stopped"; exit 1; }

stamp "completions"
for d in dev1 dev2 dev4; do for k in sig modes; do
  python scripts/a20_complete.py --kind $k --draw $d >> $A/log_${k}_${d}.txt 2>&1 &
done; done; wait
for d in dev6 dev7 dev10; do for k in sig modes; do
  python scripts/a20_complete.py --kind $k --draw $d >> $A/log_${k}_${d}.txt 2>&1 &
done; done
python scripts/a20_complete.py --kind none --draw dev4 >> $A/log_none_dev4.txt 2>&1 &
wait
n=$(grep -l "^finished" $A/log_*.txt | wc -l | tr -d " ")
stamp "completions finished: $n of 13"
[ "$n" = "13" ] || { stamp "some completions did not finish (see $A/log_*.txt): rerun this command"; exit 1; }
python scripts/a20_score.py 2>&1 | tee -a $LOG

for k in none sig modes; do
  f=$A/completion_${k}_dev4_empty.json
  [ -f $f ] || echo "{\"kind\": \"$k\", \"draw\": \"dev4\", \"label\": \"empty\", \"types\": {}}" > $f
done
a20 () {  # arm label single
  stamp "start a20 $1 dev4$2 single $3"
  python scripts/a20_run.py --arms "$1" --seed dev4 --label "$2" --single $3 --seeds 3000-3079 --workers 6 2>&1 | tee -a $LOG | grep -E --line-buffered "jobs to run|FAILED|finished"
}
a20 A-full-cd-kg-sig-comp "" 0.5
a20 A-full-cd-kg-modes-comp "" 0.5
a20 A-full-cd-kg-none-comp "" 0.5
a20 A-full-cd-kg-sig-comp _empty 0.5
a20 A-full-cd-kg-modes-comp _empty 0.5
a20 A-full-cd-kg-none-comp _empty 0.5
a20 A-full-cd-kg-modes-comp "" 0.1
stamp "all finished"
