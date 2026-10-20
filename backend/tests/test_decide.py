import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from intel_mock.app import create_app as create_mock
from api.main import create_app as create_engine
from sim.accounts import DEMO_KIRANA_UPI, DEMO_MEENA, DEMO_MULE_UPI
from sim.config import SimConfig
from sim.generate import build_world, write_world

CFG = SimConfig(seed=1, n_users=400, n_days=20, scam_rate=0.05)
NOW = CFG.end + timedelta(hours=10, minutes=40)
UNKNOWN_CALLER = "+919812345670"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    tables = build_world(CFG)
    write_world(tables, CFG, out)
    return out, tables


@pytest.fixture()
def mock(world):
    app = create_mock(world[0])
    return app, TestClient(app)


@pytest.fixture()
def engine(world, mock):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock[0]), base_url="http://mock")
    app = create_engine(world[0], intel_client=client, intel_cache_ttl_s=0, log_path=None,
                        evidence_async=False)
    return TestClient(app)


def pump(mock_client, world, n=40):
    acc = world[1]["accounts"]
    payers = acc[(acc.holder_type == "person") & ~acc.is_mule].upi_id[:n]
    for p in payers:
        mock_client.post("/npci/pay", json={"from_upi": p, "to_upi": DEMO_MULE_UPI, "amount": 1500})
    cash_out = acc[acc.mule_kind == "cash_out"].upi_id.iloc[0]
    mock_client.post("/npci/pay", json={"from_upi": DEMO_MULE_UPI, "to_upi": cash_out, "amount": 54000})
    # Warm the bank's first (slowest) calculation for this payee, as a prefetch would, so the
    # functional tests don't depend on how busy the machine is. Timing tests use real budgets.
    mock_client.get(f"/banks/axis-bank/payee-risk/{DEMO_MULE_UPI}")


def req(user=DEMO_MEENA, payee=DEMO_MULE_UPI, amount=95_000, entry="typed", says=None,
        context=None, ts=NOW, rid="r_1"):
    body = {"request_id": rid, "user_id": user, "payee_upi_id": payee, "amount_inr": amount,
            "entry_method": entry, "ts": ts.isoformat(), "context": context}
    if says:
        body["payer_says"] = says
    return body


MEENA_CONTEXT = {
    "call": {"active": True, "kind": "voip", "caller_number": UNKNOWN_CALLER,
             "caller_in_contacts": False, "started_min_ago": 38},
    "device": {"remote_access_active": True, "remote_app_installed_min_ago": 25},
    "lures": [],
}


def users_in(world, region="Pune"):
    u = world[1]["users"]
    return u[(u.region == region) & (u.user_id != DEMO_MEENA)].user_id.tolist()


# ---------- the demo stories ----------

def test_meena_digital_arrest_gets_paused(engine, mock, world):
    pump(mock[1], world)
    r = engine.post("/v1/decide", json=req(context=MEENA_CONTEXT, says="government")).json()
    assert r["tier"] in ("T3", "T4")
    assert {"R1_live_unknown_call", "R2_remote_control", "R3_mule_like_payee"} <= set(r["reasons"])
    assert r["playbook"] == "digital_arrest" and r["message_key"] == f"{r['tier'].lower()}_digital_arrest"
    assert r["pause_minutes"] > 0 and r["evidence_id"]
    assert "p_scam" not in r
    ev = engine.get(f"/v1/evidence/{r['evidence_id']}").json()
    assert DEMO_MULE_UPI not in str(ev) and UNKNOWN_CALLER not in str(ev)  # only hashes stored
    assert ev["intel_status"]["payee_registry"] == "ok"


def test_kirana_purchase_is_silent(engine, world):
    user = users_in(world)[0]
    r = engine.post("/v1/decide", json=req(user=user, payee=DEMO_KIRANA_UPI, amount=180, entry="qr")).json()
    assert r["tier"] == "T0" and r["reasons"] == []


def test_split_tranches_still_caught(engine, mock, world):
    """Adaptive fraudster: 5 x 19,000 instead of 95,000, and hangs up before the payments."""
    pump(mock[1], world)
    ctx = {"call": {"active": False, "kind": "voip", "caller_number": UNKNOWN_CALLER,
                    "caller_in_contacts": False, "started_min_ago": 50, "ended_min_ago": 5},
           "device": {"remote_access_active": True}, "lures": []}
    tiers, stages = [], []
    for i in range(5):
        r = engine.post("/v1/decide", json=req(amount=19_000, context=ctx, rid=f"r_{i}",
                                                ts=NOW + timedelta(minutes=4 * i))).json()
        tiers.append(r["tier"]), stages.append(r["stage"])
    assert stages[0] == "S4" and stages[-1] == "S5"
    assert max(tiers) >= "T3"
    assert "R7_tranche_pattern" in r["reasons"] or r["tier"] >= "T2"


# ---------- false-alarm protection ----------

def test_single_family_never_interrupts(engine, world):
    """Huge first-time payment, no phone signals, honest payee: behaviour alone is not enough."""
    user, other = users_in(world)[:2]
    payee = world[1]["users"].set_index("user_id").loc[other].upi_id
    r = engine.post("/v1/decide", json=req(user=user, payee=payee, amount=99_000)).json()
    assert r["tier"] in ("T0", "T1")


def test_registry_false_entry_alone_does_not_block(engine, world):
    rep, acc = world[1]["reports"], world[1]["accounts"]
    false = rep[rep.is_false & (rep.listed_at <= CFG.end)].identifier.iloc[0]
    assert not acc.set_index("upi_id").loc[false].is_mule
    user = users_in(world)[0]
    r = engine.post("/v1/decide", json=req(user=user, payee=false, amount=1500, entry="qr")).json()
    assert r["tier"] in ("T0", "T1")


def test_amount_cap(engine, mock, world):
    pump(mock[1], world)
    r = engine.post("/v1/decide", json=req(amount=150, context=MEENA_CONTEXT, says="government")).json()
    assert r["tier"] in ("T0", "T1")


def test_trusted_payee_suppressed_but_not_under_control(engine, world):
    user, other = users_in(world)[:2]
    payee = world[1]["users"].set_index("user_id").loc[other].upi_id
    engine.post(f"/v1/trusted/{user}/{payee}")
    calm = {"call": {"active": True, "kind": "sim", "caller_number": "+919800000001",
                     "caller_in_contacts": False, "started_min_ago": 3}, "lures": []}
    r = engine.post("/v1/decide", json=req(user=user, payee=payee, amount=60_000, context=calm)).json()
    assert r["tier"] == "T0"
    # Coached "I know this person" while someone controls the screen: the override kicks in.
    r = engine.post("/v1/decide", json=req(user=user, payee=payee, amount=60_000, context=MEENA_CONTEXT,
                                            says="government", rid="r_2")).json()
    assert r["tier"] != "T0" or "ruko_unavailable" in r["reasons"]


def _tiers_over_days(engine, user, context, n=4):
    return [engine.post("/v1/decide", json=req(user=user, amount=60_000, context=context, rid=f"r_{i}",
                                                ts=NOW + timedelta(days=i))).json()["tier"] for i in range(n)]


def test_alert_budget_on_calm_days(engine, mock, world):
    """No journey under way (no contact, no control): after 2 interruptions, medium risk only gets a tip."""
    pump(mock[1], world)
    tiers = _tiers_over_days(engine, users_in(world)[0], context={"lures": []})
    assert tiers[:2] == ["T2", "T2"]
    assert tiers[2:] == ["T1", "T1"]


def test_alert_budget_never_silences_an_active_journey(engine, mock, world):
    """Same payments, but an unknown caller is on the line: every one is still interrupted."""
    pump(mock[1], world)
    ctx = {"call": {"active": True, "kind": "sim", "caller_number": UNKNOWN_CALLER,
                    "caller_in_contacts": False, "started_min_ago": 5}, "lures": []}
    tiers = _tiers_over_days(engine, users_in(world)[0], context=ctx)
    assert all(t in ("T2", "T3", "T4") for t in tiers)


# ---------- unknown is not safe; RUKO never blocks because it broke ----------

def test_opted_out_user_still_gets_payee_protection(engine, mock, world):
    pump(mock[1], world)
    r = engine.post("/v1/decide", json=req(context=None, says="government")).json()
    assert r["tier"] in ("T2", "T3", "T4")       # payee + behaviour + story still corroborate
    assert "R1_live_unknown_call" not in r["reasons"]


def test_slow_intel_still_answers_in_time(engine, mock, world):
    mock[1].post("/sim/settings", json={"latency_ms": 500})
    t0 = time.perf_counter()
    r = engine.post("/v1/decide", json=req(context=MEENA_CONTEXT, says="government"))
    took = time.perf_counter() - t0
    assert r.status_code == 200 and took < 0.4
    # Phone signals alone (context + behaviour) still corroborate without any intel.
    assert r.json()["tier"] in ("T2", "T3")


def test_scorer_crash_fails_open(engine):
    def boom(_):
        raise RuntimeError("model broke")
    engine.app.state.score = boom
    r = engine.post("/v1/decide", json=req(context=MEENA_CONTEXT)).json()
    assert r == {**r, "tier": "T0", "reasons": ["ruko_unavailable"], "fail_open": True}


def test_intel_calls_run_in_parallel(engine, mock, world):
    """Six lookups at 60 ms each must take ~2 hops (resolve -> bank), not six."""
    mock[1].post("/sim/settings", json={"latency_ms": 60})
    t0 = time.perf_counter()
    engine.post("/v1/decide", json=req(context=MEENA_CONTEXT))
    assert time.perf_counter() - t0 < 0.3
    ev_ok = engine.get("/v1/evidence/verify").json()
    assert ev_ok["ok"]


# ---------- evidence ----------

def test_evidence_chain_detects_tampering(engine, mock, world):
    pump(mock[1], world)
    for i in range(3):
        engine.post("/v1/decide", json=req(context=MEENA_CONTEXT, says="government", rid=f"r_{i}",
                                           ts=NOW + timedelta(minutes=i)))
    v = engine.get("/v1/evidence/verify").json()
    assert v["ok"] and v["rows_checked"] >= 3
    db = engine.app.state.evidence.db
    db.execute("UPDATE evidence SET tier = 'T0' WHERE seq = 2")
    v = engine.get("/v1/evidence/verify").json()
    assert not v["ok"] and v["first_bad_id"]


def test_evidence_file_in_missing_folder(tmp_path):
    """A fresh clone has no logs/ folder; the store must create it, not crash the engine."""
    from evidence.store import EvidenceStore
    store = EvidenceStore(str(tmp_path / "logs" / "evidence.db"))
    store.write({"id": "e_1", "ts": "2026-11-07T10:40:00+05:30", "user_hash": "u", "payee_hash": "p",
                 "caller_hash": None, "tier": "T3", "p_scam": 0.9, "reasons": [], "playbook": None,
                 "family_scores": {}, "features": {}, "intel_status": {}, "stage_path": [],
                 "model_version": "m", "policy_version": "p"})
    assert store.verify() == {"ok": True, "rows_checked": 1, "first_bad_id": None}


# ---------- live phone demo ----------

def test_demo_clock_uses_sim_time_not_phone_time(world, mock):
    """A real phone sends today's date; with --demo-clock the engine decides at the sim clock."""
    pump(mock[1], world)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock[0]), base_url="http://mock")
    app = create_engine(world[0], intel_client=client, intel_cache_ttl_s=0, log_path=None,
                        evidence_async=False, demo_clock=True)
    sim_now = datetime.fromisoformat(mock[1].get("/sim/state").json()["now"])
    phone_ts = sim_now - timedelta(days=400)
    with TestClient(app) as engine:  # runs the lifespan, which reads the sim clock
        assert engine.get("/health").json()["demo_clock"] is True
        r = engine.post("/v1/decide", json=req(context=MEENA_CONTEXT, says="government",
                                               ts=phone_ts)).json()
        assert r["tier"] in ("T3", "T4")
        ev = engine.get(f"/v1/evidence/{r['evidence_id']}").json()
    assert abs(datetime.fromisoformat(ev["ts"]) - sim_now) < timedelta(minutes=1)
