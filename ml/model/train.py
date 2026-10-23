"""Train the server scorer: four LightGBM family models + a logistic combiner -> server_model.json

    PYTHONPATH=backend;ml;data python -m model.train --table ml/data/tables/dev.parquet --out ml/model/artifacts/v0

1. One LightGBM per family (C, L, P, B), each on that family's features only (the FAMILY map in
   backend/features/build.py), so each output is evidence from that family alone.
2. Each family's out-of-fold output (5 folds grouped by user) is calibrated with isotonic
   regression into P(scam | this family's evidence).
3. The policy needs family scores where "lit" (>= 0.5) means real evidence. A family score is the
   family's likelihood ratio LR (how much its evidence alone multiplies the odds of a scam) as
   LR / (LR + K): lit when the family alone makes a scam at least K times likelier than usual.
4. A logistic regression combines the calibrated families and the journey stage into p_scam.
5. The phone's verdict scores (device_c/l/b, c2-0.8) are hidden on a random VERDICT_DROPOUT share of
   rows. In the replay the verdict is missing only when every Scam Shield switch is off, so without
   this the model learns "no verdict = nothing to see" and an app that sends raw signals but no
   verdict (older build, model asset missing) gets T0 even on Meena's digital arrest.
Final family models are refit on all rows. Ground truth is used only as the training label.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold, cross_val_predict

from model.data import SERVER_FAMILIES, STAGE_FEATURES, encode_frame
from model.metrics import evaluate, summary_line
from model.reasons import REASON

VERSION = "lgbm-0.2"
FAMILIES = ("C", "L", "P", "B")
FAMILY_K = 10.0     # a family is lit when its evidence alone makes a scam >= 10x likelier
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=20,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1)
ROUNDS = 200
CLIP = 1e-4
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
VERDICT = ("device_c", "device_l", "device_b")
VERDICT_DROPOUT = 0.3


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.log(p / (1 - p))


def strength(p: np.ndarray | float, prior: float, k: float = FAMILY_K) -> np.ndarray | float:
    """Family score: likelihood ratio of the family's evidence, squashed as LR / (LR + K)."""
    p = np.clip(p, CLIP, 1 - CLIP)
    lr = (p / (1 - p)) / (prior / (1 - prior))
    return lr / (lr + k)


def combiner_inputs(cal: dict[str, np.ndarray], stage: np.ndarray) -> np.ndarray:
    return np.column_stack([logit(cal[f]) for f in FAMILIES] + [np.nan_to_num(stage, nan=0.0)])


def train(table: Path, folds: int = 5, seed: int = 0) -> tuple[dict, pd.DataFrame, np.ndarray]:
    df = pd.read_parquet(table)
    x = encode_frame(df)
    hide = np.random.default_rng(seed).random(len(x)) < VERDICT_DROPOUT
    x.loc[hide, [c for c in VERDICT if c in x.columns]] = np.nan
    y = df["label_is_scam"].to_numpy(bool)
    w = df["weight"].to_numpy(float)
    groups = df["user_id"].to_numpy()
    prior = float(w[y].sum() / w.sum())
    feats = {f: [c for c in SERVER_FAMILIES[f] if c in x.columns] for f in FAMILIES}
    splits = list(GroupKFold(n_splits=folds).split(x, y, groups))

    oof = {f: np.zeros(len(df)) for f in FAMILIES}
    for k, (tr, te) in enumerate(splits):
        for f in FAMILIES:
            b = lgb.train({**PARAMS, "seed": seed + k}, lgb.Dataset(x[feats[f]].iloc[tr], y[tr], weight=w[tr]),
                          num_boost_round=ROUNDS)
            oof[f][te] = b.predict(x[feats[f]].iloc[te])

    calibrators, cal = {}, {}
    for f in FAMILIES:
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(oof[f], y, sample_weight=w)
        calibrators[f] = iso
        cal[f] = iso.predict(oof[f])
    stage = x[STAGE_FEATURES[0]].to_numpy() if STAGE_FEATURES else np.zeros(len(df))
    z = combiner_inputs(cal, stage)
    comb = LogisticRegression(C=1.0, max_iter=2000)
    oof_p = cross_val_predict(comb, z, y, groups=groups, cv=GroupKFold(n_splits=folds),
                              method="predict_proba", params={"sample_weight": w})[:, 1]
    comb.fit(z, y, sample_weight=w)

    families = {}
    for f in FAMILIES:
        final = lgb.train({**PARAMS, "seed": seed}, lgb.Dataset(x[feats[f]], y, weight=w), num_boost_round=ROUNDS)
        iso = calibrators[f]
        families[f] = {"features": feats[f], "booster": final.model_to_string(),
                       "iso_x": iso.X_thresholds_.tolist(), "iso_y": iso.y_thresholds_.tolist()}
    model = {
        "version": VERSION, "trained_on": str(table), "rows": int(len(df)), "scam_payments": int(y.sum()),
        "scam_journeys": int(df.loc[y, "journey_id"].nunique()), "prior": prior, "family_k": FAMILY_K,
        "verdict_dropout": VERDICT_DROPOUT,
        "families": families,
        "combiner": {"inputs": [*FAMILIES, "stage"], "coef": comb.coef_[0].tolist(), "intercept": float(comb.intercept_[0])},
        "reasons": REASON,
    }
    lit = {f: float(np.average(strength(cal[f], prior) >= 0.5, weights=None)) for f in FAMILIES}
    model["oof"] = {"lit_share_on_scam": {f: float((strength(cal[f], prior) >= 0.5)[y].mean()) for f in FAMILIES},
                    "lit_share_on_legit": {f: float(np.average((strength(cal[f], prior) >= 0.5)[~y], weights=w[~y]))
                                           for f in FAMILIES},
                    "lit_share_all": lit}
    return model, df, oof_p


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--out", type=Path, default=ARTIFACTS / "v0")
    a = p.parse_args()
    t0 = time.perf_counter()
    model, df, oof_p = train(a.table)
    m = evaluate(df, oof_p)
    model["oof"]["metrics"] = m
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / "server_model.json"
    path.write_text(json.dumps(model, separators=(",", ":"), default=lambda v: None if isinstance(v, float) and math.isnan(v) else v))
    print(summary_line(f"{VERSION} out-of-fold", m))
    print("lit on scam payments:", {f: f"{100 * v:.0f}%" for f, v in model["oof"]["lit_share_on_scam"].items()},
          "| on legit:", {f: f"{100 * v:.1f}%" for f, v in model["oof"]["lit_share_on_legit"].items()})
    print(f"-> {path} ({path.stat().st_size / 1024:.0f} KB) in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
