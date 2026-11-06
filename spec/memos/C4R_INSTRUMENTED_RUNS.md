# C4R: instrumented runs and two fixes (3 Nov 2026)

These runs added instrumentation to the agent (twin simulations by purpose, agent seconds by stage, posterior
marginals) and ran it with one part removed at a time. They used spec/CAMPAIGN_MANIFEST_C4R.json, which differs from
C4H in three source files (two fixes, below) and adds the run scripts. The archive keeps the reference arm, A-full-cd,
in results/campaign/c4r/A-full-cd; the paper's Table V draws on it. Under C4R the reference arm computes exactly as
under C4H (see the F9 fix and the smoke test below). The fixed files and the runner are recorded by their hashes in the
C4R manifest; they are not part of the archived code, which is manifest C4V. This memo was edited for the archive on
6 November 2026: its opening and file name now describe the runs' use; its technical content is unchanged.

## The two fixes

**R2 in tools/rules_graph.py (rules as data in the graph).**
- C4 amendment 8 (audit F7) added rule R2 to the coded consistency rules in tools/consistency.py. Under R2, a single cause whose mode documents no effects for its element can take no service down, so services that are down count against it.
- The graph form of the same rules (rules_graph.py) was never given R2. The C4H rerun of the comparison found 59 of 60,230 verdicts differing, all from R2.
- The fix adds the `equipment_single` rule query for single-cause hypotheses, as in the coded form.
- Rerun in the cloud on diagnosis cases 2000-2319 (results/rules_compare_fix): all 60,230 verdicts agree. The graph form takes 1.065 times the time and makes 69 engine requests.
- The agent does not use rules_graph.py, so no agent outcome can change.
- The lesson for maintenance is the drift itself: keep one source of truth.

**F9 in ledger.py and tools/loop.py (the GNN ranker's term).**
- The GNN ranker scores only (element, fault class) pairs among its 14 classes.
- Until now, a candidate it could not score was declined and took the term's mean, as did "none of these". That gave such candidates an arbitrary place. They lost to strong scored candidates and beat weak ones, depending only on which other candidates happened to be scored (audit F9).
- Two remedies were considered.
  - **Predict "no fault" for them**, as the C4 F1/F3 fixes do for the twin. Rejected. A class the model never saw is not evidence of no fault. The model's "none" probability at a changed element is low, so this would charge every unknown-class candidate, the truth included when its mode is outside the 14, as if the model had ruled it out.
  - **Make the term conditional.** Chosen. The GNN reorders only the candidates it scores and leaves their total probability, against everything else, as the other terms set it. Scored candidates take their clipped, tempered values. Every other hypothesis, "none of these" included, takes c0 = log Σ_S q(s)·exp(τ·v_s) − log Σ_S q(s), where q is the posterior from all other terms (the cavity).
  - The conditional remedy is what the tool's own description promised ("reorders the candidates it can score and is silent on causes nobody proposed"). It moves no probability onto or off causes the model cannot see.
- Implementation:
  - `_LikTerm.neutral` ("mean" by default; "conditional" for the GNN);
  - `Ledger._conditional_contrib`, applied after all other terms;
  - `loop._gnn_term` passes `neutral="conditional"`.
- Every other term keeps the mean, so every arm without the GNN ranker computes exactly as under C4H. The smoke test confirms this on A-full-cd.

## What runs (scripts/run_c4r.sh, scripts/report_run.py)

Each arm is the reported agent (A-full-cd) with one change. The output goes to results/campaign/c4r/<arm>/.

| Arm | Change | Seeds |
|---|---|---|
| A-full-cd | none (the reference, instrumented) | 2000-2159 |
| A-full-cd-notwin | twin off | 2000-2159 |
| A-full-cd-noc1 | graph consistency term off | 2000-2159 |
| A-full-cd-nochecks | every check the planner chooses becomes a stop; returns to generation stay | 2000-2159 |
| A-full-cd-nopeel | no ablative use of the twin on returns | 2000-2159 |
| A-full-cd-notr | no transient event recogniser | 2000-2159 |
| A-full-cd-gnn | the GNN ranker as a conditional likelihood term (F9 fixed) | 2000-2159 |
| A-full-cd-mf | the multiple-fault specialist with the "can this decide?" step | the 40 two-fault seeds in 2000-2319 (seed % 8 = 5) |

A-full-cd-mf pairs like for like with A-full-cd on the same seeds. The C4H records in results/c4d/A-full-cd hold the A-full-cd side.

## What each record adds (row["c4r"])

- **Twin simulations by purpose**, as counts and seconds. They sum to twin_cost.simulations.
  - explain: fit of a hypothesis, evidence terms;
  - residual: what the leader leaves unexplained before committing;
  - ablative: pair and triple fits on returns, peeling and the multiple-fault specialist;
  - plan: the planner's predictions;
  - interpret: reading a check's answer;
  - generation; other.
- **Check predictions on the twin's world** (predict_check), by purpose, counted separately.
- **Agent seconds by stage**, exclusive: twin setup, generation pass 1, returns, evidence terms, decide, planning, checks, check reading, agent other, harness.
- **Final posterior marginals:** the probability on the true fault mode wherever it is, on the true location whatever the mode, and on both. These use strict matching with no equivalence classes. The record also keeps the posterior's top ten.

## Smoke test (cloud, 3 Nov 11:15-11:35)

- **Reference.** A-full-cd on seeds 2000, 2003, 2005 and 2028 gave the same outcome, leader and twin simulation count as the C4H records in results/c4d/A-full-cd (91, 49, 90 and 52 simulations).
- **Tags.** In every record, the simulations tagged by purpose sum to twin_cost.simulations.
- **Every arm.** Each arm ran on seeds 2000 and 2003: A-full-cd-mf on 2005, A-full-cd-nochecks also on 2028 (where the reference makes one check and the arm makes none).
- **F9 fix.** On 2000, the GNN term scored 20 hypotheses and could not score 23. The odds of the scored set against the rest were 912.414 with the term and 912.414 without it.

## Fix before the run (3 Nov 11:50 Ottawa)
The first launch on the laptop stopped at once: Python 3.14's argparse rejects a help string containing "%" (report_run.py
--kind). The help text was reworded; run_c4r.sh now stops with a message if the runner itself fails (it had reported
"failed jobs: 0" because no job had run). No agent code changed. Smoke on Python 3.14 in the cloud before re-sending.
