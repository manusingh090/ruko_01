"""The trained server scorer, a drop-in for api/rule_scorer.score (the engine's app.state.score).

    from model.predict import score
    score(features) -> {p_scam, family_scores, reasons, playbook, model_version}

`features` is exactly what backend/features/build.py build_features() returns; nothing is
re-implemented here. `None` stays missing (LightGBM's own missing-value branch), never 0 or false.
The playbook still comes from the rules engine's matcher (config-like, analyst-approved playbooks)
until discovery adds new ones. No LLM anywhere (CLAUDE.md rule 13).
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np

from api.rule_scorer import _playbook
from model.data import encode_features

MODEL_PATH = Path(os.environ.get("RUKO_MODEL", Path(__file__).resolve().parent / "artifacts" / "v0" / "server_model.json"))
FAMILIES = ("C", "L", "P", "B")
CLIP = 1e-4
LIT = 0.5            # policy.yaml family_threshold
MAX_REASONS = 4      # same as the rules engine


class _Family:
    def __init__(self, spec: dict):
        self.features = spec["features"]
        self.booster = lgb.Booster(model_str=spec["booster"])
        self.iso_x = np.asarray(spec["iso_x"])
        self.iso_y = np.asarray(spec["iso_y"])

    def calibrated(self, row: np.ndarray) -> float:
        raw = float(self.booster.predict(row)[0])
        return float(np.interp(raw, self.iso_x, self.iso_y))


class Model:
    def __init__(self, spec: dict):
        self.version = spec["version"]
        self.prior = spec["prior"]
        self.k = spec["family_k"]
        self.families = {f: _Family(spec["families"][f]) for f in FAMILIES}
        self.coef = np.asarray(spec["combiner"]["coef"])
        self.intercept = spec["combiner"]["intercept"]
        self.reasons = spec["reasons"]

    def strength(self, p: float) -> float:
        p = min(max(p, CLIP), 1 - CLIP)
        lr = (p / (1 - p)) / (self.prior / (1 - self.prior))
        return lr / (lr + self.k)

    def score(self, features: dict) -> dict:
        x = encode_features(features)
        rows, cal = {}, {}
        for f, fam in self.families.items():
            rows[f] = np.array([[x.get(c, math.nan) for c in fam.features]], dtype=float)
            cal[f] = fam.calibrated(rows[f])
        stage = features.get("stage") or 0
        z = self.intercept + sum(self.coef[i] * _logit(cal[f]) for i, f in enumerate(FAMILIES)) + self.coef[4] * stage
        p_scam = 1 / (1 + math.exp(-z))
        family_scores = {f: round(self.strength(cal[f]), 3) for f in FAMILIES}
        return {"p_scam": round(p_scam, 4), "family_scores": family_scores,
                "reasons": self._reasons(rows, family_scores), "playbook": _playbook(features),
                "model_version": self.version}

    def score_frame(self, x) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Many rows at once (model.data.encode_frame columns): p_scam and family scores, no reasons."""
        cal = {f: np.interp(fam.booster.predict(x.reindex(columns=fam.features)), fam.iso_x, fam.iso_y)
               for f, fam in self.families.items()}
        stage = np.nan_to_num(x["stage"].to_numpy(dtype=float), nan=0.0)
        z = self.intercept + sum(self.coef[i] * _logit_arr(cal[f]) for i, f in enumerate(FAMILIES)) + self.coef[4] * stage
        lr_prior = self.prior / (1 - self.prior)
        fam = {}
        for f in FAMILIES:
            p = np.clip(cal[f], CLIP, 1 - CLIP)
            lr = (p / (1 - p)) / lr_prior
            fam[f] = lr / (lr + self.k)
        return 1 / (1 + np.exp(-z)), fam

    def _reasons(self, rows: dict, family_scores: dict) -> list[str]:
        """Codes of the features that pushed lit families up the most, taken in turns: the best reason
        of each lit family (strongest family first), then each family's second best, and so on, so
        one family's details never crowd out another family's evidence (e.g. a WhatsApp call's R1
        and the payment's R6 must not hide R2 remote control)."""
        ranked: list[list[tuple[str, float]]] = []
        for f in sorted(family_scores, key=lambda k: -family_scores[k]):
            if family_scores[f] < LIT:
                continue
            fam = self.families[f]
            contrib = fam.booster.predict(rows[f], pred_contrib=True)[0][:-1]  # last value is the bias
            weight: dict[str, float] = {}
            for name, c in zip(fam.features, contrib):
                code = self.reasons.get(name)
                if code and c > 0:
                    weight[code] = weight.get(code, 0.0) + float(c)
            ranked.append(sorted(weight.items(), key=lambda kv: -kv[1]))
        out: list[str] = []
        for depth in range(max((len(codes) for codes in ranked), default=0)):
            for codes in ranked:
                if depth < len(codes) and codes[depth][0] not in out and len(out) < MAX_REASONS:
                    out.append(codes[depth][0])
        return out


def _logit(p: float) -> float:
    p = min(max(p, CLIP), 1 - CLIP)
    return math.log(p / (1 - p))


def _logit_arr(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, CLIP, 1 - CLIP)
    return np.log(p / (1 - p))


@lru_cache(maxsize=1)
def load(path: str | Path = MODEL_PATH) -> Model:
    return Model(json.loads(Path(path).read_text()))


MODEL_VERSION = "lgbm-0.2"


def score(features: dict) -> dict:
    return load().score(features)
