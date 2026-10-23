"""One feature builder for every decision. `None` always means unknown, never 0 / false.

Families (corroboration counts families, not sources):
    C context    calls, remote control, sideloaded apps        (phone)
    L lure       the hook: scam-script messages, the task-scam episode (phone)
    P payee      bank features, MuleHunter, registry, FRI, NPCI name/type  (payee side)
    B behaviour  amount vs history, new payee, typed, tranches, coached session (payer side)
    S stage      journey stage (not a family; used for context)
"""
from __future__ import annotations

from features.episode import STATE_CODE, effective_episode
from features.session import session_features
from features.user_baseline import UserBaseline
from api.intel import IntelResult
from schema.events import DecideRequest
from features.tracker import TrackerState

FRI_LEVEL = {"NONE": 0, "MEDIUM": 1, "HIGH": 2, "VERY_HIGH": 3}

FAMILY = {
    "on_call": "C", "caller_unknown": "C", "voip_call": "C", "foreign_caller": "C",
    "outgoing_to_unknown": "C", "remote_control": "C", "untrusted_accessibility": "C",
    "sideloaded_last_24h": "C", "call_on_speaker": "C",
    # The phone's own model (c2-0.8): one score per family, each only in its own family (rule 5).
    "device_c": "C", "device_l": "L", "device_b": "B",
    "lure_max_conf_72h": "L", "lure_tag": "L",
    # The whole episode is one phone summary, so all of it sits in one family (never two).
    "episode_state": "L", "episode_age_days": "L", "episode_bait_credits": "L",
    "episode_new_payees": "L", "episode_out_to_in": "L", "episode_escalation_steps": "L",
    "payee_holder_type": "P", "payee_verified_merchant": "P", "payee_account_age_days": "P",
    "payee_uniq_payers_6h": "P", "payee_uniq_payers_7d": "P", "payee_repeat_ratio": "P",
    "payee_pass_through_24h": "P", "payee_median_hold_min": "P", "payee_mulehunter": "P",
    "payee_registry_listed": "P", "payee_fri_level": "P", "caller_registry_listed": "P",
    "caller_fri_level": "P", "story_mismatch": "P", "vpa_invalid": "P",
    # Payment graph (c2-0.4): crowd signal and money loop; payee family only.
    "payee_crowd": "P", "payee_money_loop": "P", "payee_forwards_to_cashout": "P", "payee_approved_suspect": "P",
    "amount_inr": "B", "amount_to_max_90d": "B", "amount_to_median_90d": "B",
    "first_time_payee": "B", "entry_typed": "B", "attempts_72h": "B", "payee_in_run": "B",
    "payee_in_contacts": "B",
    "new_payee_sum_to_max_90d": "B", "trusted_payee": "B",
    # Coached session (c2-0.5): how the Pay screen was used. Behaviour only, never a second family.
    "session_dictated": "B", "session_pasted_after_switch": "B", "session_app_switches": "B",
    "session_hesitation": "B", "session_abandoned_30m": "B",
    "stage": "S", "minutes_since_first_contact": "S",
}


def build_features(req: DecideRequest, st: TrackerState, intel: IntelResult,
                   base: UserBaseline, trusted: bool, ring: dict | None = None,
                   paid_here: bool = False) -> dict:
    """`paid_here`: RUKO saw this user complete a payment to this payee before (c2-0.6 outcomes)."""
    ctx = req.context
    call = ctx.call if ctx else None
    dev = ctx.device if ctx else None
    f: dict = {}

    # C — context
    f["on_call"] = st.on_call
    f["caller_unknown"] = None if call is None or call.caller_in_contacts is None else not call.caller_in_contacts
    f["voip_call"] = None if call is None or call.kind is None else call.kind == "voip"
    num = call.caller_number if call else None
    f["foreign_caller"] = None if not num else not num.startswith("+91")
    f["outgoing_to_unknown"] = None if call is None or call.direction is None else (
        call.direction == "outgoing" and call.caller_in_contacts is False)
    # Speaker on only matters during a call; it strengthens the call signal, it is not a new family.
    f["call_on_speaker"] = call.speaker_on if call is not None and st.on_call else None
    # The phone's verdict on what only it can see (c2-0.8); null when its model did not run.
    dv = ctx.device_verdict if ctx else None
    f["device_c"] = dv.c if dv else None
    f["device_l"] = dv.l if dv else None
    f["device_b"] = dv.b if dv else None
    if dev is None:
        f["remote_control"] = f["untrusted_accessibility"] = f["sideloaded_last_24h"] = None
    else:
        recent = dev.remote_app_installed_min_ago
        f["remote_control"] = (None if dev.remote_access_active is None and recent is None
                               else bool(dev.remote_access_active) or (recent is not None and recent <= 120))
        f["untrusted_accessibility"] = dev.untrusted_accessibility
        f["sideloaded_last_24h"] = dev.sideloaded_last_24h

    # L — lure
    lures = [l for l in (ctx.lures if ctx else []) if l.tag != "normal" and l.min_ago <= 72 * 60]
    top = max(lures, key=lambda l: l.conf, default=None)
    f["lure_max_conf_72h"] = None if ctx is None else (top.conf if top else 0.0)
    f["lure_tag"] = top.tag if top else None
    # Task-scam episode from the phone's watcher (c2-0.3), or the old money summary mapped onto it.
    ep = effective_episode(ctx)
    f["episode_state"] = STATE_CODE[ep["state"]] if ep else None
    f["episode_age_days"] = ep["age_days"] if ep else None
    f["episode_bait_credits"] = ep["bait_credits"] if ep else None
    f["episode_new_payees"] = ep["new_payees"] if ep else None
    f["episode_out_to_in"] = ep["out_to_in_ratio"] if ep else None
    steps = ep["escalation_steps"] if ep else None
    if steps is not None and ep["last_out_inr"]:
        steps = steps + 1 if req.amount_inr >= 1.5 * ep["last_out_inr"] else 0  # does this payment continue it?
    f["episode_escalation_steps"] = steps

    # P — payee (what NPCI, the payee's bank and the lists said)
    npci, bank = intel.npci or {}, (intel.bank or {}).get("features") or {}
    f["vpa_invalid"] = intel.vpa_invalid
    f["payee_holder_type"] = npci.get("holder_type")
    f["payee_verified_merchant"] = npci.get("verified_merchant")
    f["payee_account_age_days"] = bank.get("account_age_days")
    f["payee_uniq_payers_6h"] = bank.get("uniq_payers_6h")
    f["payee_uniq_payers_7d"] = bank.get("uniq_payers_7d")
    f["payee_repeat_ratio"] = bank.get("repeat_payer_ratio_30d")
    f["payee_pass_through_24h"] = bank.get("pass_through_24h")
    f["payee_median_hold_min"] = bank.get("median_hold_min")
    f["payee_mulehunter"] = ((intel.bank or {}).get("mulehunter") or {}).get("score")
    f["payee_registry_listed"] = (intel.payee_registry or {}).get("listed")
    f["payee_fri_level"] = FRI_LEVEL.get((intel.payee_fri or {}).get("risk"))
    f["caller_registry_listed"] = (intel.caller_registry or {}).get("listed")
    f["caller_fri_level"] = FRI_LEVEL.get((intel.caller_fri or {}).get("risk"))
    # "Government" or "company" should never be a personal account.
    f["story_mismatch"] = (None if req.payer_says is None or f["payee_holder_type"] is None
                           else req.payer_says in ("government", "company") and f["payee_holder_type"] == "person")

    # P — payment graph: many RUKO users got "rewards" from the same account or paid this payee in
    # their episodes, or the payee forwards to where the "rewards" came from. None = unknown.
    f["payee_crowd"] = ring["crowd"] if ring else None
    f["payee_money_loop"] = ring["money_loop"] if ring else None
    f["payee_forwards_to_cashout"] = ring["forwards_to_known_cashout"] if ring else None
    f["payee_approved_suspect"] = ring.get("approved_suspect") if ring else None

    # B — behaviour (the payer's own history)
    f["amount_inr"] = req.amount_inr
    f["amount_to_max_90d"] = round(req.amount_inr / base.max_90d, 3) if base.max_90d else None
    f["amount_to_median_90d"] = round(req.amount_inr / base.median_90d, 3) if base.median_90d else None
    f["first_time_payee"] = req.payee_upi_id.lower() not in base.known_payees and not paid_here
    # c2-0.7: a saved contact on the phone. Softens "new payee" only; never makes a payment safe.
    f["payee_in_contacts"] = ctx.payee_in_contacts if ctx else None
    f["entry_typed"] = req.entry_method == "typed"
    f["attempts_72h"] = st.attempts_72h
    f["payee_in_run"] = st.payee_in_run
    f["new_payee_sum_to_max_90d"] = round(st.new_payee_sum_72h / base.max_90d, 3) if base.max_90d else None
    f["trusted_payee"] = trusted
    f.update(session_features(ctx.session if ctx else None))

    # S — stage
    f["stage"] = int(st.stage[1])
    f["minutes_since_first_contact"] = st.minutes_since_first_contact
    return f
