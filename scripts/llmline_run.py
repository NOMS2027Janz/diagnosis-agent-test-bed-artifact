"""The agent with its LLM proposer as a generation line (campaign to the NOMS 2027 paper; spec/PROTOCOL_CAMPAIGN.md,
amendment 6, "the agent with the LLM proposer on the sealed cases").

Arm A-full-cd-llm is arm A-full-cd (the agent with the "can this decide?" step, status quo instrumentation) with one
change: the LLM proposer (src/adagent/tools/proposer_llm.py, prompt proposer-0.5, the model and prompt measured in
the generation study) joins the six offline generation lines on the first pass, at the same weight as each of them.
Nothing else changes: no setting is tuned, the planner is the advisor, and the ledger alone commits. The agent's code
is not changed either: this wrapper adds the line to the lines the harness builds, so the C2 manifest still holds.

One structured call per case (the proposer), to the study's main model (gpt-5.6 unless --model is given). The call,
its tokens and its latency are recorded with the case; the key is read by the client from secrets/ and never
written anywhere.

Run from the project root:
    ADAGENT_RECORD_RUN=1 python scripts/llmline_run.py --seeds 4000-4319 --workers 6
Each job writes results/campaign/record/A-full-cd-llm/s<seed>.json; finished jobs are skipped, so the command
resumes (a resumed case is called again only if it had not finished).

--fake <dir> replays, instead of calling the model, the first proposals an LLM-alone run recorded for each case
(<dir>/s<seed>.json): a test of the plumbing on the diagnosis cases, with no API calls. --fake-empty makes the
proposer abstain on every case: then the arm must reproduce A-full-cd exactly (the wrapper's control).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

ARM, BASE = "A-full-cd-llm", "A-full-cd"
_client = None
_seed = None


class FakeClient:
    """Stands in for the model: returns recorded proposals (or none) in the proposer's schema."""
    provider, model = "fake", "replay"

    def __init__(self, replay_dir):
        self.dir = Path(replay_dir) if replay_dir else None
        self._records: list = []

    def parse(self, role, prompt_version, system, user, schema):
        data = {"assessment": "", "proposals": [], "probability_other": 1.0}
        if self.dir is not None:
            f = self.dir / f"s{_seed}.json"
            if f.exists():
                t = json.loads(f.read_text())["llm_alone"]["turns"][0]
                keep = ("fault_mode", "location_kind", "term", "anchor_symptom", "anchor_service", "element",
                        "probability", "rationale")
                data = {"assessment": t.get("assessment", ""), "probability_other": t.get("probability_other", 0.0),
                        "proposals": [{k: p.get(k) for k in keep} for p in t.get("proposals", [])]}
        self._records.append({"role": role, "ok": True, "input_tokens": 0, "output_tokens": 0, "latency_s": 0.0})
        return schema.model_validate(data)

    def records(self):
        return self._records


def install():
    """Add the LLM proposer to the lines the harness builds for every case in this worker."""
    from adagent.harness import exp2
    from adagent.tools.grounding import GroundingTool
    from adagent.tools.proposer_llm import LLMProposer
    orig = exp2.lines_for

    def lines_for(case, *a, **k):
        lines = orig(case, *a, **k)
        kg = case.substrate.kg
        prop = LLMProposer(_client, kg, GroundingTool(kg))
        exp2._llm_line = prop
        return {**lines, "llm_proposer": prop}
    exp2.lines_for = lines_for


def job(args):
    day, seed, outdir, fake, fake_empty = args
    global _client, _seed
    f = Path(outdir) / ARM / f"s{seed}.json"
    t0 = time.time()
    try:
        from adagent.harness import exp2
        if _client is None:
            if fake or fake_empty:
                _client = FakeClient(None if fake_empty else fake)
            else:
                from adagent.llm.client import LLMClient
                _client = LLMClient(os.environ.get("LLMLINE_PROVIDER", "openai"), os.environ.get("LLMLINE_MODEL") or None)
            install()
        _seed = seed
        n0 = len(_client.records())
        row = exp2.run_job(BASE, day, seed)
        row["arm"] = ARM
        prop = getattr(exp2, "_llm_line", None)
        row["llm_proposer"] = prop.trace if prop is not None else None
        row["llm"] = {"provider": _client.provider, "model": _client.model}
        row["llm_calls"] = [{k: c.get(k) for k in ("role", "latency_s", "input_tokens", "output_tokens",
                                                   "reasoning_tokens", "ok", "error")}
                            for c in _client.records()[n0:]]
        f.write_text(json.dumps(row, default=str))
        err = f.with_suffix(".error.json")
        if err.exists():
            err.write_text("")            # superseded by a successful run (files are not deleted)
        sc = row["score"]
        return f"{ARM:<14} s{seed}  {row['case']:<28} {sc['outcome']:<26} effort={sc['effort_h']:.1f}h  {time.time() - t0:.0f}s"
    except Exception as e:  # noqa: BLE001
        f.with_suffix(".error.json").write_text(json.dumps({"arm": ARM, "seed": seed, "error": repr(e)[:500],
                                                           "trace": traceback.format_exc()[-3000:]}))
        return f"{ARM:<14} s{seed}  FAILED {repr(e)[:120]}"


def seeds_arg(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--day", type=float, default=2190.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="results/campaign/record")
    ap.add_argument("--model", default=None)
    ap.add_argument("--fake", default=None, help="replay recorded LLM-alone proposals from this directory (test)")
    ap.add_argument("--fake-empty", action="store_true", help="the proposer abstains (control)")
    a = ap.parse_args()
    os.environ.setdefault("EXP2_FIT_CACHE", str(ROOT / "results" / "_fitcache"))
    if a.model:
        os.environ["LLMLINE_MODEL"] = a.model
    seeds = seeds_arg(a.seeds)
    sealed = [s for s in seeds if 4000 <= s <= 4319]
    if sealed and os.environ.get("ADAGENT_RECORD_RUN") != "1":
        raise SystemExit("seeds 4000-4319 are sealed: set ADAGENT_RECORD_RUN=1 for the record run")
    sealed2 = [s for s in seeds if 7000 <= s <= 7319]
    if sealed2 and os.environ.get("ADAGENT_RECORD_RUN_C4") != "1":
        raise SystemExit("seeds 7000-7319 are sealed: set ADAGENT_RECORD_RUN_C4=1 for the C4 record run")
    sealed += sealed2
    if sealed and (a.fake or a.fake_empty):
        raise SystemExit("no test runs on the sealed seeds")
    if sealed:
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "exp2_freeze.py"), "--check", "--manifest",
                            str(ROOT / "spec" / "CAMPAIGN_MANIFEST_C2.json")], cwd=ROOT)
        if r.returncode != 0:
            raise SystemExit("the code differs from the C2 manifest: stopped")
    outdir = ROOT / a.out
    (outdir / ARM).mkdir(parents=True, exist_ok=True)
    fake = str(ROOT / a.fake) if a.fake else None
    jobs = [(a.day, s, str(outdir), fake, a.fake_empty) for s in seeds if not (outdir / ARM / f"s{s}.json").exists()]
    print(f"{ARM}: {len(jobs)} jobs to run on {a.workers} workers", flush=True)
    t0 = time.time()
    if a.workers <= 1:
        for i, j in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] " + job(j), flush=True)
    else:
        with mp.get_context("spawn").Pool(a.workers, maxtasksperchild=4) as pool:
            for i, line in enumerate(pool.imap_unordered(job, jobs), 1):
                print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.0f} min] " + line, flush=True)
    print(f"finished in {(time.time() - t0) / 60:.0f} min", flush=True)


if __name__ == "__main__":
    main()
