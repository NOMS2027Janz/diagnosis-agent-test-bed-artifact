# Amendment 17: question interpretation and answer expression, corrected and rerun

Date: 1 Nov 2026. Replaces amendment 16's qi and ae results, which are not reported (integrity rule).

Faults found in amendment 16's test design:
1. qi: the LLM reader was told reported_at_s is "when the problem was noticed or began"; the structured question and
   the scorer hold the time reported or noticed. On slow-trend questions the reader gave the onset (days earlier);
   all 55 of its drift failures were this.
2. ae: the faithfulness checker compared cited values as text; 3 values cited in compact JSON were flagged though
   present. The summary also carried no runners-up and no cause/place split (standing rule, 31 Oct).

Changes (scripts/a17_qi_run.py, scripts/a17_ae_run.py; frozen modules imported unchanged, C4H manifest unchanged):
- qi: reader prompt states the field's meaning (time reported or noticed; for a slow trend, when noticed, normally
  the time of asking). Same cases, same controlled rewrites (asserted equal), same free paraphrases (read from the
  amendment-16 records), same coded reader and scoring. Output results/campaign/c4/qi17.
- ae: summary adds runners_up (final ledger's six leaders) and cause_and_place (what, where, both); prompt asks for
  both; template reports both; checker compares parsed JSON. Conclusion and identifier checks unchanged. Output
  results/campaign/c4/ae17.
Each record carries the script's sha256 and the prompt version.
