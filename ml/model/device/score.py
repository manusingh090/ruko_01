"""The on-device model scored from device_model.json alone, step for step like DeviceModel.kt.

This is the reference for the Kotlin port (ml/device_kotlin/DeviceModel.kt): same steps, same order,
plain arithmetic, no LightGBM. tests/test_device_model.py checks it against the LightGBM models it
was exported from, and device_vectors.json holds its answers for the Kotlin test.

One payment's features: name -> number, or None when the phone could not measure it.
1. Each family (C, L, B) walks its trees; its raw score is the sum of the leaves reached. A split
   sends the value left when value <= threshold. A missing value follows LightGBM's own rule: with
   `missing` 2 (NaN) it takes the split's default side; otherwise it counts as 0.0, and with
   `missing` 1 (Zero) a zero takes the default side.
2. p = sigmoid(raw), then the isotonic map (iso_x -> iso_y, linear in between, clamped at the ends).
3. Family score = LR / (LR + K), LR = odds(calibrated) / odds(prior). Lit when >= `lit`.
4. p_device = sigmoid(intercept + sum of coef_f * logit(calibrated_f)).
5. Reasons: in each lit family, every split on the path credits its feature with the change in node
   value (child - node); credits add up per reason code. The best code of each lit family comes
   first (strongest family first), then the strongest remaining codes, up to `max_reasons`.
6. warn = at least `local_warning.min_families` lit and p_device >= `local_warning.min_p`.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ZERO = 1e-35  # LightGBM kZeroThreshold
CLIP = 1e-4


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _logit(p: float) -> float:
    p = min(max(p, CLIP), 1 - CLIP)
    return math.log(p / (1 - p))


def _go_left(tree: dict, node: int, value: float | None) -> bool:
    missing = tree["missing"][node]
    v = math.nan if value is None else value
    if math.isnan(v) and missing != 2:
        v = 0.0
    if (missing == 1 and abs(v) <= ZERO) or (missing == 2 and math.isnan(v)):
        return tree["default_left"][node]
    return v <= tree["threshold"][node]


def _walk(tree: dict, row: list[float | None]) -> tuple[float, list[tuple[int, float]]]:
    """The leaf value reached, and (feature index, change in node value) for every split passed."""
    if not tree["split_feature"]:
        return tree["leaf_value"][0], []
    node, credits = 0, []
    while True:
        f = tree["split_feature"][node]
        child = tree["left"][node] if _go_left(tree, node, row[f]) else tree["right"][node]
        child_value = tree["node_value"][child] if child >= 0 else tree["leaf_value"][~child]
        credits.append((f, child_value - tree["node_value"][node]))
        if child < 0:
            return child_value, credits
        node = child


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:  # xs[lo] <= x < xs[hi]
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    return ys[lo] + (x - xs[lo]) * (ys[hi] - ys[lo]) / (xs[hi] - xs[lo])


def score(model: dict, features: dict) -> dict:
    fams = model["family_order"]
    prior_odds = model["prior"] / (1 - model["prior"])
    raw, cal, strength, credits = {}, {}, {}, {}
    for f in fams:
        spec = model["families"][f]
        row = [features.get(name) for name in spec["features"]]
        total, per_feature = 0.0, [0.0] * len(spec["features"])
        for tree in spec["trees"]:
            leaf, path = _walk(tree, row)
            total += leaf
            for i, c in path:
                per_feature[i] += c
        raw[f] = total
        cal[f] = _interp(_sigmoid(total), spec["iso_x"], spec["iso_y"])
        p = min(max(cal[f], CLIP), 1 - CLIP)
        lr = (p / (1 - p)) / prior_odds
        strength[f] = lr / (lr + model["family_k"])
        credits[f] = per_feature
    c = model["combiner"]
    p_device = _sigmoid(c["intercept"] + sum(c["coef"][i] * _logit(cal[f]) for i, f in enumerate(fams)))
    lit = [f for f in sorted(fams, key=lambda k: -strength[k]) if strength[f] >= model["lit"]]
    reasons = _reasons(model, lit, credits)
    w = model["local_warning"]
    return {"raw": raw, "calibrated": cal, "family_scores": strength, "p": p_device, "lit": lit,
            "reasons": reasons, "warn": len(lit) >= w["min_families"] and p_device >= w["min_p"]}


def _reasons(model: dict, lit: list[str], credits: dict) -> list[str]:
    ranked = []
    for f in lit:  # strongest family first
        names = model["families"][f]["features"]
        per_code: dict[str, float] = {}
        for name, c in zip(names, credits[f]):
            code = model["reasons"].get(name)
            if code and c > 0:
                per_code[code] = per_code.get(code, 0.0) + c
        ranked.append(sorted(per_code.items(), key=lambda kv: (-kv[1], kv[0])))
    out: list[str] = []
    for codes in ranked:
        if codes and codes[0][0] not in out and len(out) < model["max_reasons"]:
            out.append(codes[0][0])
    for code, _ in sorted((cw for codes in ranked for cw in codes[1:]), key=lambda kv: (-kv[1], kv[0])):
        if code not in out and len(out) < model["max_reasons"]:
            out.append(code)
    return out
