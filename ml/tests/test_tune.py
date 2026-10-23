"""The threshold tuner must only ever propose tier numbers, and never pick a setting outside budget."""
import yaml

from model.tune import POLICY_YAML, pick, proposal_yaml


def test_proposal_changes_only_tier_numbers_and_version():
    original = POLICY_YAML.read_text(encoding="utf-8")
    text = proposal_yaml({"T1": 0.25, "T2": 0.55, "T3": 0.8}, "policy-test")
    assert len(text.splitlines()) == len(original.splitlines())  # comments and layout kept
    old, new = yaml.safe_load(original), yaml.safe_load(text)
    assert new["version"] == "policy-test"
    assert {t: new["tiers"][t]["min_p"] for t in ("T1", "T2", "T3")} == {"T1": 0.25, "T2": 0.55, "T3": 0.8}
    assert new["tiers"]["T4"] == old["tiers"]["T4"]
    for t in ("T1", "T2", "T3"):
        new["tiers"][t]["min_p"] = old["tiers"][t]["min_p"]
    new["version"] = old["version"]
    assert new == old


def result(rupees, legit, precision, paused=0.5):
    return {"tiers": {}, "metrics": {"scam_rupees_interrupted": rupees, "legit_interruptions_per_1000": legit,
                                     "T3_precision": precision, "scam_payments_paused": paused}}


def test_pick_keeps_the_alert_budget_and_the_precision_floor():
    best_but_noisy = result(0.95, 6.0, 0.5)
    best_but_imprecise = result(0.93, 2.0, 0.1)
    fine = result(0.80, 3.9, 0.35)
    worse = result(0.70, 1.0, 0.6)
    assert pick([best_but_noisy, best_but_imprecise, fine, worse], budget=4.0) is fine
    assert pick([best_but_noisy, best_but_imprecise], budget=4.0) is None
