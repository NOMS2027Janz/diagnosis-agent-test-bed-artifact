"""Freeze Experiment 1: write spec/EXP1_MANIFEST.json, the SHA-256 of every file the experiment depends on (the
package source, the knowledge files, the experiment scripts) with the versions of the prompts and tools.
Every run records the manifest's digest; the analysis checks that all runs share one.

Run from the project root:  python scripts/exp1_freeze.py          (writes the manifest)
                            python scripts/exp1_freeze.py --check  (confirms nothing has changed since)
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def files() -> list[Path]:
    out = sorted((ROOT / "src" / "adagent").rglob("*.py")) + sorted((ROOT / "knowledge").rglob("*.yaml"))
    out += sorted((ROOT / "scripts").glob("exp1_*.py"))
    return [f for f in out if "__pycache__" not in f.parts]


def current() -> dict:
    from adagent.tools.frame_llm import PROMPT_VERSION as framer
    from adagent.tools.grounding import GroundingTool
    from adagent.tools.procedures import ProcedureTool
    from adagent.tools.proposer_llm import PROMPT_VERSION as proposer
    from adagent.llm.client import PROVIDERS
    return {"versions": {"proposer": proposer, "framer": framer, "grounding": GroundingTool.version,
                         "procedures": ProcedureTool.version, "model_default": PROVIDERS["openai"]["default"],
                         "network_seed": 24, "history_seed": 1},
            "files": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files()}}


if __name__ == "__main__":
    m = ROOT / "spec" / "EXP1_MANIFEST.json"
    if "--check" in sys.argv:
        old = json.loads(m.read_text())
        new = current()
        diff = [k for k in set(old["files"]) | set(new["files"]) if old["files"].get(k) != new["files"].get(k)]
        print("unchanged" if not diff and old["versions"] == new["versions"] else f"CHANGED: {sorted(diff)}")
        sys.exit(1 if diff else 0)
    m.write_text(json.dumps(current(), indent=1))
    print(f"written {m.relative_to(ROOT)}")
