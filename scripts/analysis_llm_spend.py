"""Tokens and estimated spend of LLM arms: python scripts/analysis_llm_spend.py <arm dir> [<arm dir> ...].
Price: $4 per million input tokens and $20 per million output tokens (the study's main model, as listed)."""
import glob
import json
import sys

PIN, POUT = 4.0, 20.0
for d in sys.argv[1:]:
    rows = [json.load(open(f)) for f in glob.glob(f"{d}/s*.json") if not f.endswith(".error.json")]
    calls = [c for r in rows for c in r.get("llm_calls", [])]
    tin = sum(c.get("input_tokens") or 0 for c in calls)
    tout = sum(c.get("output_tokens") or 0 for c in calls)
    usd = tin / 1e6 * PIN + tout / 1e6 * POUT
    out = {}
    for r in rows:
        o = r["score"]["outcome"]
        out[o] = out.get(o, 0) + 1
    n = max(len(rows), 1)
    print(f"{d}: {len(rows)} cases, {len(calls)} calls ({sum(not c.get('ok') for c in calls)} failed), "
          f"{tin:,} input and {tout:,} output tokens, about ${usd:.2f} (${usd / n:.2f} a case); outcomes {out}")
