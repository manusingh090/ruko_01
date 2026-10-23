"""Rule-based scorer with the same output shape a trained model (LightGBM + calibration) will have.

score(features) -> {p_scam, family_scores, reasons, playbook, model_version}

Each signal has a strength in [0, 1] and a reason code. Signals inside a family combine as
"noisy-or" (1 - Π(1 - s)); families combine through a logistic. Weights are hand-set and
deliberately simple -- the point is a transparent fallback, not a tuned model.
"""
from __future__ import annotations

import math

from features.session import T as SESSION

MODEL_VERSION = "rules-0.7"
# rules-0.6: a first-time payee who is a saved contact on the phone is a weaker "new payee" sign.
# rules-0.5: R7 (tranche) counts only when this payment goes to a payee in the run of new payees.
# L rose from 2.0 in rules-0.3: it now carries the phone's 30-day task-scam episode, which is as
# strong as live call context. One family alone still stays far below any tier (p < 0.15).
FAMILY_WEIGHT = {"C": 3.2, "L": 3.5, "P": 3.2, "B": 2.4}
BIAS = -5.0  # two strong families (~0.9 each) land near p=0.55; one alone stays under 0.1
REASON_MIN_STRENGTH = 0.3


def _unknown_caller(f: dict) -> bool:
    """A caller not in contacts, or a VoIP call whose number the phone cannot see (null is not safe)."""
    return bool(f["caller_unknown"]) or (bool(f["voip_call"]) and f["caller_unknown"] is None)


def _signals(f: dict) -> dict[str, list[tuple[float, str]]]:
    s: dict[str, list[tuple[float, str]]] = {"C": [], "L": [], "P": [], "B": []}
    add = lambda fam, strength, code: s[fam].append((strength, code))  # noqa: E731

    # Context: who is on the line, and who can see the screen.
    if f["on_call"] and _unknown_caller(f):
        strength = 0.9 if f["foreign_caller"] else 0.8 if f["voip_call"] else 0.7
        # On speaker while paying: someone is talking the user through it (same call, same family).
        if f["call_on_speaker"]:
            strength = min(0.95, strength + 0.1)
        add("C", strength, "R1_live_unknown_call")
    elif f["on_call"] and f["outgoing_to_unknown"]:
        add("C", 0.5, "R1_live_unknown_call")
    if f["remote_control"]:
        add("C", 0.85, "R2_remote_control")
    if f["untrusted_accessibility"]:
        add("C", 0.7, "R2_remote_control")
    if f["sideloaded_last_24h"]:
        add("C", 0.5, "R2_remote_control")

    # Lure: a scam-script message recently, or small "reward" credits from strangers.
    if (f["lure_max_conf_72h"] or 0) >= 0.6:
        add("L", f["lure_max_conf_72h"], "R5_lure_message")
    # The phone's task-scam episode is ONE lure signal: rewards in, then paying more and more.
    ep = f["episode_state"]
    if ep == 2:  # active
        add("L", 0.85, "R10_bait_then_pay")
    elif ep == 1 and f["first_time_payee"]:  # watching
        add("L", 0.5, "R10_bait_then_pay")
    elif ep == 3 and (f["payee_registry_listed"] or (f["payee_fri_level"] or 0) >= 2):
        add("L", 0.6, "R10_bait_then_pay")  # "I know them" is overridden by an intel hit

    # Payee: mule-like behaviour seen by the payee's bank, and what the lists say.
    u6, u7, rep = f["payee_uniq_payers_6h"], f["payee_uniq_payers_7d"], f["payee_repeat_ratio"]
    pt, age, hold = f["payee_pass_through_24h"], f["payee_account_age_days"], f["payee_median_hold_min"]
    if u6 is not None and rep is not None and u6 >= 10 and rep < 0.3:
        add("P", 0.8, "R3_mule_like_payee")
    if u7 is not None and rep is not None and u7 >= 20 and rep < 0.2:
        add("P", 0.6, "R3_mule_like_payee")
    if pt is not None and age is not None and pt >= 0.8 and age < 30:
        add("P", 0.75, "R3_mule_like_payee")
    if hold is not None and pt is not None and hold <= 30 and pt >= 0.6:
        add("P", 0.5, "R3_mule_like_payee")
    if age is not None and age < 14 and f["payee_holder_type"] == "person":
        add("P", 0.3, "R3_mule_like_payee")
    if (f["payee_mulehunter"] or 0) >= 0.5:
        add("P", f["payee_mulehunter"], "R3_mule_like_payee")
    if f["payee_registry_listed"]:
        add("P", 0.8, "R8_intel_hit")
    if f["caller_registry_listed"]:
        add("P", 0.7, "R8_intel_hit")
    for lvl in (f["payee_fri_level"], f["caller_fri_level"]):
        if lvl:
            add("P", {1: 0.4, 2: 0.7, 3: 0.9}[lvl], "R8_intel_hit")
    if f["story_mismatch"]:
        add("P", 0.8, "R9_story_mismatch")
    # Payment graph (c2-0.4): the account is linked to the ring that sent the "rewards".
    if f["payee_money_loop"]:
        add("P", 0.8, "R11_ring_link")
    if f["payee_crowd"]:
        add("P", 0.6, "R11_ring_link")
    if f["payee_forwards_to_cashout"]:
        add("P", 0.6, "R11_ring_link")
    if f["payee_approved_suspect"]:  # found by graph discovery, approved by two analysts
        add("P", 0.7, "R11_ring_link")

    # Behaviour: is this payment unlike the payer's usual ones?
    ratio = f["amount_to_max_90d"]
    if ratio is not None and ratio >= 3:
        add("B", 0.8, "R4_unusual_amount")
    elif ratio is not None and ratio >= 1.5:
        add("B", 0.5, "R4_unusual_amount")
    elif ratio is None and f["amount_inr"] >= 50_000:
        add("B", 0.4, "R4_unusual_amount")
    if f["first_time_payee"]:
        # A saved contact never paid is still new (scammers ask victims to save their number), but
        # weaker than a stranger's ID typed by hand. Missing or null is unknown: no softening.
        weak = f.get("payee_in_contacts") is True or not f["entry_typed"]
        add("B", 0.15 if weak else 0.35, "R6_new_payee_typed")
    # A tranche run is money split across new payees. A payment to an established payee outside the
    # run (the usual kirana after a few new payees) is not part of it.
    # Missing (rows built before rules-0.5) is unknown, and unknown is never safe: counts as in the run.
    run = f["attempts_72h"] if f.get("payee_in_run") is not False else 0
    if run >= 2:
        add("B", 0.7, "R7_tranche_pattern")
    elif run >= 1:
        add("B", 0.4, "R7_tranche_pattern")
    if (f["new_payee_sum_to_max_90d"] or 0) >= 3 and run >= 1:
        add("B", 0.6, "R7_tranche_pattern")
    # Coached session (c2-0.5): the Pay screen was used the way a guided victim uses it.
    if f["session_dictated"]:
        add("B", 0.6, "R12_coached_session")
    if f["session_pasted_after_switch"]:
        add("B", 0.55, "R12_coached_session")
    if (f["session_app_switches"] or 0) >= SESSION["many_switches"]:
        add("B", 0.45, "R12_coached_session")
    if (f["session_abandoned_30m"] or 0) >= 1:
        add("B", 0.4, "R12_coached_session")
    if f["session_hesitation"]:
        add("B", 0.3, "R12_coached_session")
    return s


def _noisy_or(xs: list[float]) -> float:
    return 1 - math.prod(1 - x for x in xs)


def _playbook(f: dict) -> str | None:
    tag = f["lure_tag"]
    # An active task-scam episode beats any older message on the phone.
    if f["episode_state"] == 2:
        return "task_job"
    if f["sideloaded_last_24h"] and tag == "kyc":
        return "fake_kyc_apk"
    if f["outgoing_to_unknown"] and (f["remote_control"] or f["session_dictated"]):
        return "fake_customer_care"
    if tag == "arrest_threat" or (f["on_call"] and _unknown_caller(f)
                                  and (f["remote_control"] or f["story_mismatch"] or f["session_dictated"])):
        return "digital_arrest"
    if tag == "trading_tip":
        return "trading_group"
    if tag == "task_job":
        return "task_job"
    if f["on_call"] and _unknown_caller(f) and not f["entry_typed"]:
        return "refund_qr"
    return None


def score(f: dict) -> dict:
    sig = _signals(f)
    fam = {k: round(_noisy_or([x for x, _ in v]), 3) for k, v in sig.items()}
    # A verified, long-standing merchant with no list hits is very unlikely to be a mule.
    if f["payee_verified_merchant"] and not any(c == "R8_intel_hit" for _, c in sig["P"]):
        fam["P"] = round(fam["P"] * 0.3, 3)
    z = BIAS + sum(FAMILY_WEIGHT[k] * v for k, v in fam.items())
    best: dict[str, float] = {}
    for k, v in sig.items():
        for strength, code in v:
            if strength >= REASON_MIN_STRENGTH:
                best[code] = max(best.get(code, 0), strength * FAMILY_WEIGHT[k])
    reasons = [c for c, _ in sorted(best.items(), key=lambda kv: -kv[1])][:4]
    return {"p_scam": round(1 / (1 + math.exp(-z)), 4), "family_scores": fam,
            "reasons": reasons, "playbook": _playbook(f), "model_version": MODEL_VERSION}
