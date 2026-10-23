"""The on-device model's JSON must score exactly like the LightGBM models it came from, and the
shipped vectors (the Android test's expected answers) must match the shipped model."""
import json
import math
import typing

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from dataset.device_features import FAMILIES
from model.device import score as json_scorer
from model.device.train import ARTIFACTS, flatten
from model.reasons import DEVICE_NOT_A_REASON, DEVICE_REASON
from schema.events import ReasonCode


def one_family_model(booster: lgb.Booster, names: list[str]) -> dict:
    trees = [flatten(t["tree_structure"]) for t in booster.dump_model()["tree_info"]]
    return {"family_order": ["C"], "families": {"C": {"features": names, "trees": trees, "iso_x": [0.0, 1.0],
                                                      "iso_y": [0.0, 1.0]}},
            "prior": 0.5, "family_k": 1.0, "lit": 0.5, "combiner": {"coef": [1.0], "intercept": 0.0},
            "reasons": {}, "max_reasons": 3, "local_warning": {"min_families": 2, "min_p": 0.99}}


@pytest.mark.parametrize("zero_as_missing", [False, True])
def test_export_matches_lightgbm_including_missing_values(zero_as_missing):
    """Covers all three LightGBM missing types: NaN seen in training (a), never seen (b: NaN counts
    as 0 at prediction), and zero-as-missing (c, when zero_as_missing is on)."""
    rng = np.random.default_rng(0)
    n = 3000
    x = pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n), "c": rng.choice([0.0, 1.0, 2.0, 3.0], size=n),
                      "d": rng.integers(0, 2, size=n).astype(float)})
    y = (x.a.fillna(0) + 0.5 * x.b + 0.3 * x.c - x.d + rng.normal(scale=0.5, size=n) > 0.3).astype(int)
    x.loc[rng.random(n) < 0.2, "a"] = np.nan
    booster = lgb.train(dict(objective="binary", num_leaves=8, min_data_in_leaf=20, zero_as_missing=zero_as_missing,
                             verbose=-1, seed=1), lgb.Dataset(x, y), num_boost_round=30)
    test = x.sample(400, random_state=1).copy()
    test.loc[test.index[:60], "b"] = np.nan   # never missing in training
    test.loc[test.index[60:120], "a"] = 0.0
    want = booster.predict(test, raw_score=True)
    model = json.loads(json.dumps(one_family_model(booster, list(x.columns))))
    rows = [{k: (None if math.isnan(v) else float(v)) for k, v in r.items()} for r in test.to_dict("records")]
    got = np.array([json_scorer.score(model, r)["raw"]["C"] for r in rows])
    assert np.abs(got - want).max() < 1e-12


def test_device_reasons_cover_every_feature():
    feats = {f for fam in FAMILIES.values() for f in fam} - DEVICE_NOT_A_REASON
    assert feats <= set(DEVICE_REASON), feats - set(DEVICE_REASON)
    assert set(DEVICE_REASON.values()) <= set(typing.get_args(ReasonCode))


@pytest.mark.skipif(not (ARTIFACTS / "device_model.json").exists(), reason="run python -m model.device.train first")
def test_shipped_vectors_match_the_shipped_model():
    model = json_scorer.load(ARTIFACTS / "device_model.json")
    cases = json.loads((ARTIFACTS / "device_vectors.json").read_text(encoding="utf-8"))
    assert cases["version"] == model["version"] and cases["vectors"]
    for c in cases["vectors"]:
        got, want = json_scorer.score(model, c["features"]), c["expected"]
        assert abs(got["p"] - want["p"]) <= cases["tolerance"], c["case"]
        assert (got["lit"], got["reasons"], got["warn"]) == (want["lit"], want["reasons"], want["warn"]), c["case"]
        assert set(got["reasons"]) <= set(typing.get_args(ReasonCode))
    assert any(c["expected"]["warn"] for c in cases["vectors"]) and not all(c["expected"]["warn"] for c in cases["vectors"])
