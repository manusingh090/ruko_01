"""Train the on-device model and ship it: device_model.json + device_vectors.json

    PYTHONPATH=backend;ml;data python -m model.device.train --table ml/data/tables/dev.parquet --test ml/data/tables/dev_test.parquet

Three small LightGBM models, one per phone-side family (C calls and remote control, L lures and the
task-scam episode, B own spending and the Pay screen; dataset/device_features.py), calibrated and
combined like the server model (model/train.py). Only rows where the device model runs are used
(some Scam Shield group on). The trees are exported as plain arrays that model/device/score.py and
DeviceModel.kt walk without LightGBM; the export is checked against LightGBM before it is written.

The local warning (a gentle notice on the phone, never a block; CLAUDE.md rule 10) fires when two
device families are lit and p_device is at least `min_p`, chosen out of fold so that at most
1 in 1,000 legit payments would see it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import GroupKFold

from dataset.device_features import FAMILIES as DEVICE_FAMILIES
from model.device import score as json_scorer
from model.reasons import DEVICE_REASON
from model.train import FAMILY_K, logit, strength

VERSION = "device-0.1"
FAMILIES = ("C", "L", "B")
PARAMS = dict(objective="binary", learning_rate=0.1, num_leaves=8, max_depth=4, min_data_in_leaf=30,
              feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1)
ROUNDS = 60
LIT = 0.5
MAX_REASONS = 3             # the app shows up to three reasons
WARN_PER_1000 = 1.0         # local warnings on legit payments, at most
MISSING = {"None": 0, "Zero": 1, "NaN": 2}
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "v0"


def device_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where the device model runs (dev_amount_log10 is set whenever any group is on)."""
    return df[df["dev_amount_log10"].notna()].reset_index(drop=True)


def matrix(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    return df[features].astype("float64")


def flatten(node: dict) -> dict:
    """LightGBM's nested tree -> flat arrays. Child index >= 0: split node; < 0: leaf ~index."""
    t = {"split_feature": [], "threshold": [], "default_left": [], "missing": [], "left": [], "right": [],
         "node_value": [], "leaf_value": []}

    def add(n: dict) -> int:
        if "split_feature" not in n:
            t["leaf_value"].append(n["leaf_value"])
            return ~(len(t["leaf_value"]) - 1)
        if n["decision_type"] != "<=":
            raise ValueError(f"only numerical splits are supported, got {n['decision_type']}")
        i = len(t["split_feature"])
        t["split_feature"].append(n["split_feature"])
        t["threshold"].append(n["threshold"])
        t["default_left"].append(bool(n["default_left"]))
        t["missing"].append(MISSING[n["missing_type"]])
        t["node_value"].append(n["internal_value"])
        t["left"].append(0)
        t["right"].append(0)
        t["left"][i] = add(n["left_child"])
        t["right"][i] = add(n["right_child"])
        return i

    add(node)
    return t


def fit(df: pd.DataFrame, folds: int = 5, seed: int = 0) -> tuple[dict, dict]:
    y = df["label_is_scam"].to_numpy(bool)
    w = df["weight"].to_numpy(float)
    groups = df["user_id"].to_numpy()
    prior = float(w[y].sum() / w.sum())
    feats = {f: list(DEVICE_FAMILIES[f]) for f in FAMILIES}
    oof = {f: np.zeros(len(df)) for f in FAMILIES}
    for k, (tr, te) in enumerate(GroupKFold(n_splits=folds).split(df, y, groups)):
        for f in FAMILIES:
            b = lgb.train({**PARAMS, "seed": seed + k}, lgb.Dataset(matrix(df, feats[f]).iloc[tr], y[tr], weight=w[tr]),
                          num_boost_round=ROUNDS)
            oof[f][te] = b.predict(matrix(df, feats[f]).iloc[te])
    isos = {f: IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(oof[f], y, sample_weight=w)
            for f in FAMILIES}
    cal = {f: isos[f].predict(oof[f]) for f in FAMILIES}
    z = np.column_stack([logit(cal[f]) for f in FAMILIES])
    comb = LogisticRegression(C=1.0, max_iter=2000).fit(z, y, sample_weight=w)
    p_oof = comb.predict_proba(z)[:, 1]
    lit_count = sum((strength(cal[f], prior) >= LIT).astype(int) for f in FAMILIES)

    # Local warning: the lowest min_p that keeps warnings on legit payments within budget.
    min_p = 0.99
    for cand in np.round(np.arange(0.50, 0.995, 0.01), 2):
        warn = (lit_count >= 2) & (p_oof >= cand)
        if 1000 * w[~y & warn].sum() / w[~y].sum() <= WARN_PER_1000:
            min_p = float(cand)
            break

    families = {}
    for f in FAMILIES:
        b = lgb.train({**PARAMS, "seed": seed}, lgb.Dataset(matrix(df, feats[f]), y, weight=w), num_boost_round=ROUNDS)
        dump = b.dump_model()
        families[f] = {"features": feats[f], "trees": [flatten(t["tree_structure"]) for t in dump["tree_info"]],
                       "iso_x": isos[f].X_thresholds_.tolist(), "iso_y": isos[f].y_thresholds_.tolist(),
                       "_booster": b}
    model = {"version": VERSION, "family_order": list(FAMILIES), "families": families, "prior": prior,
             "family_k": FAMILY_K, "lit": LIT,
             "combiner": {"coef": comb.coef_[0].tolist(), "intercept": float(comb.intercept_[0])},
             "reasons": {k: v for k, v in DEVICE_REASON.items() if any(k in feats[f] for f in FAMILIES)},
             "max_reasons": MAX_REASONS, "local_warning": {"min_families": 2, "min_p": min_p}}
    warn = (lit_count >= 2) & (p_oof >= min_p)
    first = y & (df["journey_pay_index"] == 0).to_numpy()
    oof_report = {"rows": int(len(df)), "scam_payments": int(y.sum()), "pr_auc": float(average_precision_score(y, p_oof, sample_weight=w)),
                  "warn_min_p": min_p, "warn_legit_per_1000": float(1000 * w[~y & warn].sum() / w[~y].sum()),
                  "warn_scam_payments": float(warn[y].mean()), "warn_first_scam_payment": float(warn[first].mean()) if first.any() else None,
                  "lit_on_scam": {f: float((strength(cal[f], prior) >= LIT)[y].mean()) for f in FAMILIES}}
    return model, oof_report


def booster_scores(model: dict, df: pd.DataFrame) -> dict:
    """The same outputs as model/device/score.py, from the LightGBM boosters (for large tables)."""
    cal, prior = {}, model["prior"]
    for f in model["family_order"]:
        fam = model["families"][f]
        p = fam["_booster"].predict(matrix(df, fam["features"]))
        cal[f] = np.interp(p, fam["iso_x"], fam["iso_y"])
    c = model["combiner"]
    zsum = c["intercept"] + sum(c["coef"][i] * logit(cal[f]) for i, f in enumerate(model["family_order"]))
    p_device = 1 / (1 + np.exp(-zsum))
    lit = sum((strength(cal[f], prior, model["family_k"]) >= model["lit"]).astype(int) for f in model["family_order"])
    w = model["local_warning"]
    return {"p": p_device, "warn": (lit >= w["min_families"]) & (p_device >= w["min_p"])}


def check_export(model: dict, df: pd.DataFrame, n: int = 400, seed: int = 0) -> float:
    """The JSON scorer must reproduce LightGBM's raw scores; returns the largest difference."""
    exported = json.loads(json.dumps(strip(model)))
    sample = df.sample(min(n, len(df)), random_state=seed)
    worst = 0.0
    for f in model["family_order"]:
        fam = model["families"][f]
        want = fam["_booster"].predict(matrix(sample, fam["features"]), raw_score=True)
        for row, expected in zip(features_of(sample), want):
            got = json_scorer.score(exported, row)["raw"][f]
            worst = max(worst, abs(got - expected))
    return worst


def features_of(df: pd.DataFrame) -> list[dict]:
    cols = [c for fam in DEVICE_FAMILIES.values() for c in fam]
    vals = df[cols].astype("float64").to_numpy()
    return [{c: (None if math.isnan(v) else float(v)) for c, v in zip(cols, row)} for row in vals]


def strip(model: dict) -> dict:
    """The JSON part of the model (drops the in-memory boosters)."""
    return {**model, "families": {f: {k: v for k, v in fam.items() if not k.startswith("_")}
                                  for f, fam in model["families"].items()}}


def vectors(model: dict, df: pd.DataFrame, seed: int = 0) -> list[dict]:
    """Inputs and the answers the phone must give: real rows of every kind plus edge cases."""
    rng = random.Random(seed)
    exported = json.loads(json.dumps(strip(model)))
    scam = df[df.label_is_scam]
    picks = []
    for pb in sorted(scam.playbook.dropna().unique()):
        rows = scam[scam.playbook == pb]
        picks += rows.sample(min(2, len(rows)), random_state=seed).index.tolist()
    normal = df[~df.label_is_scam]
    picks += normal.sample(min(14, len(normal)), random_state=seed).index.tolist()
    cases = [("table", row) for row in features_of(df.loc[picks])]
    base = {c: None for fam in DEVICE_FAMILIES.values() for c in fam}
    cases.append(("only the payment measured", {**base, "dev_amount_log10": 3.0, "dev_entry_typed": 1.0,
                                                "dev_entry_qr": 0.0, "dev_first_time_payee": 1.0, "dev_hist_n": 0.0,
                                                "dev_out_1h": 0.0, "dev_out_24h": 0.0}))
    cases.append(("every feature zero", {c: 0.0 for c in base}))
    cases.append(("coached call scam", {**base, "dev_call_active": 1.0, "dev_call_voip": 1.0, "dev_call_minutes": 38.0,
                                        "dev_remote_installed": 1.0, "dev_remote_min_ago": 25.0, "dev_screen_recorded": 1.0,
                                        "dev_accessibility": 0.0, "dev_sideloaded_24h": 0.0, "dev_amount_log10": 4.98,
                                        "dev_entry_typed": 1.0, "dev_entry_qr": 0.0, "dev_first_time_payee": 1.0,
                                        "dev_hist_n": 40.0, "dev_amount_to_p95": 12.0, "dev_amount_to_max": 6.0,
                                        "dev_sess_digits": 5.0, "dev_sess_median_gap_s": 2.4, "dev_sess_gap_vs_usual": 4.8,
                                        "dev_sess_app_switches": 3.0, "dev_sess_pay_screen_s": 212.0}))
    rng.shuffle(cases)
    out = []
    for name, feats in cases:
        s = json_scorer.score(exported, feats)
        out.append({"case": name, "features": feats, "expected": s})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--test", type=Path, default=None, help="a held-out world, for the report only")
    p.add_argument("--out", type=Path, default=ARTIFACTS)
    a = p.parse_args()
    t0 = time.perf_counter()
    df = device_rows(pd.read_parquet(a.table))
    model, report = fit(df)
    worst = check_export(model, df)
    if worst > 1e-9:
        raise SystemExit(f"export check failed: JSON scorer differs from LightGBM by {worst}")
    report["export_max_raw_diff"] = worst
    if a.test is not None:
        te = device_rows(pd.read_parquet(a.test))
        s = booster_scores(model, te)
        y, w = te.label_is_scam.to_numpy(bool), te.weight.to_numpy(float)
        first = y & (te.journey_pay_index == 0).to_numpy()
        report["held_out"] = {"table": str(a.test), "rows": int(len(te)), "scam_payments": int(y.sum()),
                              "pr_auc": float(average_precision_score(y, s["p"], sample_weight=w)),
                              "warn_legit_per_1000": float(1000 * w[~y & s["warn"]].sum() / w[~y].sum()),
                              "warn_scam_payments": float(s["warn"][y].mean()),
                              "warn_first_scam_payment": float(s["warn"][first].mean()) if first.any() else None}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "device_model.json").write_text(json.dumps(strip(model), separators=(",", ":")), encoding="utf-8")
    vecs = vectors(model, df)
    (a.out / "device_vectors.json").write_text(json.dumps({"version": VERSION, "tolerance": 1e-9, "vectors": vecs},
                                                          indent=1), encoding="utf-8")
    (a.out / "device_metrics.json").write_text(json.dumps({"version": VERSION, **report}, indent=2))
    size = (a.out / "device_model.json").stat().st_size / 1024
    print(f"{VERSION}: {sum(len(f['trees']) for f in model['families'].values())} trees, {size:.0f} KB, "
          f"export matches LightGBM to {worst:.1e}; local warning at p >= {model['local_warning']['min_p']}")
    print(f"out of fold: PR-AUC {report['pr_auc']:.3f}, warns on {100 * report['warn_scam_payments']:.1f}% of scam payments "
          f"({report['warn_legit_per_1000']:.2f} per 1,000 legit)")
    if "held_out" in report:
        h = report["held_out"]
        print(f"held out:    PR-AUC {h['pr_auc']:.3f}, warns on {100 * h['warn_scam_payments']:.1f}% of scam payments, "
              f"{100 * (h['warn_first_scam_payment'] or 0):.1f}% of first payments ({h['warn_legit_per_1000']:.2f} per 1,000 legit)")
    print(f"-> {a.out / 'device_model.json'}, device_vectors.json ({len(vecs)} cases) in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
