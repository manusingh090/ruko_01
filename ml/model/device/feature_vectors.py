"""Test cases for the phone's feature code: readings in, device features and verdict out.

    PYTHONPATH=backend;ml;data python -m model.device.feature_vectors

Builds a small simulated world, rebuilds what each phone measured at real payments (calls, apps,
lure hits, the episode, the Pay screen, contacts, its payment history) and records what
dataset/device_features.py and model/device/verdict.py make of it. DeviceFeaturesTest.kt must give
the same answers -> artifacts/v0/device_feature_vectors.json
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dataset.episodes import EpisodeFeed
from dataset.phone_view import PhoneView, Traits
from dataset.replay import DEVICE_MODEL, load_world, phone_stream, world_start
from dataset.device_features import ALL as DEVICE_FEATURES
from model.device import score as device_score
from model.device.verdict import verdict
from sim.config import SimConfig
from sim.generate import build_world, write_world

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "v0" / "device_feature_vectors.json"
CFG = SimConfig(seed=5, n_users=300, n_days=20, scam_rate=0.08)


def inputs_of(view: PhoneView, uid: str, info: dict, req, any_shield: bool) -> dict:
    raw, t = info["raw"], info["t"]
    upi = view.user_upi[uid]
    ts, to, amt = view.payments.before(upi, t)
    return {"call": raw["call"], "device": raw["device"],
            "lure_hits": None if raw["lure_hits"] is None else [{"tag": g, "conf": c, "min_ago": m} for g, c, m in raw["lure_hits"]],
            "episode": raw["episode"], "session": raw["session"], "payee_in_contacts": raw["payee_in_contacts"],
            "history": [{"ts": a, "payee": b, "amount": c} for a, b, c in zip(ts, to, amt)],
            "amount": req.amount_inr, "entry": req.entry_method, "payee": req.payee_upi_id, "now": t,
            "any_shield": any_shield}


def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        write_world(build_world(CFG), CFG, Path(d))
        tables, manifest = load_world(d)
        view = PhoneView(tables, seed=0)
        for i, uid in enumerate(view.traits):  # every mix of switches, some phones with none
            on = i % 9 != 0
            view.traits[uid] = Traits(call_signals=on and i % 5 != 1, app_checks=on, sms=on and i % 4 != 3,
                                      share_accounts=False, session_signals=on and i % 6 != 2, android_15=i % 2 == 0)
        model = device_score.load(DEVICE_MODEL)
        feed = EpisodeFeed(view.traits, world_start(manifest))
        picked: dict[str, list] = {k: [] for k in ("scam", "call", "lures", "episode", "warm", "no_shield", "other")}
        for item in phone_stream(tables, view, feed, seed=0, device_model=model):
            if item[0] != "pay":
                continue
            _, req, info = item
            raw, dev = info["raw"], info["dev"]
            kind = ("scam" if info["is_scam"] else "no_shield" if dev["dev_amount_log10"] is None
                    else "episode" if raw["episode"] and raw["episode"]["state"] != "none"
                    else "call" if raw["call"] and raw["call"]["kind"] else "lures" if raw["lure_hits"]
                    else "warm" if (dev["dev_hist_n"] or 0) >= 10 else "other")
            if len(picked[kind]) < 8:
                picked[kind].append((req, info))
        cases = []
        for kind, items in picked.items():
            for req, info in items:
                any_shield = info["dev"]["dev_amount_log10"] is not None
                cases.append({"case": f"{kind} {info['tx']}", "inputs": inputs_of(view, req.user_id, info, req, any_shield),
                              "features": info["dev"], "verdict": info["verdict"]})
        # Verdicts for every fraud class the phone can name, from hand-made feature sets.
        base = {**dict.fromkeys(DEVICE_FEATURES), "dev_amount_log10": 4.98, "dev_entry_typed": 1.0, "dev_entry_qr": 0.0,
                "dev_first_time_payee": 1.0, "dev_hist_n": 30.0, "dev_out_1h": 0.0, "dev_out_24h": 0.0}
        call = {"dev_call_active": 1.0, "dev_call_minutes": 38.0, "dev_remote_installed": 1.0, "dev_remote_min_ago": 25.0}
        verdict_cases = [
            ("whatsapp call + remote app", {**base, **call, "dev_call_voip": 1.0}),
            ("stranger's SIM call + screen shared", {**base, **call, "dev_call_voip": 0.0, "dev_call_unknown": 1.0,
                                                     "dev_call_incoming": 1.0, "dev_remote_min_ago": 400.0, "dev_screen_recorded": 1.0}),
            ("outgoing call to a helpline + remote app", {**base, **call, "dev_call_voip": 0.0, "dev_call_incoming": 0.0}),
            ("arrest threat SMS", {**base, "dev_lure_max": 0.9, "dev_lure_arrest": 0.9}),
            ("trading tip SMS", {**base, "dev_lure_max": 0.8, "dev_lure_trading": 0.8}),
            ("task job SMS", {**base, "dev_lure_max": 0.7, "dev_lure_task": 0.7}),
            ("active task episode", {**base, "dev_ep_state": 2.0, "dev_ep_bait_credits": 3.0, "dev_ep_outs": 3.0}),
            ("sideloaded app + accessibility", {**base, "dev_sideloaded_24h": 1.0, "dev_accessibility": 1.0}),
            ("stranger's call, paying by QR", {**base, "dev_call_active": 1.0, "dev_call_voip": 1.0, "dev_entry_qr": 1.0,
                                               "dev_entry_typed": 0.0}),
            ("refund SMS", {**base, "dev_lure_max": 0.65, "dev_lure_refund": 0.65}),
            ("KYC SMS", {**base, "dev_lure_max": 0.75, "dev_lure_kyc": 0.75}),
            ("weak KYC SMS (below 0.6)", {**base, "dev_lure_max": 0.55, "dev_lure_kyc": 0.55}),
            ("nothing unusual", base),
        ]
    OUT.write_text(json.dumps({"device_model_version": model["version"], "tolerance": 1e-9, "vectors": cases,
                               "verdict_cases": [{"case": n, "features": f, "verdict": verdict(model, f)}
                                                 for n, f in verdict_cases]}, indent=1), encoding="utf-8")
    print(f"{len(cases)} cases ({', '.join(f'{k} {len(v)}' for k, v in picked.items())}) -> {OUT}")


if __name__ == "__main__":
    main()
