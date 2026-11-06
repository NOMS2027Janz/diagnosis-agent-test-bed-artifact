"""Train, calibrate and evaluate multiple-fault attribution (src/adagent/tools/multifault.py).

Run from the project root (no API calls):
    python scripts/multifault_train.py --train data/multifault_train.pkl --held data/multifault_held.pkl --epochs 25

The model starts from the generalist ranker's weights (models/gnn/ranker.npz) and is fine-tuned. Resumable: a
checkpoint is written after every epoch (data/<name>.ckpt.pkl) and a rerun continues from it.

Calibration: a temperature on the joint softmax over (element, fault class) and "no second fault", fitted on the
held-out cases. Abstention: the line abstains when the residual is within the combined accuracy, or when the
probability of "no second fault" exceeds a threshold set so that it abstains on at least 98 per cent of held-out
single-fault cases. Writes models/gnn/<name>.npz and <name>.json (settings, temperature, threshold, held-out top-1 and
top-5 on two-fault cases, abstention on single-fault cases, and the reliability of the stated probabilities).
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def reliability(p, held, T, predict):
    """Stated against observed probability of the model's top answer (a proposal, or "no second fault")."""
    bins = np.linspace(0, 1, 6)
    pts = []
    for c in held:
        r = predict(p, c, T, k=1)
        top_i, top_cl, top_p = r["top"][0] if r["top"] else (None, None, 0.0)
        if r["p_noise"] >= top_p:
            pts.append((r["p_noise"], not c["y"]))
        else:
            pts.append((top_p, int(r["order"][0]) in c["y"]))
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = [(s, o) for s, o in pts if lo <= s < hi or (hi == 1.0 and s == 1.0)]
        if sel:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "stated": round(float(np.mean([s for s, _ in sel])), 3),
                         "observed": round(float(np.mean([o for _, o in sel])), 3)})
    return rows


def main():
    from adagent.tools import multifault as MF
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--held", required=True)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--name", default="multifault")
    a = ap.parse_args()
    t0 = time.time()
    tr = MF.load_cases(a.train)
    he = MF.load_cases(a.held)
    print(f"loaded {len(tr)} training and {len(he)} held-out cases ({time.time() - t0:.0f} s)", flush=True)
    losses, ep_s = [], []
    ck = ROOT / "data" / f"{a.name}.ckpt.pkl"           # resumable: a checkpoint after every epoch
    state = None
    if ck.exists():
        state = pickle.loads(ck.read_bytes())
        losses = state.pop("losses", [])
        ep_s = state.pop("epoch_seconds", [])
        print(f"resuming after epoch {state['epoch']}", flush=True)
    tick = [time.time()]

    def save(st):
        ep_s.append(round(time.time() - tick[0], 1))
        tick[0] = time.time()
        ck.write_bytes(pickle.dumps({**st, "losses": losses, "epoch_seconds": ep_s}))

    p = MF.train(tr, epochs=a.epochs, hidden=a.hidden, layers=a.layers, lr=a.lr,
                 log=lambda s: (losses.append(s), print(f"{s} ({time.time() - tick[0]:.0f} s)", flush=True)),
                 state=state, on_epoch=save)
    t1 = time.time()
    temp = MF.fit_temperature(p, he)
    thr = MF.abstain_threshold(p, he, temp)
    ev = MF.evaluate(p, he, temp, thr)
    meta = {"version": "0.1", "train_cases": len(tr), "held_cases": len(he), "epochs": a.epochs,
            "hidden": a.hidden, "layers": a.layers, "lr": a.lr, "init": "ranker", "loss": losses[-3:],
            "temperature": round(temp, 4), "abstain_threshold": round(thr, 6), "held_out": ev,
            "reliability": reliability(p, he, temp, MF.predict), "epoch_seconds": ep_s,
            "calibration_seconds": round(time.time() - t1), "seconds": round(time.time() - t0)}
    MF.save(p, meta, a.name)
    print(json.dumps(meta, indent=1), flush=True)


if __name__ == "__main__":
    main()
