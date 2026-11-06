"""Freeze Experiment 2: write spec/EXP2_MANIFEST.json, the SHA-256 of every file the experiment depends on (the
package source, the knowledge files, the Experiment 2 scripts, the settings memo) with the prompt and tool versions.
The held-out run must start from this state; scripts/exp2_freeze.py --check confirms nothing has changed since.

Run from the project root:  python scripts/exp2_freeze.py          (writes the manifest)
                            python scripts/exp2_freeze.py --check  (confirms nothing has changed since)
Amendment 1 (2026-10-17, orchestrator.v2 and the rules comparison, after the offline arms had run):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A1.json
Amendment 2 (2026-10-17, orchestrator.v3 and loop 0.3, for L-v3 and the confirmation on fresh cases):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A2.json
Amendment 3 (2026-10-17, the GNN extension and the model comparison; adds the trained model files):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A3.json
Amendment 4 (2026-10-17, corrections, a realistic twin and the clean rerun of every arm; supersedes the above):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A4.json
Amendment 4.1 (2026-10-18, the documented reading of checks; cases that reached the planner rerun):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A41.json
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A42.json
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_A42B.json
Experiment 3 (2026-10-19, peeling, the transient recogniser and the instrumentation branches; spec/PROTOCOL_EXP3.md):
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_X3.json
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_X3TR.json (with the
                            trained recogniser, for the transient arms)
                            python scripts/exp2_freeze.py --check --manifest spec/EXP2_MANIFEST_X3F.json (the agent as
                            built: peeling and the recogniser together)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C1.json (the
                            campaign to the NOMS 2027 paper, stage 1)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C2.json (the
                            capability ladder; also lists tests/test_ladder.py)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C3.json (spectral
                            monitoring and the multiple-fault specialist; also lists their tests, run script and model)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4.json (the fixed
                            agent of amendments 8 and 9; the same file list as C3)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4T.json (C4H with
                            amendment 22, for the twin with structural error)
                            python scripts/exp2_freeze.py --check --manifest spec/CAMPAIGN_MANIFEST_C4U.json (C4T with
                            amendment 23: drift cases posed in a world that raises standing alarms)
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
    out += sorted((ROOT / "scripts").glob("exp2_*.py")) + [ROOT / "spec" / "memos" / "EXP2_LOOP_SETTINGS.md"]
    out += sorted((ROOT / "scripts").glob("exp1_*.py")) + sorted((ROOT / "scripts").glob("run_a4*.sh"))
    out += [f for f in (ROOT / "models" / "gnn" / "ranker.npz", ROOT / "models" / "gnn" / "ranker.json",
                        ROOT / "scripts" / "gnn_train.py", ROOT / "scripts" / "analysis_provenance.py",
                        ROOT / "scripts" / "twin_fidelity.py") if f.exists()]
    if "X3" in " ".join(sys.argv):
        # Experiment 3: the run scripts; with X3TR (the transient arms) also the trained recogniser and the
        # scripts that made its data and trained it
        out += sorted((ROOT / "scripts").glob("run_x3*.sh"))
    if "CAMPAIGN" in " ".join(sys.argv):
        # the campaign to the NOMS 2027 paper: its run scripts and the spend analysis
        out += sorted((ROOT / "scripts").glob("run_c*.sh")) + [ROOT / "scripts" / "analysis_llm_spend.py"]
    if "MANIFEST_C2" in " ".join(sys.argv):
        # the capability ladder: its tests (the run script, the ladder's knowledge and code are already listed)
        out += [ROOT / "tests" / "test_ladder.py"]
    if "MANIFEST_C3" in " ".join(sys.argv) or "MANIFEST_C4" in " ".join(sys.argv):
        # spectral monitoring and the multiple-fault specialist: their tests, run script and trained model
        out += [ROOT / "tests" / "test_ladder.py", ROOT / "tests" / "test_spectral.py", ROOT / "scripts" / "run_spectral.sh"]
        out += [f for f in (ROOT / "models" / "gnn" / "multifault.npz", ROOT / "models" / "gnn" / "multifault.json")
                if f.exists()]
    if any(m in " ".join(sys.argv) for m in ("MANIFEST_C4F", "MANIFEST_C4G", "MANIFEST_C4H", "MANIFEST_C4T", "MANIFEST_C4U", "MANIFEST_C4V")):
        # amendments 13 and 14: the identity check, and the proposer under the degraded documentation with the
        # wrappers it builds on
        out += [ROOT / "scripts" / n for n in ("identity_c4f.py", "kgx_llm_run.py", "kgx_run.py", "llmline_run.py")]
    if any(m in " ".join(sys.argv) for m in ("MANIFEST_C4H", "MANIFEST_C4T", "MANIFEST_C4U", "MANIFEST_C4V")):
        out += [ROOT / "scripts" / n for n in ("qi_run.py", "ae_run.py")] + [ROOT / "tests" / "test_amendment16.py"]
    if any(m in " ".join(sys.argv) for m in ("MANIFEST_C4T", "MANIFEST_C4U", "MANIFEST_C4V")):
        # amendment 22: the structural mismatch carried to the case-day network
        out += [ROOT / "spec" / "memos" / "AMENDMENT_22.md"]
    if "MANIFEST_C4U" in " ".join(sys.argv) or "MANIFEST_C4V" in " ".join(sys.argv):
        # amendment 23: a drift is posed before any alarm of its own; alarms the network raises with no fault are standing
        out += [ROOT / "spec" / "memos" / n for n in ("AMENDMENT_22a.md", "AMENDMENT_23.md")]
    if "MANIFEST_C4V" in " ".join(sys.argv):
        # amendment 24 (report only): the margin levels judged against the fleet
        out += [ROOT / "spec" / "memos" / "AMENDMENT_24.md"]
    if "X3TR" in " ".join(sys.argv) or "X3F" in " ".join(sys.argv) or "CAMPAIGN" in " ".join(sys.argv):
        out += [f for f in (ROOT / "models" / "gnn" / "transient.npz", ROOT / "models" / "gnn" / "transient.json",
                            ROOT / "scripts" / "transient_gen.py", ROOT / "scripts" / "transient_train.py")
                if f.exists()]
    return [f for f in out if "__pycache__" not in f.parts and f.name != "exp2_freeze.py"]


def current() -> dict:
    from adagent.llm.client import PROVIDERS
    from adagent.tools.orchestrator_llm import PROMPT_VERSION as orchestrator
    from adagent.tools.loop import WinnowingLoop
    from adagent.tools.twin import GraphTwin
    return {"versions": {"orchestrator": orchestrator, "loop": WinnowingLoop.version, "twin": GraphTwin.version,
                         "model_default": PROVIDERS["openai"]["default"], "network_seed": 24, "history_seed": 1},
            "files": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files()}}


if __name__ == "__main__":
    # --manifest <path>: another manifest (an amendment); default the one the held-out offline arms ran under
    m = ROOT / (sys.argv[sys.argv.index("--manifest") + 1] if "--manifest" in sys.argv else "spec/EXP2_MANIFEST.json")
    if "--check" in sys.argv:
        old = json.loads(m.read_text())
        new = current()
        diff = [k for k in set(old["files"]) | set(new["files"]) if old["files"].get(k) != new["files"].get(k)]
        print("unchanged" if not diff and old["versions"] == new["versions"] else f"CHANGED: {sorted(diff)}")
        sys.exit(1 if diff else 0)
    m.write_text(json.dumps(current(), indent=1))
    print(f"written {m.relative_to(ROOT)}")
