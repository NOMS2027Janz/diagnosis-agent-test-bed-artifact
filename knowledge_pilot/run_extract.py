"""Pilot of an LLM-built graph (Section 23.1.2 of "The Diagnosis Agent Test Bed: Results"), step 2: extraction.

An LLM reads each vendor's prose manual (knowledge_pilot/prose/<vendor>/*.md) and writes what it says as a
structured model in the VENDOR'S OWN TERMS: the faults by the vendor's names, their symptoms, scopes and locations in
the vendor's words, the monitors by the vendor's parameter names. Nothing here knows the operator's shared
vocabulary or the framework's fault-mode identifiers; mapping the vendor's terms onto those is the reconciliation
(run_reconcile.py).

    python knowledge_pilot/run_extract.py                      # every manual
    python knowledge_pilot/run_extract.py --only VB            # one vendor

Writes knowledge_pilot/out/extracted/<vendor>/<manual>.json and a call log. Key: secrets/openai.env (never printed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adagent.llm.client import LLMClient          # noqa: E402

PROMPT_VERSION = "pilot-extract-1"


class UnitType(BaseModel):
    product_code: str
    unit: str                      # the vendor's name for the kind of unit
    role_or_position: Optional[str]
    line_rate_or_client_rate: Optional[str]
    odu_mapping: Optional[str]


class DatasheetItem(BaseModel):
    item: str
    value: str


class Monitor(BaseModel):
    name: str                      # the vendor's parameter or monitor name
    measures: list[str]            # each quantity it reports, in the vendor's words
    availability: str              # the option, licence or standard condition under which it exists
    applies_to: list[str]          # product codes, or [] for every unit of the manual
    location: Optional[str]
    alarm_only: bool               # used only to raise alarms, not reported as values


class Behaviour(BaseModel):
    applies_to: list[str]
    when: list[str]                # the condition(s) arriving at the unit, in the vendor's words
    result: list[str]              # what leaves the unit or is declared, in the vendor's words; [] if nothing
    threshold: Optional[str]       # a size limit on when this applies, if stated
    note: Optional[str]


class Effect(BaseModel):
    effect: str                    # one atomic symptom in the vendor's words (split compound symptoms)
    scope: str                     # how much of the traffic it affects, in the vendor's words
    where: Optional[str]           # where it shows, if not at the faulty unit itself


class Fault(BaseModel):
    name: str                      # the vendor's name for the fault
    applies_to: list[str]          # product codes, or [] for every unit of the manual
    description: str
    effects: list[Effect]
    character: str                 # the vendor's word for its character (hard, soft, incipient, degradation ...)
    rate_per_year_fraction: Optional[float]   # e.g. 1 % per year -> 0.01
    rate_per: Optional[str]                   # per what: unit, card, port, degree, channel, switch ...


class ProbableCause(BaseModel):
    cause: str                     # the fault, in the vendor's words
    where: str                     # where it lies relative to the unit raising the alarm, in the vendor's words


class Alarm(BaseModel):
    id: str                        # the alarm name exactly as the equipment raises it
    raised_by: list[str]           # unit kinds, roles or product codes that raise it
    severity: str
    monitor: Optional[str]         # the monitor whose reading raises it, if one is named
    symptom: Optional[str]         # the symptom that raises it, in the vendor's words (e.g. "no light at the input")
    raised_when: str
    probable_causes: list[ProbableCause]
    suppression: Optional[str]
    reported_against: Optional[str]


class Manual(BaseModel):
    vendor: str
    document_title: str
    standards: list[str]
    unit_types: list[UnitType]
    datasheet: list[DatasheetItem]
    monitors: list[Monitor]
    behaviours: list[Behaviour]
    faults: list[Fault]
    alarms: list[Alarm]
    routing: Optional[str]
    troubleshooting_notes: list[str]


SYSTEM = (
    "You read equipment vendors' alarm and troubleshooting manuals for optical transport networks and write down "
    "exactly what a manual says, as structured data. Use the manual's own words and names throughout: do not "
    "translate them into any other vocabulary, do not normalise them and do not add knowledge the manual does not "
    "state. Split compound symptoms into atomic effects (\"low output and lower OSNR on every channel\" is two "
    "effects, each with scope \"every channel\"). Give alarm names exactly as written. Give each probable cause of "
    "an alarm as a separate entry with its location relative to the unit raising the alarm. Record what is not "
    "stated as null or an empty list."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="a vendor (VA, VB or VC)")
    ap.add_argument("--provider", default="openai")
    a = ap.parse_args()
    client = LLMClient(provider=a.provider)
    out_root = ROOT / "knowledge_pilot" / "out" / "extracted"
    log = []
    for p in sorted((ROOT / "knowledge_pilot" / "prose").glob("*/*.md")):
        vendor = p.parent.name
        if a.only and vendor != a.only:
            continue
        t0 = time.time()
        m = client.parse("extract", PROMPT_VERSION, SYSTEM, p.read_text(), Manual)
        dst = out_root / vendor / (p.stem + ".json")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(m.model_dump(), indent=1))
        rec = client.records()[-1]
        log.append({"manual": str(p.relative_to(ROOT)), "seconds": round(time.time() - t0, 1),
                    **{k: rec.get(k) for k in ("model", "input_tokens", "output_tokens", "reasoning_tokens")}})
        print(f"{p.name}: {len(m.faults)} faults, {len(m.alarms)} alarms, {len(m.monitors)} monitors "
              f"({log[-1]['seconds']} s, {rec.get('input_tokens')} in, {rec.get('output_tokens')} out)", flush=True)
    (out_root / "calls.json").write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
