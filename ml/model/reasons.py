"""Which reason code each server feature speaks for. Reasons come only from lit families, ranked by
how much each feature pushed this payment's family score up (LightGBM per-feature contributions).

Every model feature except `trusted_payee` (a policy rule, never a reason) must be listed here;
tests/test_predict.py checks that, and that every code is a contract ReasonCode.
"""
from __future__ import annotations

REASON = {
    # C: who is on the line, and who can see the screen
    "on_call": "R1_live_unknown_call", "caller_unknown": "R1_live_unknown_call", "voip_call": "R1_live_unknown_call",
    "foreign_caller": "R1_live_unknown_call", "outgoing_to_unknown": "R1_live_unknown_call",
    "call_on_speaker": "R1_live_unknown_call",
    "remote_control": "R2_remote_control", "untrusted_accessibility": "R2_remote_control",
    "sideloaded_last_24h": "R2_remote_control",
    # L: the hook (a scam-script SMS) and the task-scam episode
    "lure_max_conf_72h": "R5_lure_message", "lure_is_arrest_threat": "R5_lure_message", "lure_is_kyc": "R5_lure_message",
    "lure_is_task_job": "R5_lure_message", "lure_is_trading_tip": "R5_lure_message", "lure_is_refund": "R5_lure_message",
    "episode_state": "R10_bait_then_pay", "episode_age_days": "R10_bait_then_pay",
    "episode_bait_credits": "R10_bait_then_pay", "episode_new_payees": "R10_bait_then_pay",
    "episode_out_to_in": "R10_bait_then_pay", "episode_escalation_steps": "R10_bait_then_pay",
    # P: the payee, as its bank, the lists and the payment graph see it
    "payee_is_person": "R3_mule_like_payee", "payee_verified_merchant": "R3_mule_like_payee",
    "payee_account_age_days": "R3_mule_like_payee", "payee_uniq_payers_6h": "R3_mule_like_payee",
    "payee_uniq_payers_7d": "R3_mule_like_payee", "payee_repeat_ratio": "R3_mule_like_payee",
    "payee_pass_through_24h": "R3_mule_like_payee", "payee_median_hold_min": "R3_mule_like_payee",
    "payee_mulehunter": "R3_mule_like_payee", "vpa_invalid": "R3_mule_like_payee",
    "payee_registry_listed": "R8_intel_hit", "payee_fri_level": "R8_intel_hit",
    "caller_registry_listed": "R8_intel_hit", "caller_fri_level": "R8_intel_hit",
    "story_mismatch": "R9_story_mismatch",
    "payee_crowd": "R11_ring_link", "payee_money_loop": "R11_ring_link",
    "payee_forwards_to_cashout": "R11_ring_link", "payee_approved_suspect": "R11_ring_link",
    # B: this payment against the payer's own habits, and the Pay screen
    "amount_inr": "R4_unusual_amount", "amount_to_max_90d": "R4_unusual_amount",
    "amount_to_median_90d": "R4_unusual_amount",
    "first_time_payee": "R6_new_payee_typed", "entry_typed": "R6_new_payee_typed",
    "payee_in_contacts": "R6_new_payee_typed",
    "attempts_72h": "R7_tranche_pattern", "new_payee_sum_to_max_90d": "R7_tranche_pattern",
    "payee_in_run": "R7_tranche_pattern",
    "session_dictated": "R12_coached_session", "session_pasted_after_switch": "R12_coached_session",
    "session_app_switches": "R12_coached_session", "session_hesitation": "R12_coached_session",
    "session_abandoned_30m": "R12_coached_session",
}
# trusted_payee is a policy rule; device_* are the phone's own family scores (c2-0.8), whose reasons
# the phone already chose: they add weight to their family but never name a reason here.
NOT_A_REASON = {"trusted_payee", "device_c", "device_l", "device_b"}

# The on-device model's features (dataset/device_features.py), for the phone's own reasons.
DEVICE_REASON = {
    **{f: "R1_live_unknown_call" for f in ("dev_call_active", "dev_call_voip", "dev_call_unknown", "dev_call_incoming",
                                           "dev_call_minutes", "dev_call_ended_min_ago", "dev_call_foreign")},
    **{f: "R2_remote_control" for f in ("dev_remote_installed", "dev_remote_min_ago", "dev_screen_recorded",
                                        "dev_accessibility", "dev_sideloaded_24h")},
    **{f: "R5_lure_message" for f in ("dev_lure_max", "dev_lure_arrest", "dev_lure_kyc", "dev_lure_task",
                                      "dev_lure_trading", "dev_lure_refund", "dev_lure_count_72h", "dev_lure_min_ago")},
    **{f: "R10_bait_then_pay" for f in ("dev_ep_state", "dev_ep_age_days", "dev_ep_bait_credits", "dev_ep_bait_inr",
                                        "dev_ep_outs", "dev_ep_new_payees", "dev_ep_escalation", "dev_ep_out_to_in",
                                        "dev_ep_payee_seen")},
    **{f: "R4_unusual_amount" for f in ("dev_amount_log10", "dev_amount_to_p95", "dev_amount_to_max",
                                        "dev_amount_to_median", "dev_hour_share")},
    **{f: "R6_new_payee_typed" for f in ("dev_entry_typed", "dev_entry_qr", "dev_first_time_payee", "dev_payee_in_contacts")},
    **{f: "R7_tranche_pattern" for f in ("dev_out_1h", "dev_out_24h")},
    **{f: "R12_coached_session" for f in ("dev_sess_digits", "dev_sess_median_gap_s", "dev_sess_max_gap_s",
                                          "dev_sess_gap_vs_usual", "dev_sess_edits", "dev_sess_pasted",
                                          "dev_sess_paste_after_return_s", "dev_sess_app_switches", "dev_sess_away_s",
                                          "dev_sess_pay_screen_s", "dev_sess_abandoned_30m")},
}
DEVICE_NOT_A_REASON = {"dev_hist_n"}  # how much history the phone has: context, not evidence
