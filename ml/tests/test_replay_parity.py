"""The replay must compute exactly the features the live engine computes for the same phones.

Both sides get the same world and the same time-ordered stream of what the phones send: episode
events (POST /v1/episode) and payments (POST /v1/decide). The live side is the real engine over
HTTP against the real mock services, with the mock clock set to each message's time; the replay
side runs the same code without HTTP on the payment path.
"""
import math

import httpx
import pytest
from fastapi.testclient import TestClient

import api.main
from api import rule_scorer
from dataset.episodes import EpisodeFeed
from dataset.phone_view import PhoneView, Traits
from dataset.replay import DEVICE_MODEL, EngineReplay, load_world, phone_stream, world_start
from model.device import score as device_score
from features.user_baseline import Baselines
from intel_mock.app import create_app as create_mock
from intel_mock.store import Store
from sim.config import SimConfig
from sim.generate import build_world, write_world

CFG = SimConfig(seed=3, n_users=300, n_days=20, scam_rate=0.08)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("world")
    write_world(build_world(CFG), CFG, out)
    return out


@pytest.fixture(scope="module")
def stream(world):
    tables, manifest = load_world(world)
    view = PhoneView(tables, seed=0)
    # Most phones with everything on, so every feature is exercised; every 7th phone opted out.
    for i, uid in enumerate(view.traits):
        on = i % 7 != 0
        view.traits[uid] = Traits(call_signals=on, app_checks=on, sms=on, share_accounts=on and i % 2 == 0,
                                  session_signals=on, android_15=i % 2 == 0)
    feed = EpisodeFeed(view.traits, world_start(manifest))
    # The phones also send their own verdict (c2-0.8), computed by the shipped device model.
    items = list(phone_stream(tables, view, feed, seed=0, device_model=device_score.load(DEVICE_MODEL)))
    # Every episode event and scam payment, plus every 12th other payment (the live side is slow).
    picked, n = [], 0
    for item in items:
        if item[0] == "pay" and not item[2]["is_scam"]:
            n += 1
            if n % 12:
                continue
        picked.append(item)
    return picked


def live_features(data_dir, items, monkeypatch) -> list[dict]:
    mock_app = create_mock(data_dir)
    mock = TestClient(mock_app)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url="http://mock")
    monkeypatch.setattr(api.main, "DEADLINE_S", 60.0)  # compare values, not speed
    app = api.main.create_app(data_dir, intel_client=client, intel_cache_ttl_s=0, log_path=None,
                              evidence_async=False, intel_budget_s=60.0)
    seen = []
    app.state.score = lambda f: (seen.append(dict(f)), rule_scorer.score(f))[1]
    engine = TestClient(app)
    for item in items:
        if item[0] == "episode":
            mock.post("/sim/clock", json={"set": item[1]["ts"]}).raise_for_status()
            engine.post("/v1/episode", json=item[1]).raise_for_status()
            continue
        req = item[1]
        mock.post("/sim/clock", json={"set": req.ts.isoformat()}).raise_for_status()
        r = engine.post("/v1/decide", json=req.model_dump(mode="json"))
        r.raise_for_status()
        assert not r.json()["fail_open"]
        # After the PIN the phone reports the payment completed (c2-0.6).
        engine.post("/v1/payment/outcome", json={
            "request_id": req.request_id, "user_id": req.user_id, "payee_upi_id": req.payee_upi_id,
            "amount_inr": req.amount_inr, "status": "completed", "ts": req.ts.isoformat()}).raise_for_status()
    return seen


def same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
    return a == b and type(a) is type(b)


def test_replay_matches_the_live_engine(world, stream, monkeypatch):
    live = live_features(world, stream, monkeypatch)

    engine = EngineReplay(Store.load(world), world)
    baselines = Baselines.from_data(world)
    replayed, statuses, reqs = [], [], []
    for item in stream:
        if item[0] == "episode":
            engine.episode_event(item[1])
            continue
        req = item[1]
        _, res, f = engine.step(req, baselines.get(req.user_id, at=req.ts))
        engine.outcome(req)
        replayed.append(f)
        statuses.append(res.status)
        reqs.append(req)

    assert len(live) == len(replayed) == len(reqs)
    for req, a, b in zip(reqs, live, replayed):
        assert a.keys() == b.keys()
        diff = {k: (a[k], b[k]) for k in a if not same(a[k], b[k])}
        assert not diff, f"{req.request_id}: live vs replay {diff}"

    # The comparison must have exercised the interesting paths, not just empty lookups.
    assert sum(s.get("bank") == "ok" for s in statuses) >= 20
    assert sum(s.get("bank") == "not_shared" for s in statuses) >= 5
    assert sum(r.context is None for r in reqs) >= 3
    assert sum(r.context is not None and r.context.session is not None for r in reqs) >= 20
    assert sum(r.context is not None and r.context.episode is not None for r in reqs) >= 20
    assert sum(item[0] == "episode" for item in stream) >= 1
    assert sum(f["payee_registry_listed"] is True for f in replayed) >= 1
    assert sum(f["payee_in_contacts"] is True for f in replayed) >= 5
    assert sum(f["payee_in_contacts"] is False for f in replayed) >= 5
    assert sum(f["device_c"] is not None for f in replayed) >= 20 and sum(f["device_c"] is None for f in replayed) >= 3
