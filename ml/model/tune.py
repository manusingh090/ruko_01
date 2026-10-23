"""Tune policy.yaml's tier thresholds on a replayed world -> a proposal for backend/api/policy.yaml.

    PYTHONPATH=backend;ml;data python -m model.tune --table ml/data/tables/dev_calib.parquet --test ml/data/tables/dev_test.parquet

For each scorer (rules-0.6, which the engine uses today, and the trained model), every combination of
T1 / T2 / T3 `min_p` goes through the real policy (backend/api/policy.py: corroboration gate, amount
caps, alert budget) on the tuning world. The winner interrupts the most scam rupees while legit
interruptions stay within budget (default 4 per 1,000, under the deck's 5) and T3+ precision is at
least 1 in 4. It is then checked on a world neither the model nor the tuner saw.

Output: `artifacts/v0/policy_proposal_<scorer>.yaml`, the current policy.yaml with only the tier
numbers and the version changed. Adopting it is backend's call (CLAUDE.md rule 6), and the numbers
must be re-tuned whenever the simulated world changes.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import re
from pathlib import Path

import pandas as pd

from api.policy import Policy
from model.evaluate import raw_features, score_both, tier_metrics, tiers

POLICY_YAML = Path(__file__).resolve().parents[2] / "backend" / "api" / "policy.yaml"
ARTIFACTS = Path(__file__).resolve().parent / "artifacts" / "v0"
GRID = {"T1": (0.2, 0.3, 0.4), "T2": (0.3, 0.4, 0.5, 0.6, 0.7), "T3": (0.5, 0.6, 0.7, 0.75, 0.8, 0.9)}
MIN_T3_PRECISION = 0.25   # deck slide 7: at least 1 in 4 pauses is a real scam


def candidates(base: dict) -> list[dict]:
    t4 = base["tiers"]["T4"]["min_p"]
    out = []
    for t1, t2, t3 in itertools.product(GRID["T1"], GRID["T2"], GRID["T3"]):
        if t1 < t2 < t3 <= t4:
            cfg = copy.deepcopy(base)
            cfg["tiers"]["T1"]["min_p"], cfg["tiers"]["T2"]["min_p"], cfg["tiers"]["T3"]["min_p"] = t1, t2, t3
            out.append(cfg)
    return out


def summary(m: dict) -> dict:
    keys = ("scam_rupees_interrupted", "scam_payments_interrupted", "scam_payments_paused", "pre_loss_catch_T2",
            "legit_interruptions_per_1000", "T3_precision")
    return {k: m[k] for k in keys}


def pick(results: list[dict], budget: float) -> dict | None:
    ok = [r for r in results if r["metrics"]["legit_interruptions_per_1000"] <= budget
          and (r["metrics"]["T3_precision"] is None or r["metrics"]["T3_precision"] >= MIN_T3_PRECISION)]
    return max(ok, key=lambda r: (r["metrics"]["scam_rupees_interrupted"], r["metrics"]["scam_payments_paused"],
                                  -r["metrics"]["legit_interruptions_per_1000"])) if ok else None


def proposal_yaml(tiers_min_p: dict[str, float], version: str) -> str:
    """policy.yaml with only the tier min_p values and the version changed (comments kept)."""
    text = POLICY_YAML.read_text(encoding="utf-8")
    text = re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M)
    for tier, p in tiers_min_p.items():
        text = re.sub(rf"^(  {tier}: \{{min_p: )[0-9.]+", rf"\g<1>{p:.2f}", text, count=1, flags=re.M)
    return text


def run(table: Path, test: Path | None, budget: float) -> dict:
    base = Policy().cfg
    df = pd.read_parquet(table).reset_index(drop=True)
    feats = raw_features(df)
    out = {"table": str(table), "budget_legit_per_1000": budget, "current": {t: base["tiers"][t]["min_p"] for t in base["tiers"]}}
    held = None
    if test is not None:
        te = pd.read_parquet(test).reset_index(drop=True)
        te_feats = raw_features(te)
        held = (te, te_feats, score_both(te, te_feats))
    for scorer, scores in score_both(df, feats).items():
        results = []
        for cfg in candidates(base):
            m = tier_metrics(df, tiers(df, feats, scores, cfg))
            results.append({"tiers": {t: cfg["tiers"][t]["min_p"] for t in ("T1", "T2", "T3")}, "metrics": summary(m)})
        current = summary(tier_metrics(df, tiers(df, feats, scores, base)))
        best = pick(results, budget)
        entry = {"current_on_tuning_world": current, "chosen": best, "grid": results}
        if best is not None:
            version = f"policy-0.2-proposal-{scorer}"
            path = ARTIFACTS / f"policy_proposal_{scorer}.yaml"
            path.write_text(proposal_yaml(best["tiers"], version), encoding="utf-8")
            entry["proposal_file"] = str(path)
            if held is not None:
                te, te_feats, te_scores = held
                cfg = copy.deepcopy(base)
                for t, p in best["tiers"].items():
                    cfg["tiers"][t]["min_p"] = p
                entry["held_out"] = {"current": summary(tier_metrics(te, tiers(te, te_feats, te_scores[scorer], base))),
                                     "proposed": summary(tier_metrics(te, tiers(te, te_feats, te_scores[scorer], cfg)))}
        out[scorer] = entry
    return out


def _fmt(m: dict) -> str:
    def pct(x):
        return "  n/a" if x is None else f"{100 * x:5.1f}%"
    return (f"rupees {pct(m['scam_rupees_interrupted'])} | payments {pct(m['scam_payments_interrupted'])} | "
            f"paused {pct(m['scam_payments_paused'])} | 1st payment {pct(m['pre_loss_catch_T2'])} | "
            f"legit/1k {m['legit_interruptions_per_1000']:5.2f} | T3+ precision {pct(m['T3_precision'])}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--table", type=Path, required=True, help="the tuning world (not the training world)")
    p.add_argument("--test", type=Path, default=None, help="a held-out world to check the choice on")
    p.add_argument("--budget", type=float, default=4.0, help="legit interruptions per 1,000, at most")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    res = run(a.table, a.test, a.budget)
    for scorer in (k for k in res if isinstance(res[k], dict) and "grid" in res[k]):
        e = res[scorer]
        print(f"\n{scorer}: current {res['current']['T1']}/{res['current']['T2']}/{res['current']['T3']} (T1/T2/T3 min_p)")
        print(f"  tuning world, current : {_fmt(e['current_on_tuning_world'])}")
        if e["chosen"] is None:
            print(f"  no setting keeps legit interruptions <= {a.budget}/1,000 with T3+ precision >= 25%")
            continue
        t = e["chosen"]["tiers"]
        print(f"  tuning world, proposed {t['T1']}/{t['T2']}/{t['T3']}: {_fmt(e['chosen']['metrics'])}")
        if "held_out" in e:
            print(f"  held-out world, current : {_fmt(e['held_out']['current'])}")
            print(f"  held-out world, proposed: {_fmt(e['held_out']['proposed'])}")
        print(f"  -> {e['proposal_file']}")
    out = a.out or Path(__file__).resolve().parents[1] / "runs" / f"tune_{a.table.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=lambda v: None if isinstance(v, float) and math.isnan(v) else v))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
