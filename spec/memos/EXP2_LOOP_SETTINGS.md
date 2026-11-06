# Experiment 2: settings of the winnowing loop (provisional, for development)

> Amendment 4 (section at the end) supersedes the settings above wherever they differ: the interpreter's and the
> advisor's tolerances, the advisor's contenders and the twin's scope in particular.

2026-10-16. Every number the loop, the twin, graph consistency, the interpreters and the advisor use, in one place,
with where it comes from. All are provisional: they are set on development cases (seeds 9100 upwards) and frozen in
the Experiment 2 protocol before any held-out run. None was tuned on held-out cases.

## Loop (src/adagent/tools/loop.py)

| Setting | Value | Source |
|---|---|---|
| Effort budget per case | 8 h | Proposal: one working shift; "Specification" Appendix B leaves it open |
| Commit threshold | 0.80 | "Specification" Appendix B (ledger default) |
| Returns to generation | at most 3 | Plan §7 (agreed with Chris 2026-10-15) |
| Checks per case | at most 12 | Guard against loops; never reached so far |
| Graph consistency weight (tau) | 0.3 | Shares the data held with the twin; the two weights sum to one (ledger §7.4) |
| Twin weight (tau) | 0.7 | As above; the twin uses the readings' values, consistency only their presence |
| Twin applied to | the 12 leading hypotheses, then any that enters contention | Cost: about 2 s of simulation per hypothesis |

## Twin (src/adagent/tools/twin.py)

| Setting | Value | Meaning |
|---|---|---|
| Windows | before: onset-480 s to onset-20 s; during: onset-20 s to onset+120 s; after: the last 300 s (at least 150 s after onset) | What is compared |
| Twin error on a change | power 0.25 dB + 15 % of the change; OSNR 0.4 dB + 20 %; log BER 0.3 + 20 %; DGD 1 ps + 20 % | Allowance for the twin's error on changes (the hidden gap; measured error on levels is about 1 dB, mostly cancelled in changes) |
| Cap per stream | 8 nats | One stray reading cannot rule a hypothesis out |
| Signal lost in one, present in the other | 6 nats | |
| Alarm or protection switch predicted and absent, or the reverse | 2 nats each | Alarms have soak times and suppression the twin models; a small allowance for timing |
| Background (a cause nobody proposed) | the no-fault twin's score on the unchanged items, half a nat per changed item, less 12 nats | What a correct but unproposed explanation would typically score. The allowance was first 3 nats; on development cases 9100 to 9114 the true cause scored between 8 nats below and 178 above that figure, so it was raised to 12, which puts every true cause seen so far above the background |
| Transient durations tried | polarisation: 5 ms, 50 ms, 0.5 s, 5 s; mechanical: 0.2, 2, 20, 60 s | From the documents' descriptions (knowledge/practice/twin.yaml) |
| Parameter fit | 8-point grid over the documented range, then 6 golden-section steps | |

## Graph consistency (src/adagent/tools/consistency.py)

A contradicting rule costs 4 nats, at most two rules counted. Supported and unresolved cost nothing.

## Interpreters (src/adagent/tools/interpret.py)

Field tolerances on absolute values (the twin's error on levels is larger than on changes): powers 1.0 dB, estimated
OSNR and margin 1.5 dB, span loss 1.5 dB, DGD 2 ps + 20 %, log BER 1.0 (a 1 dB error in OSNR moves the bit error
rate by about a decade near the operating point), log SOP rate 0.4. A categorical difference
costs 5 nats; a direct answer about a place (unit swap, connector inspection, loopback, cross-connect audit, spare
receiver) against a contender located there, or not, costs 4 nats.

## Advisor (src/adagent/tools/advisor.py)

| Setting | Value |
|---|---|
| Contenders considered | those holding 95 % of the named mass, at most 6 |
| Travel estimate per new site | 2 h (the median of the world's 1 to 4 h; the agent does not know the actual figure) |
| Penalty for a service-affecting check | 1 h |
| Least expected information to recommend a check | 0.05 bits |
| Answers counted as the same | misfit under 1 nat |

## Baseline B (src/adagent/tools/baseline.py)

The procedures as written. It commits when one grounded cause holds at least 0.8 of the leaf mass. Its readings of
the procedures' test texts against the check catalogue are in TEST_CHECKS.

## GNN (src/adagent/tools/gnn.py; Experiment 2 extension, amendment 3)

| Setting | Value | Meaning |
|---|---|---|
| Model | 3 message-passing layers, 32 hidden, 4 edge types; models/gnn/ranker.npz | Trained on 6,000 generated local topologies, 25 epochs |
| Region at diagnosis | lightpaths touching the frame's change symptoms, ranked by changed elements touched, at most 80 | The local subgraph of the design |
| Domain | speaks only when some change symptom is a level shift or a lost signal | Trained on lasting changes |
| Contract | likelihood: log-probability of each candidate's (element, class), summed over causes; declined otherwise; no background | |
| Shares of the data held | with the twin: twin 0.5, graph consistency 0.2, GNN 0.3; without the twin's scoring: graph consistency 0.3, GNN 0.7 | Consumers of one evidence item sum to one |

## Amendment 4 (2026-10-17)

- World: calibrated gap at scale 1 (GAP_REAL), twin calculation error 0.18 dB (CALC_REAL); checks read when they
  complete; a retried check is a fresh trial; the PM register holds one-minute bins from 20 minutes before the
  question; coherent diagnostics report BER (0.1 decade) and SOP rate (10 %) with noise.
- Twin 0.2: transmission only (TRANSMISSION_PM, TRANSMISSION_CHECKS); predictions noise-free plus its own calculation
  error; stated accuracy in knowledge/practice/twin.yaml; health streams not compared; a hypothesis that does not
  improve on no fault by 0.5 nat scores no better than the background; covers every contender of the planner.
- Accuracy standard (src/adagent/accuracy.py, twin.yaml check_accuracy): misfit of an answer = its worst field;
  alike under 2 nats. Used by interpreter, advisor, orchestrator and harness.
- Data held split into transmission and equipment evidence: twin 0.7 and graph consistency 0.3 on transmission;
  graph consistency 0.3 and the documented equipment term 0.7 on equipment (GNN arms: twin 0.5, GNN 0.3, graph
  consistency 0.2; equipment term 0.8). Equipment term: 2 nats per alarm or service effect unexplained or required
  and absent, at most 8; background −2.
- Advisor: contenders = named members of the reported set, at most 12, at least 3; predicts at the estimated time of
  reading; equipment checks by documented meaning (interpret.answer_set, direct).
- Orchestrator prompt orchestrator.v4: the tolerances it states are those of the accuracy standard (2 sigma).
- Loop 0.5: returns from a frame widened by 2 per pass, graph consistency rebuilt on it; residual test from the twin
  (transmission) and the documented joint propagation (alarms, services); repairs carried forward.
