"""Experiment 1: exploratory analyses, outside the protocol (labelled as such in every output).

Run from the project root, after exp1_analysis.py:  python scripts/exp1_explore.py
Reads results/exp1/analysis_rows.json, the LLM arm files and results/exp1/equivalence/, and builds each case
again only to read its question, truth and deterministic frame (no LLM calls). Writes results/exp1/EXPLORATORY.md
and results/exp1/case_mix.jsonl.

1. Results by kind of anomaly: how it came to notice, whether any equipment alarm was raised, its nature, and
   whether it involved more than one element.
2. Where the reference LLM arm's misses come from: the truth's mode never proposed; proposed but no member of
   the truth's class among the grounded candidates (split into lost in grounding and placed elsewhere); or
   among the candidates but ranked below another.
3. Why the LLM framer loses: its frames against the deterministic frames on the same cases.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adagent.harness.cases import make_case            # noqa: E402
from adagent.tools.frame import DeterministicFramer    # noqa: E402

EXP = ROOT / "results" / "exp1"
NATURE = {"FIBRE_CUT": "hard", "CONNECTOR_PULLED": "hard", "AMP_PUMP_FAIL": "hard", "TX_LASER_FAIL": "hard",
          "CARD_FAIL": "hard", "OTN_SWITCH_FAIL": "hard", "CLIENT_PORT_FAIL": "hard", "XC_MISCONNECT": "hard",
          "SOP_TRANSIENT": "transient"}
LINES = ["L0_r1", "L0_r2", "L0_r3", "L1_r1", "pooled:offline", "pooled:offline+L0_r1", "abduction", "setcover",
         "procedures", "analogy", "alarm_correlation", "pagerank"]
HOW = {"loss": "Customer: loss of service", "interruption": "Customer: brief interruption",
       "pm_degraded": "Monitoring: degraded quality, no alarm", "alarm": "Operations: alarm"}


def case_mix(cases):
    f = EXP / "case_mix.jsonl"
    old = {json.loads(x)["case"]: json.loads(x) for x in f.open()} if f.exists() else {}
    out = {}
    for cid in cases:
        if cid in old:
            out[cid] = old[cid]
            continue
        day, seed = float(cid.split("-")[0][1:]), int(cid.split("-s")[1])
        c = make_case(day, seed)
        txt, t = c.question.text, c.truth
        how = ("alarm" if txt.startswith("Alarm") else "pm_degraded" if txt.startswith("Performance") else
               "loss" if "a loss of service" in txt else "interruption" if "brief interruption" in txt else "errors")
        out[cid] = {"case": cid, "how": how, "alarms": len(t.alarms) > 0, "nature": NATURE.get(t.fault.mode, "soft"),
                    "multi": len(t.fault.targets) > 1, "mode": t.fault.mode}
    f.write_text("".join(json.dumps(out[c]) + "\n" for c in sorted(out)))
    return out


def table(rows, mix, key, labels, L):
    groups = defaultdict(list)
    for cid, by in rows.items():
        groups[key(mix[cid])].append(by)
    order = [g for g in labels if g in groups]
    L.append("| Line | " + " | ".join(f"{labels[g]} (n={len(groups[g])})" for g in order) + " |")
    L.append("|---|" + "---|" * len(order))
    for ln in LINES:
        cells = []
        for g in order:
            v = [b[ln]["class_top1"] for b in groups[g] if ln in b]
            cells.append(f"{sum(v) / len(v):.2f}" if v else "-")
        L.append(f"| {ln} | " + " | ".join(cells) + " |")
    L.append("")


def main():
    R = json.loads((EXP / "analysis_rows.json").read_text())
    ok = [r for r in R if not r.get("error") and not r.get("unlocalisable")]
    rows = defaultdict(dict)
    for r in ok:
        rows[r["case"]][r["line"]] = r
    mix = case_mix(sorted({r["case"] for r in R}))
    L = ["# Experiment 1: exploratory analyses (outside the protocol)", "",
         "Class top-1: share of cases whose first-ranked candidate is in the truth's equivalence class. "
         f"Localisable cases only ({len(rows)} of 200). Small groups show direction, not significance.", "",
         "## 1. By kind of anomaly", "", "### How it came to notice", ""]
    table(rows, mix, lambda m: m["how"], HOW, L)
    L += ["### Whether any equipment alarm was raised", ""]
    table(rows, mix, lambda m: m["alarms"], {True: "Alarm raised", False: "No alarm at all"}, L)
    L += ["### Nature", ""]
    table(rows, mix, lambda m: m["nature"], {"hard": "Hard failure", "soft": "Soft degradation",
                                              "transient": "Transient"}, L)
    L += ["### Multiplicity", ""]
    table(rows, mix, lambda m: m["multi"], {False: "One element", True: "Both fibres of a cable"}, L)
    L += ["### Mode, for the cases with no alarm", ""]
    table({c: b for c, b in rows.items() if not mix[c]["alarms"]}, mix, lambda m: m["mode"],
          {m: m for m in sorted({mix[c]["mode"] for c in rows if not mix[c]["alarms"]})}, L)

    # 2. attribution of the reference arm's misses
    L += ["## 2. Where the reference LLM arm's misses come from", ""]
    detail = []
    for arm in ("L0_r1", "L0_r2", "L0_r3"):
        recs = {json.loads(x)["case"]: json.loads(x) for x in (EXP / f"{arm}.jsonl").open()}
        why = Counter()
        for cid, by in rows.items():
            if arm not in by or by[arm]["class_top1"]:
                continue
            eq = json.loads((EXP / "equivalence" / f"{cid}.json").read_text())
            members = set(eq["members"])
            modes = {m.split("@")[0] for m in members}
            rec = recs[cid]
            props = rec["trace"]["proposals"]
            cands = [c for c, _ in rec["candidates"]]
            if any(c in members or any(p in members for p in c.split("+")) for c in cands):
                k = "proposed, ranked below another candidate"
            elif not any(p["fault_mode"] in modes for p in props):
                k = "truth's fault mode never proposed"
            elif all(p["n_grounded"] == 0 for p in props if p["fault_mode"] in modes):
                k = "right fault mode, lost in grounding"
            else:
                k = "right fault mode, placed at the wrong location"
            why[k] += 1
            if arm == "L0_r1":
                detail.append(f"| {cid} | {mix[cid]['how']} | {eq['truth']} | {cands[0] if cands else '-'} | {k} |")
        L.append(f"- {arm}: {sum(why.values())} misses: " + "; ".join(f"{k} {v}" for k, v in why.most_common()))
    L += ["", "Reference arm, run 1, case by case:", "", "| Case | Came to notice | Truth | LLM's first | Why |",
          "|---|---|---|---|---|"] + sorted(detail) + [""]

    # 3. LLM framer against the deterministic framer
    L += ["## 3. LLM-built frames against deterministic frames (the 50 LLM-framer cases)", ""]
    l3 = {json.loads(x)["case"]: json.loads(x) for x in (EXP / "L3_r1.jsonl").open()}
    agg = defaultdict(list)
    lines = ["| Case | Truth | L3 right | L0 right | Symptoms (det / LLM) | Affected services (det / LLM) | "
             "Shared elements (det / LLM) | Quiet instruments (det / LLM) | Symptom kinds missing from LLM frame |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cid in sorted(l3):
        if cid not in rows or "L3_r1" not in rows[cid]:
            continue
        day, seed = float(cid.split("-")[0][1:]), int(cid.split("-s")[1])
        c = make_case(day, seed)
        det = DeterministicFramer(c.substrate).build(c.question).model_dump()
        llm = l3[cid]["frame"]
        def n_aff(f):
            return sum(1 for s in f["services"] if s.get("state") != "ok")
        kd = Counter(s["kind"] for s in det["symptoms"])
        kl = Counter(s["kind"] for s in llm["symptoms"])
        missing = ", ".join(f"{k} {kd[k] - kl.get(k, 0)}" for k in sorted(kd) if kd[k] > kl.get(k, 0)) or "-"
        r3, r0 = rows[cid]["L3_r1"]["class_top1"], rows[cid]["L0_r1"]["class_top1"]
        for k, a, b in (("sym", len(det["symptoms"]), len(llm["symptoms"])), ("aff", n_aff(det), n_aff(llm)),
                        ("shared", len(det["shared_elements"]), len(llm["shared_elements"])),
                        ("quiet", len(det["quiet_instruments"]), len(llm["quiet_instruments"]))):
            agg[(k, r3 < r0)].append((a, b))
        if r3 != r0:
            lines.append(f"| {cid} | {rows[cid]['L0_r1'].get('mode', '')} | {r3:.0f} | {r0:.0f} | "
                         f"{len(det['symptoms'])} / {len(llm['symptoms'])} | {n_aff(det)} / {n_aff(llm)} | "
                         f"{len(det['shared_elements'])} / {len(llm['shared_elements'])} | "
                         f"{len(det['quiet_instruments'])} / {len(llm['quiet_instruments'])} | {missing} |")
    for worse in (True, False):
        L.append(f"- Cases where the LLM framer did {'worse' if worse else 'as well or better'}: " + "; ".join(
            f"{k} mean det {sum(a for a, _ in v) / len(v):.1f}, LLM {sum(b for _, b in v) / len(v):.1f}"
            for (k, w), v in sorted(agg.items()) if w == worse))
    L += ["", "Cases where the two frames led to different results:", ""] + lines + [""]
    (EXP / "EXPLORATORY.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
