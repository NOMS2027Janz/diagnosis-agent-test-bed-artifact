"""Train, calibrate and evaluate the transient event recogniser (src/adagent/tools/transient.py).

Run from the project root (no API calls):
    python scripts/transient_train.py --train data/transient_train.pkl --held data/transient_held.pkl --epochs 25

Calibration: a temperature on the kind head, fitted on the held-out cases. Abstention: the line abstains when the
probability of "no transient" exceeds a threshold set so that it abstains on at least 98 per cent of held-out cases
with no transient. Writes models/gnn/transient.npz and transient.json (settings, temperature, threshold, held-out
results by kind, and the reliability of the kind head's stated probabilities).
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
    bins = np.linspace(0, 1, 6)
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        st, ok = [], []
        for c in held:
            pk = predict(p, c, T)["p_kind"]
            k = int(np.argmax(pk))
            if lo <= pk[k] < hi or (hi == 1.0 and pk[k] == 1.0):
                st.append(float(pk[k]))
                ok.append(k == int(c["y_kind"]))
        if st:
            rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(st), "stated": round(float(np.mean(st)), 3),
                         "observed": round(float(np.mean(ok)), 3)})
    return rows


def main():
    from adagent.tools import transient as T
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--held", required=True)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=3)
    ap.add_argument("--name", default="transient")
    a = ap.parse_args()
    t0 = time.time()
    tr = T.load_cases(a.train)
    he = T.load_cases(a.held)
    print(f"loaded {len(tr)} training and {len(he)} held-out cases", flush=True)
    losses = []
    ck = ROOT / "data" / f"{a.name}.ckpt.pkl"           # resumable: a checkpoint after every epoch
    state = None
    if ck.exists():
        state = pickle.loads(ck.read_bytes())
        losses = state.pop("losses", [])
        print(f"resuming after epoch {state['epoch']}", flush=True)

    def save(st):
        ck.write_bytes(pickle.dumps({**st, "losses": losses}))

    p = T.train(tr, epochs=a.epochs, hidden=a.hidden, layers=a.layers,
                log=lambda s: (losses.append(s), print(s, flush=True)), state=state, on_epoch=save)
    temp = T.fit_temperature(p, he)
    thr = T.abstain_threshold(p, he, temp)
    ev = T.evaluate(p, he, temp, thr)
    meta = {"version": "0.1", "train_cases": len(tr), "held_cases": len(he), "epochs": a.epochs,
            "hidden": a.hidden, "layers": a.layers, "loss": losses[-3:], "temperature": round(temp, 4),
            "abstain_threshold": round(thr, 6), "held_out": ev,
            "reliability": reliability(p, he, temp, T.predict), "seconds": round(time.time() - t0)}
    T.save(p, meta, a.name)
    print(json.dumps(meta, indent=1), flush=True)


if __name__ == "__main__":
    main()
