"""Compare the trained model with the rules engine on a held-out replayed world, through the real policy.

    PYTHONPATH=backend;ml;data python -m model.evaluate --table ml/data/tables/dev_test.parquet

Both scorers see the same rows, and both go through backend/api/policy.py (corroboration gate,
amount caps, alert budget) in time order, so the comparison is in tiers, as the user would feel it.
Normal payments were subsampled in the replay; weights bring them back to their real share.
The alert budget only sees the kept rows, so it binds a little less often than in real life.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from api import rule_scorer
from api.policy import Policy
from features.build import FAMILY
from model import predict
from model.data import encode_frame
from model.metrics import evaluate

INTERRUPT = {"T2", "T3", "T4"}
PAUSE = {"T3", "T4"}


def raw_features(df: pd.DataFrame) -> list[dict]:
    keys = list(FAMILY)
    raw = df[keys].astype(object).where(df[keys].notna(), None)
    return [dict(zip(keys, row)) for row in raw.itertuples(index=False)]


def tiers(df: pd.DataFrame, feats: list[dict], scores: list[dict], cfg: dict | None = None) -> np.ndarray:
    """Tiers in time order through backend/api/policy.py; `cfg` replaces policy.yaml's numbers."""
    policy, out = Policy(), np.empty(len(df), dtype=object)
    if cfg is not None:
        policy.cfg = cfg
    control = (df["control_seen"].to_numpy(bool) if "control_seen" in df
               else ((df["stage"] == 3) | (df["remote_control"] == True)).to_numpy())  # noqa: E712
    for i in np.argsort(df["ts"].to_numpy(), kind="stable"):
        f, s = feats[i], scores[i]
        d = policy.decide(s, f, f"S{int(f['stage'])}", bool(control[i]), df["user_id"].iat[i], df["ts"].iat[i])
        out[i] = d.tier
    return out


def tier_metrics(df: pd.DataFrame, tier: np.ndarray) -> dict:
    y = df["label_is_scam"].to_numpy(bool)
    w = df["weight"].to_numpy(float)
    amt = df["amount"].to_numpy(float)
    inter = np.isin(tier, list(INTERRUPT))
    pause = np.isin(tier, list(PAUSE))
    first = y & (df["journey_pay_index"] == 0).to_numpy()
    ages = df["audit_age_band"].fillna("none").to_numpy()
    legit_fpr = {a: float(w[~y & inter & (ages == a)].sum() / w[~y & (ages == a)].sum())
                 for a in sorted(set(ages[~y]))}
    fpr_vals = [v for v in legit_fpr.values()]
    return {
        "scam_payments_interrupted": float(inter[y].mean()),
        "scam_payments_paused": float(pause[y].mean()),
        "scam_rupees_interrupted": float(amt[y & inter].sum() / amt[y].sum()),
        "pre_loss_catch_T2": float(inter[first].mean()) if first.any() else None,
        "legit_interruptions_per_1000": float(1000 * w[~y & inter].sum() / w[~y].sum()),
        "T3_precision": float(w[y & pause].sum() / w[pause].sum()) if pause.any() else None,
        "by_playbook_interrupted": {pb: float(inter[y & (df["playbook"] == pb).to_numpy()].mean())
                                    for pb in sorted(df.loc[y, "playbook"].dropna().unique())},
        "legit_interruption_rate_by_age_band": legit_fpr,
        "age_band_gap": (max(fpr_vals) / min(fpr_vals)) if fpr_vals and min(fpr_vals) > 0 else None,
        "tiers": {t: int((tier == t).sum()) for t in ("T0", "T1", "T2", "T3", "T4")},
    }


def score_both(df: pd.DataFrame, feats: list[dict]) -> dict[str, list[dict]]:
    """The rules engine's and the trained model's scores for every row, keyed by model version."""
    model = predict.load()
    p_ml, fam_ml = model.score_frame(encode_frame(df))
    playbooks = [rule_scorer._playbook(f) for f in feats]
    ml_scores = [{"p_scam": float(p_ml[i]), "family_scores": {k: float(v[i]) for k, v in fam_ml.items()},
                  "playbook": playbooks[i]} for i in range(len(df))]
    return {rule_scorer.MODEL_VERSION: [rule_scorer.score(f) for f in feats], model.version: ml_scores}


def run(table: Path) -> dict:
    df = pd.read_parquet(table).reset_index(drop=True)
    feats = raw_features(df)
    both = score_both(df, feats)
    rule_scores, ml_scores = both[rule_scorer.MODEL_VERSION], both[predict.load().version]
    model = predict.load()
    out = {"table": str(table), "rows": int(len(df)), "scam_payments": int(df.label_is_scam.sum())}
    for name, scores in ((rule_scorer.MODEL_VERSION, rule_scores), (model.version, ml_scores)):
        out[name] = {"scores": evaluate(df, np.array([s["p_scam"] for s in scores])),
                     "policy": tier_metrics(df, tiers(df, feats, scores))}
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    res = run(a.table)
    for name in (rule_scorer.MODEL_VERSION, predict.load().version):
        s, t = res[name]["scores"], res[name]["policy"]
        print(f"{name:<12} PR-AUC {s['pr_auc']:.3f} | scam payments interrupted {100 * t['scam_payments_interrupted']:5.1f}% "
              f"(rupees {100 * t['scam_rupees_interrupted']:5.1f}%), paused {100 * t['scam_payments_paused']:5.1f}% | "
              f"catch at 1st payment {100 * (t['pre_loss_catch_T2'] or 0):5.1f}% | "
              f"legit interruptions/1k {t['legit_interruptions_per_1000']:.2f} | "
              f"T3+ precision {100 * (t['T3_precision'] or 0):5.1f}% | tiers {t['tiers']}")
    out = a.out or Path(__file__).resolve().parents[1] / "runs" / f"evaluate_{a.table.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=lambda v: None if isinstance(v, float) and math.isnan(v) else v))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
