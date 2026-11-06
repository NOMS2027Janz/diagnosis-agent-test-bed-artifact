# The diagnosis agent test bed: simulator, agent, harness, sealed cases and technical companion

This archive accompanies the paper "An Agent That Diagnoses the Optical Network: Measured Against Alarm-Based
Troubleshooting" (C. Janz, H. Yu, S. Taeb, R. Cai, Y. You, Y. Chen; IEEE/IFIP NOMS 2027). It holds everything needed to
check and reproduce the paper's results:

- the world simulator;
- the knowledge files the agent reads;
- the agent and its ledger;
- the harness that generates, scores and costs each case from its seed;
- the manifests of file hashes under which every arm ran;
- one record per case and arm;
- a technical companion, `Technical_Companion.pdf`, which gives the agent's method in full, the test bed, the scoring
  and costing rules and the reproduction steps.

The companion also lists every modelled quantity with its source and the test of its sensitivity, and draws the
reference network in full.

## Contents

| Path | Contents |
|---|---|
| `Technical_Companion.pdf` | The method, the test bed, scoring and costing, and reproduction, in full |
| `src/adagent/` | The code. The world simulator is in `world/`, the knowledge graph and data access in `substrate/`, the generation and winnowing tools, the twin and the planners in `tools/`, the ledger in `ledger.py`, the experiment harness in `harness/`, and the LLM client in `llm/` |
| `knowledge/` | The documentation the agent reads, all written for the test bed: two fictitious vendors' equipment packages, ITU-T G.798 extracts, practice notes, the alarm-clearing procedures, consistency rules and the vocabulary |
| `models/gnn/` | The learned specialists: the transient event recogniser and the multiple-fault specialist |
| `scripts/` | Runners, freeze check, analyses |
| `tests/` | Tests (`pytest`) |
| `spec/` | The frozen manifests (`CAMPAIGN_MANIFEST_C4H.json` for every result in the paper but the twin with structural error, which ran under `C4T` and, for the margin judged against the fleet, `C4V`), the campaign protocol and its amendments |
| `results/` | One record per case and arm (see below) |
| `knowledge_pilot/` | The pilot of building the knowledge graph from twelve vendor-style manuals (Table VII, first row) |

## Results and where each comes from

The case sets:

- **Sealed cases:** seeds 7000 to 7319, drawn after the freeze and run once.
- **Diagnosis cases:** seeds 2000 to 2319, used in development.
- **Fresh test cases for documentation completion:** seeds 3160 to 3319.
- **Generation cases:** seeds 1000 to 1099.

| Paper | Records |
|---|---|
| Table III, the sealed cases | `results/campaign/c4/` (`A-full-cd`, `A-full-cd-fut`, `A-Lall`, `AC`, `AC-fut`, `B`, `B-fut`, `B-ac`, `B-ac-fut`, `B-Lall`); summary `results/campaign/flagship_c4.json`. `AC-fut` matches `AC` in every case by construction: alarm correlation reads only alarms, and the future instruments add none |
| Table IV, by kind of anomaly | the same records |
| Table V, the LLM deciding alone | `results/campaign/c4_dev/LLM-1` against `c4_dev` and `results/campaign/c4r/A-full-cd` |
| Table VI, telemetry capabilities | `results/campaign/c4_dev/A-L1` to `A-Lall`, `B-L1`, `B-Lall` |
| Table VII and Section VIII, the LLM's roles | planning: `results/campaign/c4/L-full-cd2`; proposing: `results/exp1_c4/`; framing the case: `results/campaign/c4/A-full-cd-lf`; question reading: `results/campaign/qi19/`; answer expression: `results/campaign/c4/ae17` and `results/c4h_cloud/ae_records`; building the graph: `knowledge_pilot/out/` |
| Robustness and the degraded knowledge graph | `results/campaign/kgx_c4/` with its control `kgx_c4_control/`; the twin with a shifted mean: the frozen agent and manual troubleshooting in `results/campaign/c4t/` (`A-full-cd-tm1`, `B-tm1`), the margin judged against the fleet in `results/campaign/c4v/` (`A-full-cd`, `A-full-cd-tm1`); tabulated by `scripts/c4t_tm_summary.py` |
| Completing the documentation | first round: `results/campaign/a18/`; second round, development and test: `results/campaign/a20/`, with the completions as `completion_*.json` and the trace of every changed case in `a20/knockouts_2026-11-04/` |
| Warning rules (1,686 commitments) | `results/campaign/warnings/` |
| Worked example, case 2021 | `results/campaign/c4_trace/` |
| Reweighted mixes of kinds | `results/campaign/mix_c4.json` |

Each record is one JSON file per case, `s<seed>.json`, holding:

- the case and its truth;
- the agent's trajectory, ledger and checks;
- the score: outcome, equivalence class, effort and site visits.

## Reproducing

Python 3.11 or later; the runs used Python 3.14.

```
pip install -e ".[dev]"
python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4V.json   # prints "unchanged"
python scripts/exp2_run.py --arms A-full-cd --seeds 2000-2019 --workers 4        # rerun part of an arm
```

- **The sealed seeds (7000 to 7319) are refused** unless `ADAGENT_RECORD_RUN_C4=1` is set, as it was for the single
  record run.
- **The archived code is manifest C4V.** It is the code of manifest C4H with three amendments made for the test of a
  twin with structural error (`spec/memos/`): amendment 22 carries the mismatch to the network of each case's day,
  amendment 23 lets a drift case be posed in a world that raises standing alarms, and amendment 24 adds the margin
  levels judged against the fleet, switched on by `ADAGENT_MARGIN_FLEET=1` and off by default. Four files differ from
  C4H, and with the variable unset no change alters any case without the mismatch, which was confirmed case for case.
  A stronger level of the mismatch, for which amendment 23 was made, was withdrawn: at that level the network raises
  alarms before any fault and the frozen framer took them as the onset, so it did not measure the twin's error. Its
  records are not included.
- **The records in `results/campaign/c4r/`** were made under manifest C4R, which adds instrumentation and two fixes
  that leave the reference arm computing as under C4H (`spec/memos/C4R_INSTRUMENTED_RUNS.md`). Its runner is recorded
  by hash in that manifest, not archived. Every manifest but C4V is kept as the record of what its runs used, and
  checks against the archived code only where the code has not changed since.
- **Every arm that calls no LLM reproduces exactly**, case for case, with one exception found: in case 2133 a rerun
  chose a different check of the same kind and reached a different outcome, probably because a tie between checks of
  equal value is broken by the order of a set, which Python's string hashing varies between runs. Setting
  `PYTHONHASHSEED=0` fixes that order for reruns. The fit cache is rebuilt on first use.
- **The LLM arms used gpt-5.6 under default sampling.** Their records hold each response and each call's latency and
  token counts. A rerun needs an OpenAI key in the environment (`OPENAI_API_KEY`) and will differ in detail.

The run scripts for each arm are `scripts/run_c4_record.sh` and `scripts/run_c4*.sh` (sealed cases and campaign),
`scripts/run_a20_test.sh` (documentation completion), `scripts/a19_qi_run.py` (question reading) and
`scripts/run_c4t.sh` (the twin with structural error, frozen agent; the fleet-judged runs are `exp2_run.py` with
`ADAGENT_MARGIN_FLEET=1`, as `spec/memos/AMENDMENT_24.md` records). The companion
gives the step-by-step procedure and the time each takes.

## Citing this archive

C. Janz, H. Yu, S. Taeb, R. Cai, Y. You and Y. Chen, "The diagnosis agent test bed: Simulator, agent, harness, sealed
cases and technical companion," version 1.0, Zenodo, 2026. doi:10.5281/zenodo.24005540,
https://doi.org/10.5281/zenodo.24005540

`CITATION.cff` gives the same in machine-readable form.

## Licence

Licensed under the Apache License, Version 2.0 (`LICENSE`, `NOTICE`).
