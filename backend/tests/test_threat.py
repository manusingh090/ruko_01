"""c2-0.8: the phone's verdict goes in, the server's threat analysis comes out."""
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import api.main
from api import rule_scorer
from api.main import THREAT_LEVEL, load_scorer
from intel_mock.app import create_app as create_mock
from sim.accounts import DEMO_KIRANA_UPI, DEMO_MEENA, DEMO_MULE_UPI
from sim.config import SimConfig
from sim.generate import build_world, write_world

CFG = SimConfig(seed=1, n_users=400, n_days=20, scam_rate=0.05)
NOW = CFG.end + timedelta(hours=10, minutes=40)
REMOTE = {"remote_access_active": True, "remote_app_installed_min_ago": 25}
# What a real phone sends for a WhatsApp call: no number, contact status unknown.
WHATSAPP = {"active": True, "kind": "voip", "started_min_ago": 38}
VERDICT = {"model_version": "device-0.1", "fraud_class": "digital_arrest", "level": "high",
           "c": 0.99, "l": None, "b": 0.97, "reasons": ["R2_remote_control", "R12_coached_session"]}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    tables = build_world(CFG)
    write_world(tables, CFG, out)
    return out, tables


@pytest.fixture()
def stack(world, monkeypatch):
    monkeypatch.setattr(api.main, "DEADLINE_S", 10.0)  # decision logic, not speed
    mock_app = create_mock(world[0])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url="http://mock")
    app = api.main.create_app(world[0], intel_client=client, intel_cache_ttl_s=0, log_path=None,
                              evidence_async=False, intel_budget_s=5.0)
    return TestClient(mock_app), TestClient(app), app


def pump(mock, tables, n=40):
    acc = tables["accounts"]
    for p in acc[(acc.holder_type == "person") & ~acc.is_mule].upi_id[:n]:
        mock.post("/npci/pay", json={"from_upi": p, "to_upi": DEMO_MULE_UPI, "amount": 1500})


def decide(engine, context, payee=DEMO_MULE_UPI, amount=95_000, entry="typed", says="government", user=DEMO_MEENA):
    body = {"request_id": "r_1", "user_id": user, "payee_upi_id": payee, "amount_inr": amount,
            "entry_method": entry, "ts": NOW.isoformat(), "context": context}
    if says:
        body["payer_says"] = says
    r = engine.post("/v1/decide", json=body)
    r.raise_for_status()
    return r.json()


def test_threat_analysis_comes_back_with_the_tier(stack, world):
    mock, engine, _ = stack
    pump(mock, world[1])
    r = decide(engine, {"call": WHATSAPP, "device": REMOTE, "lures": []})
    t = r["threat"]
    assert r["tier"] in ("T3", "T4") and t["level"] == THREAT_LEVEL[r["tier"]]
    assert 0 <= t["score"] <= 100 and t["score"] >= 50
    assert t["fraud_type"] == r["playbook"] == "digital_arrest"
    assert {"C", "P"} <= set(t["families"])


def test_whatsapp_call_counts_as_an_unknown_caller(stack, world):
    """A real phone cannot see a VoIP caller's number: unknown is not safe (rule 1)."""
    mock, engine, _ = stack
    pump(mock, world[1])
    r = decide(engine, {"call": WHATSAPP, "device": REMOTE, "lures": []})
    assert "R1_live_unknown_call" in r["reasons"] and r["stage"] != "S0"


def test_quiet_payment_has_no_threat_level(stack, world):
    users = world[1]["users"]
    user = users[(users.region == "Pune") & (users.user_id != DEMO_MEENA)].user_id.iloc[0]
    r = decide(stack[1], None, payee=DEMO_KIRANA_UPI, amount=180, entry="qr", says=None, user=user)
    assert r["tier"] == "T0" and r["threat"]["level"] == "none" and r["threat"]["fraud_type"] is None


def test_no_threat_when_failing_open(stack):
    _, engine, app = stack
    app.state.score = lambda f: 1 / 0
    r = decide(engine, None)
    assert r["fail_open"] and r["threat"] is None


def test_device_verdict_reaches_its_own_families(stack):
    _, engine, app = stack
    seen = []
    app.state.score = lambda f: (seen.append(dict(f)), rule_scorer.score(f))[1]
    decide(engine, {"call": WHATSAPP, "device": REMOTE, "lures": [], "device_verdict": VERDICT})
    f = seen[-1]
    assert (f["device_c"], f["device_l"], f["device_b"]) == (0.99, None, 0.97)
    decide(engine, {"call": WHATSAPP, "device": REMOTE, "lures": []})
    assert seen[-1]["device_c"] is None  # no verdict: unknown, never 0


def test_phone_fraud_class_names_the_scam_when_the_server_finds_none(stack, world):
    mock, engine, app = stack
    pump(mock, world[1])
    app.state.score = lambda f: {**rule_scorer.score(f), "playbook": None}
    verdict = {**VERDICT, "fraud_class": "fake_customer_care"}
    r = decide(engine, {"call": WHATSAPP, "device": REMOTE, "lures": [], "device_verdict": verdict})
    assert r["tier"] != "T0"
    assert r["playbook"] == r["threat"]["fraud_type"] == "fake_customer_care"


def test_scorer_choice_falls_back_to_rules(monkeypatch):
    score, version = load_scorer("rules")
    assert score is rule_scorer.score and version == rule_scorer.MODEL_VERSION
    import model.predict as predict
    monkeypatch.setattr(predict, "load", lambda: (_ for _ in ()).throw(FileNotFoundError("no model")))
    assert load_scorer("ml")[1] == rule_scorer.MODEL_VERSION  # never fails: keeps the rules


def test_health_reports_the_active_scorer(stack):
    _, engine, _ = stack
    assert engine.get("/health").json()["model_version"] == rule_scorer.MODEL_VERSION
