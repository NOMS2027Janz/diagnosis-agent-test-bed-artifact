# Building the knowledge graph from vendor documentation: the pilot

The pilot reported in "Building the Knowledge Graph from Vendor Documentation" (docs/explainers). It applies the method
of "Portable Semantic Models and Ad Hoc Reconciliation by Cognitive Agents" to vendor manuals written in each vendor's
own words.

- `prose/VA`, `prose/VB`, `prose/VC`: the twelve manuals, four per vendor, in three house styles; VC is the held-out
  vendor, written after the grouping step was fixed.
- `run_extract.py`: step 1, each manual read by an LLM into a structured record in the vendor's words
  (`out/extracted`).
- `run_lift.py`: step 2, the surface (wordings grouped into concepts; `--surface v3` for the revised grouping), and
  step 3, the study's lift (`out/lifted`, `out/lifted_v2`, `out/lifted_v3`).
- `run_reconcile.py`: step 4, the study's reconciliation under the reference conditions none, constructed and given
  (`out/reconciled*`).
- `reconcile/`: the study's code, unchanged (see `reconcile/README.md`).
- `gold.py`, `targets.py`, `score.py`: the gold standard, written from the equipment packages before any output was
  read, and the scoring (`out/scores.json`, `out/scores_vc.json`).
- Runs: `scripts/pilot_lift_reconcile.sh`, `scripts/pilot_v2.sh`, `scripts/pilot_v3.sh`, `scripts/pilot_vc.sh`. Each
  calls an LLM API; each run costs under $10. The key is read from `secrets/openai.env`, which is never committed.
