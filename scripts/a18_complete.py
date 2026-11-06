"""Amendment 18: documentation completion by the language model (spec/memos/AMENDMENT_18.md).

For one degradation (kind none, sig or modes; seed "diag" = the diagnosis arms' draw, as scripts/kgx_run.py, or
"dev" = the development draw), builds the documentation as the agent would hold it, asks the LLM once per equipment
type to fill its visible gaps (effects for modes documented without effects) and to add the fault modes it lacks,
validates the answers against the vocabularies and writes results/campaign/a18/completion_<kind>_<seed>.json.
The LLM never sees a case. --dry writes the prompts only (no API).

Run from the project root:   python scripts/a18_complete.py --kind sig --seed diag
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
from typing import Literal, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "results" / "campaign" / "a18"
PROMPT_VERSION = "doc-completion-a18-0.1"
DEVSEED = 20261101
SELF_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

SYSTEM = """You complete equipment documentation for the diagnosis system of an optical transport network. \
The documentation of one equipment type is given as its operator or vendor supplied it, in a structured form. \
Some of it may be missing: a fault mode may be named with no effects, and fault modes the equipment can suffer may \
be absent altogether. You never see a network event; you complete the documentation from what you know of such \
equipment.
Do two things.
(1) For every listed fault mode marked "effects: MISSING", give its effects.
(2) List the fault modes this equipment type can suffer that are not documented, choosing names only from the \
fault-mode vocabulary given. Add a mode only when you are confident this kind of equipment suffers it and it is \
not already listed; an empty list is a good answer when the documentation looks complete.
Write effects exactly as the documentation does: each effect names an effect from the effect vocabulary, a scope \
from the scope vocabulary and, where the effect appears somewhere other than the unit itself, a location from the \
location vocabulary. Follow the style of the documented modes of this and similar equipment. For an added mode \
give a one-sentence description, its nature, a rate of occurrence per year per unit, and the twin scenario (from \
the twin's catalogue) that models its physical change, or none when it has no physical effect on transmission."""


def vocab(kg_true):
    """The shared vocabularies: effects (knowledge/vocabulary.yaml), scope and location terms, fault-mode names and
    the twin's scenario catalogue, read from the documentation as loaded (the complete one: the vocabularies are the
    ontology and the twin's capabilities, not any vendor's list)."""
    import yaml
    v = yaml.safe_load((ROOT / "knowledge" / "vocabulary.yaml").read_text())
    effects = {k: (d or {}).get("meaning", "") for k, d in (v.get("effects") or {}).items()}
    scopes, wheres, modes, scen = set(), set(), {}, {}
    for u, m, d in kg_true.g.edges(data=True):
        if d.get("rel") != "hasFaultMode":
            continue
        modes.setdefault(m, d.get("description", ""))
        for e in d.get("effects") or []:
            scopes.add(e.get("scope", "all_channels"))
            if e.get("where"):
                wheres.add(e["where"])
        sc = d.get("twin_scenario")
        if sc:
            scen[sc["kind"] + (":transient" if sc.get("transient") else "")] = sc
    for n, d in kg_true.g.nodes(data=True):
        if d.get("cls") == "FaultMode":
            modes.setdefault(n, "")
    return {"effects": effects, "scopes": sorted(scopes), "wheres": sorted(wheres), "modes": dict(sorted(modes.items())),
            "scenarios": dict(sorted(scen.items()))}


def degrade(kg, kind: str, seed: str):
    import kgx_run
    if seed == "diag":
        return kgx_run.degraded(kg, kind)
    rng = random.Random(f"{DEVSEED}:{kind}")
    new = kgx_run._copy(kg)
    if kind == "none":
        return new, {}
    rep = {"sig": kgx_run.degrade_sig, "modes": kgx_run.degrade_modes}[kind](new, rng)
    return new, rep


def type_doc(kg, ty: str) -> str:
    n = kg.g.nodes[ty]
    L = [f"Equipment type {ty} (element class {n.get('element_class')}; {n.get('document', '')})"]
    for note in n.get("notes") or []:
        L.append(f"  note: {note}")
    L.append("  Documented fault modes:")
    fm = kg.out(ty, "hasFaultMode")
    if not fm:
        L.append("    none")
    for m, d in fm:
        eff = d.get("effects") or []
        es = "; ".join(f"{e['effect']} scope={e.get('scope', 'all_channels')}" + (f" where={e['where']}" if e.get("where") else "")
                       + (f" if={e['if']}" if e.get("if") else "") for e in eff) or "MISSING"
        L.append(f"    {m}: {d.get('description', '')} | nature={d.get('nature')} | effects: {es}"
                 + (f" | twin scenario: {d['twin_scenario']['kind']}" if d.get("twin_scenario") else ""))
    return "\n".join(L)


def schema(V):
    from pydantic import create_model
    Eff = Literal[tuple(V["effects"])]
    Sc = Literal[tuple(V["scopes"])]
    Wh = Literal[tuple(V["wheres"])]
    Mo = Literal[tuple(V["modes"])]
    Sn = Literal[tuple(list(V["scenarios"]) + ["none"])]
    E = create_model("Effect", effect=(Eff, ...), scope=(Sc, ...), where=(Optional[Wh], ...))
    G = create_model("Gap", fault_mode=(Mo, ...), effects=(list[E], ...))
    A = create_model("Added", fault_mode=(Mo, ...), description=(str, ...),
                     nature=(Literal["hard", "soft", "transient", "incipient"], ...),
                     rate_per_year_per_unit=(float, ...), effects=(list[E], ...), twin_scenario=(Sn, ...),
                     confidence=(float, ...))
    return create_model("Completion", gaps=(list[G], ...), added_modes=(list[A], ...))


def user_msg(kg, ty, V) -> str:
    return (type_doc(kg, ty) + "\n\nEffect vocabulary:\n" + "\n".join(f"  {k}: {m}" for k, m in V["effects"].items())
            + "\nScope vocabulary: " + ", ".join(V["scopes"]) + "\nLocation vocabulary: " + ", ".join(V["wheres"])
            + "\nFault-mode vocabulary: " + ", ".join(V["modes"])
            + "\nTwin scenario catalogue: " + ", ".join(V["scenarios"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=["none", "sig", "modes"])
    ap.add_argument("--seed", default="diag", choices=["diag", "dev"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    from adagent.harness import exp2
    case = exp2.make_case2(2190.0, 2000, gap_scale=1.0, records_complete=False, procedures_in_graph=False,
                           gap_calibrated=True, instrumentation="status_quo", mismatch=0, ladder=())
    kg_true = case.substrate.kg
    V = vocab(kg_true)
    kgd, rep = degrade(kg_true, kind=a.kind, seed=a.seed)
    types = sorted({u for u, _, d in kgd.g.edges(data=True) if d.get("rel") == "hasFaultMode"}
                   | {n for n, d in kgd.g.nodes(data=True) if d.get("cls") == "EquipmentType"})
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"completion_{a.kind}_{a.seed}.json"
    done = json.loads(f.read_text()) if f.exists() else {"types": {}}
    done.update({"kind": a.kind, "seed": a.seed, "degradation": rep, "prompt_version": PROMPT_VERSION,
                 "script_sha256": SELF_SHA, "system": SYSTEM})
    if a.dry:
        (OUT / f"prompts_{a.kind}_{a.seed}.txt").write_text("\n\n=====\n\n".join(user_msg(kgd, t, V) for t in types))
        print(f"{len(types)} prompts written")
        return
    from adagent.llm.client import LLMClient
    client = LLMClient("openai", os.environ.get("A18_MODEL") or None)
    S = schema(V)
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
        print(f"[{i}/{len(types)} {time.time() - t0:.0f}s] {ty}: {msg}", flush=True)
    done["model"] = client.model
    done["calls"] = [{k: c.get(k) for k in ("latency_s", "input_tokens", "output_tokens", "reasoning_tokens", "ok")}
                     for c in client.records()]
    f.write_text(json.dumps(done, indent=1))
    print(f"finished: {f.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
