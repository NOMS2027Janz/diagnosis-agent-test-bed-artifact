"""Amendment 20: documentation completion with comparable-equipment templates (spec/memos/AMENDMENT_20.md).

As amendment 18 (scripts/a18_complete.py), with three changes, all made before any amendment-20 result:
- each fault mode documented without effects is shown with the effects the documentation gives for the same mode on
  comparable equipment (the same element class; any other type if none), as templates;
- the location vocabulary is shown with the documentation's own uses of each term, and a location is to be given
  only where comparable documentation gives one;
- draws: "dev1" ... "dev12" (development draws), "test" (the test draw, seed fixed 2 Nov 2026 before any result),
  "diag" (the kgx_run draw).
Writes results/campaign/a20/completion_<kind>_<draw>.json. The LLM never sees a case. --dry writes prompts only.

Run from the project root:   python scripts/a20_complete.py --kind sig --draw dev4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import a18_complete as A18  # noqa: E402  (vocab, schema, the amendment-18 type description)

OUT = ROOT / "results" / "campaign" / "a20"
PROMPT_VERSION = "doc-completion-a20-0.1"
DEVSEED = 20261101
TESTSEED = 20261102
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DRAWS = ["diag", "test"] + [f"dev{i}" for i in range(1, 13)]

SYSTEM = """You complete equipment documentation for the diagnosis system of an optical transport network. \
The documentation of one equipment type is given as its operator or vendor supplied it, in a structured form. \
Some of it may be missing: a fault mode may be named with no effects, and fault modes the equipment can suffer may \
be absent altogether. You never see a network event; you complete the documentation from what you know of such \
equipment and from how comparable equipment is documented.
Do two things.
(1) For every listed fault mode marked "effects: MISSING", give its effects. Where templates are shown (the same \
fault mode as documented for comparable equipment), follow them: give the same effects, scopes and locations unless \
you know this equipment differs. Give a location only where the templates or the documentation's conventions give \
one for such an effect; a location names where an effect is seen when that is not the unit itself, and a wrong \
location is worse than none.
(2) List the fault modes this equipment type can suffer that are not documented, choosing names only from the \
fault-mode vocabulary given. Add a mode only when you are confident this kind of equipment suffers it and it is \
not already listed; an empty list is a good answer when the documentation looks complete.
Write effects exactly as the documentation does: each effect names an effect from the effect vocabulary, a scope \
from the scope vocabulary and, only where needed, a location from the location vocabulary. For an added mode give \
a one-sentence description, its nature, a rate of occurrence per year per unit, and the twin scenario (from the \
twin's catalogue) that models its physical change, or none when it has no physical effect on transmission."""


def degrade(kg, kind: str, draw: str):
    import kgx_run
    if draw == "diag":
        return kgx_run.degraded(kg, kind)
    seed = f"{TESTSEED}:{kind}" if draw == "test" else f"{DEVSEED}:{kind}:{int(draw[3:])}"
    new = kgx_run._copy(kg)
    if kind == "none":
        return new, {}
    rep = {"sig": kgx_run.degrade_sig, "modes": kgx_run.degrade_modes}[kind](new, random.Random(seed))
    return new, rep


def _fmt(eff):
    return "; ".join(f"{e['effect']} scope={e.get('scope', 'all_channels')}" + (f" where={e['where']}" if e.get("where") else "")
                     for e in eff)


def templates(kg, ty: str) -> str:
    """For each of the type's modes documented without effects: the same mode's documented effects on comparable
    equipment (same element class), else on any other type. From the documentation as held."""
    g = kg.g
    cls = g.nodes[ty].get("element_class")
    L = []
    for m, d in kg.out(ty, "hasFaultMode"):
        if d.get("effects"):
            continue
        same, other = [], []
        for u, v, e in g.edges(data=True):
            if e.get("rel") == "hasFaultMode" and v == m and u != ty and e.get("effects"):
                (same if g.nodes[u].get("element_class") == cls else other).append((u, e["effects"]))
        use = same or other
        if use:
            L.append(f"  Templates for {m} ({'comparable equipment' if same else 'other equipment; none comparable'}):")
            L += [f"    {u}: {_fmt(e)}" for u, e in use[:4]]
        else:
            L.append(f"  Templates for {m}: none documented elsewhere.")
    return ("\nTemplates (the same fault mode as documented for other equipment):\n" + "\n".join(L)) if L else ""


def where_uses(kg) -> str:
    """Each location term with up to three of the documentation's own uses (type, mode, effect)."""
    uses = {}
    for u, m, d in kg.g.edges(data=True):
        if d.get("rel") == "hasFaultMode":
            for e in d.get("effects") or []:
                if e.get("where"):
                    uses.setdefault(e["where"], []).append(f"{u} {m} {e['effect']}")
    return "\n".join(f"  {w}: e.g. " + "; ".join(sorted(set(x))[:3]) for w, x in sorted(uses.items()))


def user_msg(kg, ty, V) -> str:
    return (A18.type_doc(kg, ty) + templates(kg, ty)
            + "\n\nEffect vocabulary:\n" + "\n".join(f"  {k}: {m}" for k, m in V["effects"].items())
            + "\nScope vocabulary: " + ", ".join(V["scopes"])
            + "\nLocation vocabulary, with the documentation's own uses:\n" + where_uses(kg)
            + "\nFault-mode vocabulary: " + ", ".join(V["modes"])
            + "\nTwin scenario catalogue: " + ", ".join(V["scenarios"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["none", "sig", "modes"])
    ap.add_argument("--draw", required=True, choices=DRAWS)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    from adagent.harness import exp2
    case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                           gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
    kg_true = case.substrate.kg
    V = A18.vocab(kg_true)
    kgd, rep = degrade(kg_true, a.kind, a.draw)
    types = sorted({u for u, _, d in kgd.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
                   | {n for n, d in kgd.g.nodes(data=True) if d.get("cls") == "EquipmentType"})
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"completion_{a.kind}_{a.draw}.json"
    done = json.loads(f.read_text()) if f.exists() else {"types": {}}
    done.update({"kind": a.kind, "draw": a.draw, "degradation": rep, "prompt_version": PROMPT_VERSION,
                 "script_sha256": SELF_SHA, "system": SYSTEM})
    if a.dry:
        (OUT / f"prompts_{a.kind}_{a.draw}.txt").write_text("\n\n=====\n\n".join(user_msg(kgd, t, V) for t in types))
        print(f"{len(types)} prompts written")
        return
    from adagent.llm.client import LLMClient
    client = LLMClient("openai", os.environ.get("A18_MODEL") or None)
    S = A18.schema(V)
    t0 = time.time()
    for i, ty in enumerate(types, 1):
        if ty in done["types"]:
            continue
        try:
            out = client.parse("doc_completion", PROMPT_VERSION, SYSTEM, user_msg(kgd, ty, V), S).model_dump()
            done["types"][ty] = out
            msg = f"{len(out['gaps'])} gaps, {len(out['added_modes'])} added"
        except Exception as e:  # noqa: BLE001
            msg = f"FAILED {repr(e)[:120]}"
        f.write_text(json.dumps(done, indent=1))
        print(f"[{i}/{len(types)} {time.time() - t0:.0f}s] {a.kind} {a.draw} {ty}: {msg}", flush=True)
    done["model"] = client.model
    done["calls"] = [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens", "reasoning_tokens", "ok")}
                     for c in client.records()]
    f.write_text(json.dumps(done, indent=1))
    print(f"finished: {f.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
