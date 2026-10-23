"""The phone's verdict, contract c2-0.8 `context.device_verdict`: the only thing the phone sends
about what it saw. DeviceVerdict.kt is a line-by-line port; device_feature_vectors.json checks both.

- c / l / b: the device model's family scores, rounded to 2 decimals; null when every input of that
  family was unmeasured (a model's guess about nothing must not look like a measurement; rule 1).
- level: high when the local warning fires, medium when any family is lit, low otherwise.
- fraud_class: the known scam the phone's own signals point to, with the engine's playbook names
  (the same order of checks as api/rule_scorer._playbook, on what the phone can see).
- reasons: the device model's reasons, at most 3.
"""
from __future__ import annotations

import math

from model.device import score as device_score

FAMILY_KEY = {"C": "c", "L": "l", "B": "b"}
LURE_SENT = 0.6            # LURE_SEND_THRESHOLD: a lure the phone would send
RECENT_INSTALL_MIN = 120   # a remote app installed in the last 2 hours counts as control (features.build)


def round2(x: float) -> float:
    """Half up, the same in Kotlin (floor(x * 100 + 0.5) / 100)."""
    return math.floor(x * 100 + 0.5) / 100


def fraud_class(f: dict) -> str | None:
    def on(k: str) -> bool:
        return f.get(k) is not None and f[k] >= 0.5

    def lure(k: str) -> bool:
        return (f.get(k) or 0.0) >= LURE_SENT

    call_now = on("dev_call_active") or f.get("dev_call_ended_min_ago") is not None
    unknown = on("dev_call_unknown") or (on("dev_call_voip") and f.get("dev_call_unknown") is None)
    remote = on("dev_screen_recorded") or (f.get("dev_remote_min_ago") is not None
                                           and f["dev_remote_min_ago"] <= RECENT_INSTALL_MIN)
    outgoing = f.get("dev_call_incoming") is not None and f["dev_call_incoming"] < 0.5
    if (f.get("dev_ep_state") or 0.0) == 2.0:
        return "task_job"
    if on("dev_sideloaded_24h") and (lure("dev_lure_kyc") or on("dev_accessibility")):
        return "fake_kyc_apk"
    if call_now and outgoing and remote:
        return "fake_customer_care"
    if lure("dev_lure_arrest") or (call_now and unknown and remote):
        return "digital_arrest"
    if lure("dev_lure_trading"):
        return "trading_group"
    if lure("dev_lure_task"):
        return "task_job"
    if call_now and unknown and on("dev_entry_qr"):
        return "refund_qr"
    if lure("dev_lure_refund"):
        return "refund_qr"
    if lure("dev_lure_kyc"):
        return "fake_kyc_apk"
    return None


def verdict(model: dict, features: dict) -> dict:
    r = device_score.score(model, features)
    fam = {}
    for f in model["family_order"]:
        measured = any(features.get(n) is not None for n in model["families"][f]["features"])
        fam[FAMILY_KEY[f]] = round2(r["family_scores"][f]) if measured else None
    level = "high" if r["warn"] else "medium" if r["lit"] else "low"
    return {"model_version": model["version"], "fraud_class": fraud_class(features), "level": level, **fam,
            "reasons": r["reasons"]}
