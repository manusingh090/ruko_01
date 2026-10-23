"""Payment ids and outcomes (contract c2-0.6): each payment is traced by its /v1/decide request_id,
and the phone's completed / cancelled report updates the payment graph."""
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app as create_engine
from graph.store import KEEP, GraphStore
from intel_mock.app import create_app as create_mock
from sim.accounts import DEMO_KIRANA_UPI, DEMO_MEENA
from sim.config import SimConfig
from sim.generate import build_world, write_world
from schema.events import CONTRACT_VERSION

CFG = SimConfig(seed=5, n_users=200, n_days=10, scam_rate=0.05)
NOW = CFG.end + timedelta(hours=10)
PAYEE = "ravi.kumar@oksbi"


# ---------- unit: the store on its own ----------

def checked(g: GraphStore, rid="p1", user="u_a", payee=PAYEE, inr=2_000.0, tier="T0"):
    g.add_payment(user, payee, inr, NOW, request_id=rid)
    g.set_decision(rid, tier, [], None)


def test_completed_payment_updates_the_edge_and_its_record():
    g = GraphStore()
    checked(g, tier="T1")
    assert g.record_outcome("p1", "u_a", PAYEE, 2_000, "completed", NOW + timedelta(minutes=1)) == ("completed", True)
    p = g.payment("p1")
    assert p["status"] == "completed" and p["tier"] == "T1" and p["checked"]
    e = next(e for (a, b), e in g.edges.items() if b == PAYEE)
    assert (e.n, e.paid_n, e.paid_inr) == (1, 1, 2_000)
    listed = g.payments_for(PAYEE)
    assert listed["by_status"] == {"checked": 0, "completed": 1, "cancelled": 0} and listed["completed_inr"] == 2_000


def test_cancelled_is_recorded_but_not_paid():
    g = GraphStore()
    checked(g)
    assert g.record_outcome("p1", "u_a", PAYEE, 2_000, "cancelled", NOW) == ("cancelled", True)
    e = next(e for (a, b), e in g.edges.items() if b == PAYEE)
    assert e.paid_n == 0 and g.payment("p1")["status"] == "cancelled"


def test_first_final_status_wins_and_repeats_are_safe():
    g = GraphStore()
    checked(g)
    g.record_outcome("p1", "u_a", PAYEE, 2_000, "completed", NOW)
    assert g.record_outcome("p1", "u_a", PAYEE, 2_000, "completed", NOW) == ("completed", False)
    assert g.record_outcome("p1", "u_a", PAYEE, 2_000, "cancelled", NOW) == ("completed", False)
    assert next(e for (a, b), e in g.edges.items() if b == PAYEE).paid_n == 1


def test_another_users_payment_id_is_not_touched():
    g = GraphStore()
    checked(g, user="u_a")
    assert g.record_outcome("p1", "u_b", PAYEE, 2_000, "cancelled", NOW) == ("cancelled", False)
    assert g.payment("p1")["status"] == "checked"


def test_unchecked_payment_is_still_traced_and_capped():
    """The check timed out on the phone, but the payment went through: it still enters the graph."""
    g = GraphStore()
    assert g.record_outcome("p9", "u_a", PAYEE, 500, "completed", NOW) == ("completed", True)
    p = g.payment("p9")
    assert not p["checked"] and p["tier"] is None and p["status"] == "completed"
    e = next(e for (a, b), e in g.edges.items() if b == PAYEE)
    assert "outcome" in e.sources and e.paid_n == 1
    for i in range(30):
        g.record_outcome(f"spam{i}", "u_a", f"x{i}@ybl", 1, "completed", NOW)
    assert sum(1 for r in g.payments if r.startswith("spam")) < 30


def test_user_is_pseudonymised_and_old_payments_expire():
    g = GraphStore()
    checked(g, user=DEMO_MEENA)
    assert DEMO_MEENA not in str(g.payment("p1"))
    g.expire(NOW + KEEP + timedelta(days=1))
    assert g.payment("p1") is None and g.payments_for(PAYEE)["total"] == 0


# ---------- through the API ----------

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    write_world(build_world(CFG), CFG, out)
    return out


@pytest.fixture()
def engine(world):
    mock = create_mock(world)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock), base_url="http://mock")
    return TestClient(create_engine(world, intel_client=client, intel_cache_ttl_s=0, log_path=None,
                                    evidence_async=False))


def decide(engine, rid, payee=DEMO_KIRANA_UPI, amount=240):
    return engine.post("/v1/decide", json={
        "request_id": rid, "user_id": DEMO_MEENA, "payee_upi_id": payee, "amount_inr": amount,
        "entry_method": "qr", "ts": NOW.isoformat(), "context": None}).json()


def outcome(engine, rid, status, payee=DEMO_KIRANA_UPI, amount=240):
    return engine.post("/v1/payment/outcome", json={
        "request_id": rid, "user_id": DEMO_MEENA, "payee_upi_id": payee, "amount_inr": amount,
        "status": status, "ts": (NOW + timedelta(minutes=1)).isoformat()})


def test_a_payment_is_traced_from_check_to_outcome(engine):
    d = decide(engine, "pay-1")
    r = outcome(engine, "pay-1", "completed")
    assert r.status_code == 200 and r.json() == {"request_id": "pay-1", "status": "completed", "recorded": True,
                                                 "contract_version": CONTRACT_VERSION}
    p = engine.get("/v1/payments/pay-1").json()
    assert p["payment_id"] == "pay-1" and p["tier"] == d["tier"] and p["status"] == "completed" and p["checked"]
    listed = engine.get(f"/v1/graph/payments/{DEMO_KIRANA_UPI}").json()
    assert listed["payments"][0]["payment_id"] == "pay-1" and listed["by_status"]["completed"] == 1
    link = next(lk for lk in engine.get(f"/v1/graph/story/{DEMO_KIRANA_UPI}").json()["links"] if lk["kind"] == "pay")
    assert link["completed"] == 1 and link["completed_inr"] == 240


def test_cancel_then_retry_are_two_payments(engine):
    decide(engine, "pay-a")
    outcome(engine, "pay-a", "cancelled")
    decide(engine, "pay-b")
    outcome(engine, "pay-b", "completed")
    listed = engine.get(f"/v1/graph/payments/{DEMO_KIRANA_UPI}").json()
    assert {p["payment_id"]: p["status"] for p in listed["payments"]} == {"pay-a": "cancelled", "pay-b": "completed"}


def test_unknown_payment_id_is_404_and_bad_status_is_422(engine):
    assert engine.get("/v1/payments/nope").status_code == 404
    assert outcome(engine, "pay-x", "pending").status_code == 422


def test_paid_before_needs_a_completed_payment_by_the_same_user():
    g = GraphStore()
    checked(g, user="u_a")
    assert not g.paid_before("u_a", PAYEE, NOW + timedelta(hours=1))  # only checked
    g.record_outcome("p1", "u_a", PAYEE, 2_000, "completed", NOW + timedelta(minutes=1))
    assert g.paid_before("u_a", PAYEE.upper(), NOW + timedelta(hours=1))
    assert not g.paid_before("u_a", PAYEE, NOW)          # not before it happened
    assert not g.paid_before("u_b", PAYEE, NOW + timedelta(hours=1))
    checked(g, rid="p2", user="u_c")
    g.record_outcome("p2", "u_c", PAYEE, 2_000, "cancelled", NOW)
    assert not g.paid_before("u_c", PAYEE, NOW + timedelta(hours=1))
