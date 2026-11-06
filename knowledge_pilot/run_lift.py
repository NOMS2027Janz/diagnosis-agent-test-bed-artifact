"""Pilot of an LLM-built graph, step 3: from each vendor's extracted manuals to a lifted semantic model, following
"Portable Semantic Models and Ad Hoc Reconciliation by Cognitive Agents" (MASTER_REPORT, sections 3 and 7).

The study lifts from a data-model surface: concepts with a label, synonyms, a shallow kind, structural relations and
instances, with no gloss or example. Our sources are prose manuals, so this script first produces that surface (the
one step the study does not cover), then applies the study's own lift, unchanged, to write each concept's gloss and
example:

  3a. surface: an agent groups every wording the vendor's manuals use (as extracted in step 2) into concepts, one per
      meaning; each wording becomes a label or synonym of exactly one concept. Each concept gets a shallow kind (an
      RFC 9940 class where one applies), its relations to the vendor's other concepts and its instances (product
      codes, alarm names). No gloss or example is written here.
  3b. lift: reconcile.lift.lift_model (the study's code) writes the gloss and example of every concept from the
      surface alone.

    python knowledge_pilot/run_lift.py            # both vendors
    python knowledge_pilot/run_lift.py --dry      # the wording inventories only; no API calls

Writes knowledge_pilot/out/lifted/<vendor>.json (the lifted model, in the study's model shape) with
<vendor>.wordings.json (every wording and the concept it went to) and the effort of each call.
Key: secrets/openai.env (never printed).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))

from reconcile.model import Concept, SemanticModel          # noqa: E402  (the study's code)

PROMPT_VERSION = "pilot-surface-2"

# The categories of wording the extraction produced, with the shallow kind each falls under: RFC 9940 classes where
# one applies (Cause, Condition, Characteristic, Resource, Alarm), the pilot's own classes otherwise.
KINDS = ("Cause", "Condition", "Characteristic", "Resource", "Alarm", "Scope", "Location", "Availability", "Nature",
         "Severity", "Behaviour")


def wordings(manuals: list[dict]) -> dict:
    """{category: {wording: [context, ...]}} — every distinct wording the extraction recorded, with where it occurs."""
    out: dict = {k: {} for k in KINDS}

    def add(cat, w, ctx):
        w = (w or "").strip()
        if w:
            out[cat].setdefault(w, [])
            if ctx not in out[cat][w] and len(out[cat][w]) < 4:
                out[cat][w].append(ctx)

    for m in manuals:
        t = m["document_title"]
        for u in m["unit_types"]:
            add("Resource", u["unit"], f"{t}: unit {u['product_code']}")
            add("Resource", u.get("role_or_position"), f"{t}: position of {u['product_code']}")
        for mo in m["monitors"]:
            for q in mo["measures"]:
                add("Characteristic", q, f"{t}: measured by monitor {mo['name']}")
            add("Availability", mo["availability"], f"{t}: availability of monitor {mo['name']}")
        for b in m["behaviours"]:
            for w in b["when"]:
                add("Condition", w, f"{t}: arriving condition -> {'; '.join(b['result']) or 'nothing'}")
            for r in b["result"]:
                add("Condition", r, f"{t}: result of {'; '.join(b['when'])}")
        add("Behaviour", m.get("routing"), f"{t}: how channels are switched")
        for f in m["faults"]:
            add("Cause", f["name"], f"{t}: fault — {f['description']}")
            add("Nature", f["character"], f"{t}: character of fault {f['name']}")
            for e in f["effects"]:
                add("Condition", e["effect"], f"{t}: symptom of fault {f['name']}")
                add("Scope", e["scope"], f"{t}: extent of symptom '{e['effect']}' of {f['name']}")
                add("Location", e.get("where"), f"{t}: where symptom '{e['effect']}' of {f['name']} shows")
        for a in m["alarms"]:
            add("Alarm", a["id"], f"{t}: {a['raised_when']}")
            add("Severity", a["severity"], f"{t}: severity of {a['id']}")
            add("Condition", a.get("symptom"), f"{t}: raises alarm {a['id']}")
            for r in a["raised_by"]:
                add("Resource", r, f"{t}: raises alarm {a['id']}")
            for pc in a["probable_causes"]:
                add("Cause", pc["cause"], f"{t}: probable cause of alarm {a['id']} ({a['raised_when']})")
                add("Location", pc["where"], f"{t}: where the probable cause '{pc['cause']}' of {a['id']} lies")
            add("Location", a.get("reported_against"), f"{t}: what alarm {a['id']} is reported against")
    return {k: v for k, v in out.items() if v}


class _Rel(BaseModel):
    rel: str
    target: str


class _SurfaceConcept(BaseModel):
    id: str
    label: str
    kind: str
    synonyms: list[str]          # every OTHER given wording that denotes this concept, exactly as given
    relations: list[_Rel]
    instances: list[str]


class _Surface(BaseModel):
    concepts: list[_SurfaceConcept]


SURFACE_SYSTEM = (
    "You are given everything one equipment vendor's manuals say, as structured data in the vendor's own words, and "
    "an inventory of every distinct WORDING the vendor uses, grouped by category, each with where it occurs. Build "
    "the vendor's DATA-MODEL SURFACE: a set of concepts, ONE PER MEANING. Different wordings that denote the same "
    "thing in this vendor's documentation become ONE concept: choose the clearest as its label and list every other "
    "such wording, exactly as given, as its synonyms. The same fault, symptom or place is often worded differently in "
    "the fault descriptions, the alarm tables and the behaviour notes, and often with a location or a hedge attached: "
    "for instance 'the cooling fan of the adjacent shelf has stopped' and 'fan failure' name the same fault (where it "
    "lies is a separate Location concept), and 'the display stays dark' and 'no picture on the screen' the same "
    "symptom. Merge such "
    "wordings. Aim for the FEWEST concepts that keep every distinct meaning apart. Wordings that denote different "
    "things stay separate concepts, even if they share words (a power supply failure and a fan failure are different "
    "faults; a front-panel port and a backplane port are different things). A wording that bundles several things (for example a list of impairments) may stay a "
    "concept of its own. Every given wording must appear in exactly one concept, as its label or as a synonym, with "
    "its spelling unchanged. Give each concept a short id (lower case, dots or underscores), its kind (the category "
    "the wording came from), its relations to the vendor's OTHER concepts, using relation names such as has-fault "
    "(unit to fault), produces (fault to symptom), affects (symptom to scope), shows-at (symptom to location), "
    "raised-by (alarm to unit), raised-on (alarm to condition), probable-cause (alarm to fault), lies-at (fault to "
    "location, as a probable cause), measures (unit to characteristic), available-with (characteristic to "
    "availability), and its instances (product codes, alarm names exactly as raised). Do NOT write glosses, "
    "definitions or examples, and do not use any vocabulary but the vendor's own."
)


def surface(vendor: str, manuals: list[dict], inv: dict, client) -> tuple[SemanticModel, dict, dict]:
    user = json.dumps({"vendor": vendor, "manuals": manuals,
                       "wordings": {k: {w: ctx for w, ctx in v.items()} for k, v in inv.items()}}, indent=1)
    s = client.parse("surface", PROMPT_VERSION, SURFACE_SYSTEM, user, _Surface)
    rec = client.records()[-1]
    # every wording to exactly one concept; a wording the agent dropped becomes a concept of its own (counted)
    by_word, concepts, dropped = {}, [], []
    for c in s.concepts:
        words = [c.label] + [w for w in c.synonyms if w != c.label]
        kept = [w for w in words if w not in by_word]
        for w in kept:
            by_word[w] = c.id
        concepts.append(Concept(id=c.id, label=c.label, kind=c.kind, synonyms=tuple(w for w in kept if w != c.label),
                                relations=tuple(r.model_dump() for r in c.relations), instances=tuple(c.instances)))
    for cat, ws in inv.items():
        for w in ws:
            if w not in by_word:
                cid = f"{cat.lower()}.unassigned.{len(dropped)}"
                concepts.append(Concept(id=cid, label=w, kind=cat))
                by_word[w] = cid
                dropped.append(w)
    effort = {k: rec.get(k) for k in ("model", "input_tokens", "output_tokens", "reasoning_tokens", "latency_s")}
    return (SemanticModel(system=f"vendor {vendor}", dialect=f"{vendor} manuals", modules=(), concepts=concepts),
            {"by_wording": by_word, "dropped": dropped}, effort)


# ---------------------------------------------------------------------------------------------- surface v3
# The first two surfaces asked one agent call to sort all of a vendor's wordings (about 250) into concepts and to write
# their relations at the same time; consolidation was weak (about 1.8 concepts per meaning). Surface v3 splits the job:
#   (1) grouping, one call per category of wording (Cause, Condition, Location, ...), on that category's wordings and
#       where each occurs; a wording that lists several things becomes a set with parts;
#   (2) review, one call per category, on the groups from (1): which groups are the same thing and should be one;
#   (3) relations and instances, built deterministically from the extracted record (fault produces symptom, symptom
#       affects scope, alarm has probable cause, ...), not by the agent.
# The study's lift then runs unchanged on the result.
PROMPT_VERSION_V3 = "pilot-surface-3"

CATEGORY_MEANING = {
    "Cause": "the events or faults that give rise to a problem (RFC 9940 Cause)",
    "Condition": "an interpretation of what is observed on a resource: a symptom, a state, a defect (RFC 9940 Condition)",
    "Characteristic": "an observable or measurable aspect of a resource (RFC 9940 Characteristic)",
    "Resource": "an element of the network system: a unit, a card, a module, a role it is fitted in (RFC 9940 Resource)",
    "Alarm": "an alarm the equipment raises, by its identifier (RFC 9940 Alarm)",
    "Scope": "how much of a resource's traffic a condition affects",
    "Location": "where a cause lies or a symptom shows, relative to the unit that raised an alarm or saw a change",
    "Availability": "the monitoring option, licence or tier under which a measurement is reported",
    "Nature": "the temporal character of a fault: an outage, a degradation, a slow trend, a brief event",
    "Severity": "the severity an alarm is raised with",
    "Behaviour": "how a unit passes on or transforms what arrives at it",
}


class _Group(BaseModel):
    label: str
    members: list[str]           # the given wordings in this group, exactly as given
    parts: list[str]             # for a wording that lists several things: the labels of the groups it lists


class _Groups(BaseModel):
    groups: list[_Group]


class _Merges(BaseModel):
    merge: list[list[str]]       # each a set of group labels that refer to the same thing


GROUP_SYSTEM = (
    "You are given every distinct WORDING one equipment vendor's documentation uses for one category of thing, each "
    "with where it occurs. Group the wordings by meaning: ONE GROUP FOR EACH DISTINCT THING the vendor refers to. Put "
    "in one group every wording that refers to the same thing, wherever it occurs (a fault table, an alarm table, a "
    "list of probable causes, a behaviour note) and however it is phrased: a noun or a sentence, capitalised or not, "
    "with or without an article, active or passive, terse or explanatory. A wording that states the same thing together "
    "with where it happens or how much it affects belongs with the plain wording when that detail is the only "
    "difference: for instance 'no picture', 'the screen stays dark' and 'no picture on the second display' are one "
    "symptom, and 'fan failure', 'the fan has stopped' and 'a failed fan in the rear tray' are one fault. Keep apart "
    "wordings that refer to different things even when they share words: a fault and the symptom it produces, a "
    "component and the unit that holds it, one place and another, one measurement and another. A wording that lists "
    "several distinct things refers to a set: give it a group of its own and name under 'parts' the labels of the "
    "groups of the things it lists, where the documentation has them; otherwise leave 'parts' empty. Every given "
    "wording must be in exactly one group, spelled exactly as given; do not invent wordings. Choose as each group's "
    "label its clearest member. Aim for the FEWEST groups that keep every distinct meaning apart.")

REVIEW_SYSTEM = (
    "You are given groups of one equipment vendor's wordings for one category of thing; each group is meant to be one "
    "meaning. Find groups that refer to the SAME thing and should be one group, and list each such set by the groups' "
    "labels, exactly as given. Wordings that differ only in phrasing, or in an added statement of where or how much, "
    "refer to the same thing. Do not merge groups that refer to different things: a fault and the symptom it produces, "
    "a component and its unit, different places, different measurements, a set and one of its parts. Return an empty "
    "list if no groups should merge.")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48] or "x"


def surface_v3(vendor: str, manuals: list[dict], inv: dict, client) -> tuple[SemanticModel, dict, dict]:
    calls0 = len(client.records())
    concepts_by_cat, word_to_cid, dropped, invented, merges_applied = {}, {}, [], [], 0
    used_ids = set()
    for cat, ws in inv.items():
        user = json.dumps({"vendor": vendor, "category": cat, "category_meaning": CATEGORY_MEANING[cat],
                           "wordings": {w: ctx for w, ctx in ws.items()}}, indent=1)
        g = client.parse("surface-group", PROMPT_VERSION_V3, GROUP_SYSTEM, user, _Groups)
        groups, seen = [], set()
        for gr in g.groups:
            mem = []
            for w in gr.members:
                if w not in ws:
                    invented.append(w)
                elif w not in seen:
                    mem.append(w)
                    seen.add(w)
            if mem:
                label = gr.label if gr.label in mem else mem[0]
                groups.append({"label": label, "members": mem, "parts": list(gr.parts)})
        for w in ws:                                   # a wording the agent left out becomes a group of its own
            if w not in seen:
                groups.append({"label": w, "members": [w], "parts": []})
                dropped.append(w)
        # review: merge groups the agent finds to be the same thing
        if len(groups) > 1:
            user = json.dumps({"vendor": vendor, "category": cat, "category_meaning": CATEGORY_MEANING[cat],
                               "groups": [{"label": x["label"], "members": x["members"]} for x in groups]}, indent=1)
            m = client.parse("surface-review", PROMPT_VERSION_V3, REVIEW_SYSTEM, user, _Merges)
            by_label = {x["label"]: x for x in groups}
            for s in m.merge:
                live = [by_label[l] for l in s if l in by_label and by_label[l] in groups]
                if len(live) < 2:
                    continue
                head = live[0]
                for other in live[1:]:
                    head["members"] += [w for w in other["members"] if w not in head["members"]]
                    head["parts"] += [p for p in other["parts"] if p not in head["parts"]]
                    groups.remove(other)
                    by_label[other["label"]] = head
                    merges_applied += 1
        for x in groups:
            cid = f"{cat.lower()}.{_slug(x['label'])}"
            k = 2
            while cid in used_ids:
                cid = f"{cat.lower()}.{_slug(x['label'])}_{k}"
                k += 1
            used_ids.add(cid)
            x["id"] = cid
            for w in x["members"]:
                word_to_cid.setdefault((cat, w), cid)
        concepts_by_cat[cat] = groups

    def cid(cat, w):
        return word_to_cid.get((cat, (w or "").strip()))

    rel = {c["id"]: [] for g in concepts_by_cat.values() for c in g}
    inst = {c["id"]: [] for g in concepts_by_cat.values() for c in g}

    def link(a, r, b):
        if a and b and a != b and {"rel": r, "target": b} not in rel[a]:
            rel[a].append({"rel": r, "target": b})

    for cat, groups in concepts_by_cat.items():               # sets and their parts
        lab = {x["label"]: x["id"] for x in groups}
        for x in groups:
            for p in x["parts"]:
                link(x["id"], "comprises", lab.get(p))
    for m in manuals:                                          # structure from the extracted record
        unit_c = {}
        for u in m["unit_types"]:
            c = cid("Resource", u["unit"])
            unit_c[u["product_code"]] = unit_c[u["unit"]] = c
            if c and u["product_code"] not in inst[c]:
                inst[c].append(u["product_code"])
            link(c, "positioned-as", cid("Resource", u.get("role_or_position")))
        for mo in m["monitors"]:
            for q in mo["measures"]:
                for a in mo.get("applies_to") or []:
                    link(unit_c.get(a) or cid("Resource", a), "measures", cid("Characteristic", q))
                link(cid("Characteristic", q), "available-with", cid("Availability", mo["availability"]))
        for b in m["behaviours"]:
            for w in b["when"]:
                for r_ in b["result"]:
                    link(cid("Condition", w), "leads-to", cid("Condition", r_))
        for f in m["faults"]:
            fc = cid("Cause", f["name"])
            link(fc, "has-nature", cid("Nature", f["character"]))
            for e in f["effects"]:
                ec = cid("Condition", e["effect"])
                link(fc, "produces", ec)
                link(ec, "affects", cid("Scope", e["scope"]))
                link(ec, "shows-at", cid("Location", e.get("where")))
        for a in m["alarms"]:
            ac = cid("Alarm", a["id"])
            if ac and a["id"] not in inst[ac]:
                inst[ac].append(a["id"])
            link(ac, "raised-on", cid("Condition", a.get("symptom")))
            link(ac, "has-severity", cid("Severity", a["severity"]))
            link(ac, "reported-against", cid("Location", a.get("reported_against")))
            for r_ in a["raised_by"]:
                link(ac, "raised-by", cid("Resource", r_))
            for pc in a["probable_causes"]:
                cc = cid("Cause", pc["cause"])
                link(ac, "probable-cause", cc)
                link(cc, "lies-at", cid("Location", pc["where"]))
    concepts, by_word = [], {}
    for cat, groups in concepts_by_cat.items():
        for x in groups:
            concepts.append(Concept(id=x["id"], label=x["label"], kind=cat,
                                    synonyms=tuple(w for w in x["members"] if w != x["label"]),
                                    relations=tuple(rel[x["id"]]), instances=tuple(inst[x["id"]])))
            for w in x["members"]:
                by_word.setdefault(w, x["id"])
    recs = client.records()[calls0:]
    effort = {"calls": len(recs), "model": recs[-1].get("model") if recs else None,
              **{k: sum((r.get(k) or 0) for r in recs) for k in ("input_tokens", "output_tokens", "reasoning_tokens",
                                                                 "latency_s")},
              "merges_applied": merges_applied, "invented_dropped": len(invented)}
    return (SemanticModel(system=f"vendor {vendor}", dialect=f"{vendor} manuals", modules=(), concepts=concepts),
            {"by_wording": by_word, "dropped": dropped, "invented": invented}, effort)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", default="v2", choices=("v2", "v3"), help="the surface method (v3: grouped by "
                    "category, reviewed, relations from the record)")
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--tag", default="", help="a suffix for the output folder (lifted<tag>)")
    a = ap.parse_args()
    src = ROOT / "knowledge_pilot" / "out" / "extracted"
    dst = ROOT / "knowledge_pilot" / "out" / f"lifted{a.tag}"
    dst.mkdir(parents=True, exist_ok=True)
    client = None
    if not a.dry:
        from adagent.llm.client import LLMClient, _load_key
        client = LLMClient(provider="openai")
        os.environ.setdefault("OPENAI_API_KEY", _load_key("openai"))   # for the study's lift, which uses the SDK
    from reconcile.lift import lift_model
    for vdir in sorted(p for p in src.iterdir() if p.is_dir()):
        vendor = vdir.name
        if a.only and vendor != a.only:
            continue
        manuals = [json.loads(p.read_text()) for p in sorted(vdir.glob("*.json"))]
        inv = wordings(manuals)
        n = sum(len(v) for v in inv.values())
        print(f"{vendor}: {n} wordings in {len(inv)} categories " +
              ", ".join(f"{k} {len(v)}" for k, v in inv.items()), flush=True)
        if a.dry:
            continue
        sm, words, eff_s = (surface_v3 if a.surface == "v3" else surface)(vendor, manuals, inv, client)
        print(f"{vendor}: surface of {len(sm.concepts)} concepts ({len(words['dropped'])} wordings left out by the "
              f"agent, kept as concepts of their own)", flush=True)
        lifted, eff_l = lift_model(sm, os.environ.get("OPENAI_MODEL", "gpt-5.6"))
        print(f"{vendor}: lifted ({eff_l})", flush=True)
        model_json = lifted.to_dict(note=f"agent-lifted from {vendor}'s manuals (pilot steps 2 and 3)")
        (dst / f"{vendor}.json").write_text(json.dumps(model_json, indent=1))
        (dst / f"{vendor}.wordings.json").write_text(json.dumps({**words, "inventory": inv,
                                                                 "effort": {"surface": eff_s, "lift": eff_l}},
                                                                indent=1))


if __name__ == "__main__":
    main()
