"""Pilot of an LLM-built graph, step 4: reconciliation, following "Portable Semantic Models and Ad Hoc Reconciliation
by Cognitive Agents" (MASTER_REPORT, sections 17 and 19.6), with the study's code unchanged (knowledge_pilot/reconcile).

Three pairs of lifted models are reconciled:
  VA-op  vendor VA's lifted model (step 3) against the operator's model (our documented vocabulary, a fixture lift);
  VB-op  vendor VB's against the operator's;
  VA-VB  the two vendors' lifted models directly.
each under the study's three reference conditions (section 19.6):
  none         no shared reference (OpenAIAgentStack, both cognitive);
  constructed  the agents build a thin shared reference from the two models, then bind through it
               (reconcile.construct.construct_reference, then bind_through_reference);
  given        bind through a given thin reference: the operator's vocabulary published as one, each entry carrying
               an RFC 9940 class (bind_through_reference).

    python knowledge_pilot/run_reconcile.py              # every pair and condition (9 reconciliations)
    python knowledge_pilot/run_reconcile.py --dry        # the models' sizes only

Writes knowledge_pilot/out/reconciled/<pair>.<condition>.json: the proposed correspondences with confidence, the
residual where the stack reports one, the constructed reference where there is one, and the effort.
Key: secrets/openai.env (never printed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from reconcile.model import Concept, SemanticModel           # noqa: E402  (the study's code, unchanged)
from reconcile.reference import Reference, ReferenceEntry    # noqa: E402
from targets import RFC9940, LOCAL, concepts as op_concepts  # noqa: E402

OUT = ROOT / "knowledge_pilot" / "out"


def operator_model() -> SemanticModel:
    cs = []
    for cls, items in op_concepts().items():
        for c in items:
            cs.append(Concept(id=f"op.{c['id']}", label=c["label"], kind=cls, gloss=c["gloss"],
                              example=c.get("example", ""), synonyms=tuple(c.get("synonyms", ())),
                              ref=c["id"], relations=tuple(c.get("relations", ())), instances=()))
    return SemanticModel(system="operator", dialect="operator shared vocabulary", modules=(), concepts=cs)


def operator_reference() -> Reference:
    entries = []
    for cls, items in op_concepts().items():
        rfc = cls if cls in RFC9940 else ""
        for c in items:
            entries.append(ReferenceEntry(id=c["id"], label=c["label"], definition=c["gloss"],
                                          example=c.get("example", ""), cls=(f"RFC 9940 {rfc}" if rfc else cls)))
    return Reference(id="operator-vocabulary", kind="given", entries=entries)


TAG = ""


def lifted(vendor: str) -> SemanticModel:
    return SemanticModel.from_json(OUT / f"lifted{TAG}" / f"{vendor}.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--pairs", default="VA-op,VB-op,VA-VB")
    ap.add_argument("--conditions", default="none,constructed,given")
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5.6"))
    ap.add_argument("--tag", default="", help="read lifted<tag>, write reconciled<tag>")
    a = ap.parse_args()
    global TAG
    TAG = a.tag
    dst = OUT / f"reconciled{a.tag}"
    dst.mkdir(parents=True, exist_ok=True)
    op = operator_model()
    models = {"op": op}
    for v in ("VA", "VB", "VC"):            # VC: the held-out vendor
        if (OUT / f"lifted{TAG}" / f"{v}.json").exists():
            models[v] = lifted(v)
    print("models: " + ", ".join(f"{k} {len(m.concepts)} concepts" for k, m in models.items()), flush=True)
    if a.dry:
        return
    from adagent.llm.client import _load_key
    os.environ.setdefault("OPENAI_API_KEY", _load_key("openai"))
    from reconcile.construct import bind_through_reference, construct_reference
    from reconcile.stacks.agent_openai import OpenAIAgentStack
    given = operator_reference()
    for pair in a.pairs.split(","):
        x, y = pair.split("-")
        if x not in models or y not in models:
            print(f"{pair}: a model is missing; skipped", flush=True)
            continue
        A, B = models[x], models[y]
        for cond in a.conditions.split(","):
            rec = {"pair": pair, "condition": cond, "model": a.model}
            try:
                if cond == "none":
                    stack = OpenAIAgentStack(use_reference=False, model=a.model)
                    result, effort = stack.infer(A, B, None, "both_cognitive")
                    rec["correspondences"] = [c.model_dump() for c in result.correspondences]
                    rec["residual_a"], rec["residual_b"] = result.residual_a, result.residual_b
                    rec["effort"] = effort
                else:
                    ref, eff_c = (construct_reference(A, B, a.model) if cond == "constructed" else (given, {}))
                    res, eff_b = bind_through_reference(A, B, ref, a.model, placement="both_cognitive")
                    rec["correspondences"] = [c.model_dump() for c in res.correspondences]
                    rec["effort"] = {**eff_c, **eff_b}
                    if cond == "constructed":
                        rec["reference"] = [e.__dict__ | {"synonyms": list(e.synonyms)} for e in ref.entries]
                print(f"{pair} {cond}: {len(rec['correspondences'])} correspondences ({rec['effort']})", flush=True)
            except Exception as e:      # noqa: BLE001 — one failed call must not stop the others
                rec["error"] = str(e)[:300]
                print(f"{pair} {cond}: FAILED {rec['error']}", flush=True)
            (dst / f"{pair}.{cond}.json").write_text(json.dumps(rec, indent=1, default=str))


if __name__ == "__main__":
    main()
