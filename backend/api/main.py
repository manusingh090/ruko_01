"""RUKO risk engine API.

    PYTHONPATH=backend:ml:data python -m api.main --data data/ --intel http://127.0.0.1:8001 --port 8000

Add --demo-clock when a real phone is paying: the phone sends today's time, but the synthetic
world (baselines, mock intel) lives at the sim clock. With the flag, the engine ignores the
request's `ts` and uses the sim clock, read from the mock at startup and on /sim/reset.

Use 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and adds ~200 ms per new
connection, which alone blows the 150 ms intel budget.

Decide path, all inside a 200 ms deadline (fail open on timeout or any error):
    tracker.update -> intel.lookup (150 ms budget) -> build_features -> score -> policy -> evidence
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import rule_scorer as scorer
from features.user_baseline import Baselines
from evidence.store import EvidenceStore, pseudonym
from features.build import build_features
from api.intel import IntelAdapter
from api.policy import Policy
from api.episode_events import EpisodeService
from graph import discover as discovery
from graph.store import GraphStore
from schema.events import (CONTRACT_VERSION, DecideRequest, DecideResponse, EpisodeEvent, EpisodeEventResponse, Threat,
                           PaymentOutcome, PaymentOutcomeResponse)
from features.tracker import StageTracker

log = logging.getLogger("ruko")
DEADLINE_S = 0.200
MAX_ALERTS = 500
# The built analyst console (cd console && npm run build), served at /console/.
CONSOLE_DIST = Path(__file__).resolve().parents[2] / "console" / "dist"


class ReviewBody(BaseModel):
    """An analyst's decision on a discovered suspect (two different approvals are needed)."""
    reviewer: str
    decision: str  # approve | reject


def create_app(data_dir: str | Path, intel_client: httpx.AsyncClient | None = None,
               intel_url: str = "http://127.0.0.1:8001", evidence_path: str = ":memory:",
               intel_budget_s: float = 0.150, intel_cache_ttl_s: float = 10.0,
               log_path: str | Path | None = "logs/decide.jsonl", evidence_async: bool = True,
               demo_clock: bool = False, discover_every_s: float | None = None,
               scorer_name: str = "rules") -> FastAPI:
    """`scorer_name`: "rules" (api/rule_scorer.py) or "ml" (the trained LightGBM model in
    ml/model/predict.py). If the model cannot be loaded, the engine keeps the rules (never fails)."""
    client = intel_client or httpx.AsyncClient(base_url=intel_url)
    # sim time - wall time; None means "use the request's ts".
    clock: dict[str, timedelta | None] = {"offset": None}

    async def sync_clock() -> None:
        try:
            r = await client.get("/sim/state")
            sim_now = datetime.fromisoformat(r.raise_for_status().json()["now"])
            clock["offset"] = sim_now - datetime.now(sim_now.tzinfo)
            log.info("demo clock: sim now %s", sim_now.isoformat())
        except (httpx.HTTPError, KeyError, ValueError):
            log.warning("demo clock: sim state not reachable; using request timestamps")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Open the connection pool before the first payment so it doesn't pay connect costs.
        try:
            await asyncio.gather(*(client.get("/health") for _ in range(6)))
        except httpx.HTTPError:
            log.warning("intel services not reachable at startup; lookups will count as unknown")
        if demo_clock:
            await sync_clock()
        task = asyncio.create_task(discover_loop()) if discover_every_s else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="RUKO risk engine", lifespan=lifespan)
    intel = IntelAdapter(client, budget_s=intel_budget_s, cache_ttl_s=intel_cache_ttl_s)
    tracker, policy = StageTracker(), Policy()
    baselines = Baselines.from_data(data_dir)
    evidence = EvidenceStore(evidence_path)
    trusted: dict[str, set[str]] = {}
    # Swappable: anything with score(features) -> same dict shape (e.g. the trained model).
    app.state.score, app.state.model_version = load_scorer(scorer_name)
    app.state.evidence, app.state.trusted = evidence, trusted
    graph = GraphStore()
    app.state.graph = graph
    reviews: dict[str, discovery.Review] = {}
    found: dict = {"suspects": [], "last_run": None}
    app.state.reviews = reviews
    # Payment checks that got T1 or higher, newest last, for the analyst console.
    alerts: list[dict] = []
    app.state.alerts = alerts
    eval_dir = Path(data_dir) / "eval"

    def sim_now() -> datetime:
        now = datetime.now().astimezone()
        return now + clock["offset"] if clock["offset"] is not None else now

    def run_discovery(min_band: str = "med") -> list[dict]:
        now = sim_now()
        try:
            found["suspects"] = discovery.discover(graph, reviews, now, min_band=min_band)
            found["last_run"] = discovery.last_run_summary(found["suspects"], now)
        except Exception as e:  # a broken run must never affect payments
            log.exception("discovery failed")
            found["last_run"] = discovery.last_run_summary([], now, error=str(e))
        return found["suspects"]

    async def discover_loop() -> None:
        while True:
            await asyncio.sleep(discover_every_s)
            await asyncio.get_running_loop().run_in_executor(None, run_discovery)
    episodes = EpisodeService(intel, evidence, graph=graph)
    app.state.episodes = episodes
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def fail_open(req: DecideRequest) -> DecideResponse:
        return DecideResponse(request_id=req.request_id, tier="T0", reasons=["ruko_unavailable"], fail_open=True)

    async def decide_inner(req: DecideRequest, ms: dict) -> DecideResponse:
        t = time.perf_counter()
        base = baselines.get(req.user_id, at=req.ts)  # only what the bank knew before this payment
        upi = req.payee_upi_id.strip().lower()
        is_trusted = upi in trusted.get(req.user_id, set())
        # Known payee: the bank's history, or a payment here the phone reported completed (c2-0.6).
        paid_here = graph.paid_before(req.user_id, upi, req.ts)
        state = tracker.update(req, new_payee=upi not in base.known_payees and not paid_here and not is_trusted)
        ms["tracker"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        call = req.context.call if req.context else None
        res = await intel.lookup(upi, call.caller_number if call else None)
        ms["intel"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        # The payment graph: this check is an edge, and the payee's crowd / loop signals are read
        # from what episodes already added (no extra lookups on the payment path).
        graph.add_payment(req.user_id, upi, req.amount_inr, req.ts, request_id=req.request_id)
        graph.expire(req.ts)
        ring = graph.account_signals(upi, req.ts)
        # Discovered suspects count only after two analysts approved them (§9.5).
        ring["approved_suspect"] = upi in discovery.approved_suspects(reviews)
        feats = build_features(req, state, res, base, is_trusted, ring=ring, paid_here=paid_here)
        ms["features"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        s = app.state.score(feats)
        # The phone's fraud class names the scam when the server's playbook matcher found none (c2-0.8),
        # e.g. a WhatsApp "digital arrest" call whose number the phone cannot see.
        dv = req.context.device_verdict if req.context else None
        if s["playbook"] is None and dv is not None and dv.fraud_class is not None:
            s = {**s, "playbook": dv.fraud_class}
        ms["score"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        control_seen = any(stage == "S3" for stage, _ in state.stage_path) or bool(feats["remote_control"])
        d = policy.decide(s, feats, state.stage, control_seen, req.user_id, req.ts)
        ms["policy"] = (time.perf_counter() - t) * 1000

        evidence_id = None
        if d.tier != "T0" or d.applied:
            evidence_id = f"e_{uuid.uuid4().hex[:12]}"
            row = {
                "id": evidence_id, "ts": req.ts.isoformat(), "user_hash": pseudonym(req.user_id),
                "payee_hash": pseudonym(upi), "caller_hash": pseudonym(call.caller_number if call else None),
                "tier": d.tier, "p_scam": s["p_scam"], "reasons": s["reasons"], "playbook": s["playbook"],
                "family_scores": {**s["family_scores"], "lit": d.lit_families, "policy_rules": d.applied},
                "features": feats, "intel_status": res.status,
                "stage_path": [(stage, ts.isoformat()) for stage, ts in state.stage_path],
                "model_version": s["model_version"], "policy_version": policy.version,
            }
            if evidence_async:  # off the latency path
                asyncio.get_running_loop().run_in_executor(None, evidence.write, row)
            else:
                evidence.write(row)

        reasons = s["reasons"] if d.tier != "T0" else []
        graph.set_decision(req.request_id, d.tier, reasons, evidence_id)
        if d.tier != "T0":
            alerts.append({
                "alert_id": evidence_id or f"a_{uuid.uuid4().hex[:12]}", "payment_id": req.request_id,
                "ts": req.ts.isoformat(),
                "user_hash": pseudonym(req.user_id), "payee": upi, "amount_inr": req.amount_inr,
                "tier": d.tier, "reasons": reasons, "message_key": d.message_key,
                "playbook": s["playbook"], "stage": state.stage, "pause_minutes": d.pause_minutes,
                "stage_path": [(stage, ts.isoformat()) for stage, ts in state.stage_path],
                "families": d.lit_families, "evidence_id": evidence_id, "entry_method": req.entry_method,
            })
            del alerts[:-MAX_ALERTS]
        return DecideResponse(
            request_id=req.request_id, tier=d.tier, reasons=reasons, message_key=d.message_key,
            pause_minutes=d.pause_minutes, playbook=s["playbook"] if d.tier != "T0" else None,
            stage=state.stage, evidence_id=evidence_id, threat=threat_of(s, d))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model_version": app.state.model_version, "policy_version": policy.version,
                "contract_version": CONTRACT_VERSION, "demo_clock": clock["offset"] is not None}

    @app.post("/v1/decide", response_model=DecideResponse)
    async def decide(req: DecideRequest) -> DecideResponse:
        t0, ms = time.perf_counter(), {}
        if clock["offset"] is not None:
            req = req.model_copy(update={"ts": datetime.now(req.ts.tzinfo) + clock["offset"]})
        try:
            resp = await asyncio.wait_for(decide_inner(req, ms), timeout=DEADLINE_S)
        except asyncio.TimeoutError:
            log.warning("decide timed out")
            resp = fail_open(req)
        except Exception:
            log.exception("decide failed")
            resp = fail_open(req)
        ms["total"] = (time.perf_counter() - t0) * 1000
        if log_path:
            with open(log_path, "a") as fh:
                fh.write(json.dumps({"ts": req.ts.isoformat(), "request_id": req.request_id,
                                     "tier": resp.tier, **{f"ms_{k}": round(v, 2) for k, v in ms.items()}}) + "\n")
        return resp

    @app.post("/v1/payment/outcome", response_model=PaymentOutcomeResponse)
    def payment_outcome(o: PaymentOutcome) -> PaymentOutcomeResponse:
        """After the PIN or Cancel (c2-0.6): the payment's outcome goes into the payment graph.
        Never on the payment path; the phone retries until it gets an answer."""
        ts = o.ts
        if clock["offset"] is not None:
            ts = datetime.now(o.ts.tzinfo) + clock["offset"]
        status, recorded = graph.record_outcome(o.request_id, o.user_id, o.payee_upi_id, o.amount_inr,
                                                o.status, ts)
        return PaymentOutcomeResponse(request_id=o.request_id, status=status, recorded=recorded)

    @app.post("/v1/episode", response_model=EpisodeEventResponse)
    async def episode_event(ev: EpisodeEvent) -> EpisodeEventResponse:
        """The phone's watcher changed state (c2-0.4). Never needed by /v1/decide; missing means unknown."""
        now = ev.ts
        if clock["offset"] is not None:
            now = datetime.now(ev.ts.tzinfo) + clock["offset"]
        try:
            return await episodes.handle(ev, now)
        except Exception:
            log.exception("episode event failed")
            return EpisodeEventResponse(event_id=ev.event_id, episode_risk="unknown", reasons=[])

    @app.get("/v1/cases")
    def cases() -> list[dict]:
        """Analyst cases opened by high-risk episodes (console, C5)."""
        return episodes.cases

    @app.get("/v1/alerts")
    def list_alerts(min_tier: str = "T1", limit: int = 200) -> list[dict]:
        """Payment checks that got min_tier or higher, newest first (console, C5)."""
        rank = {"T1": 1, "T2": 2, "T3": 3, "T4": 4}
        if min_tier not in rank:
            raise HTTPException(422, "min_tier must be T1, T2, T3 or T4")
        keep = [a for a in alerts if rank[a["tier"]] >= rank[min_tier]]
        return keep[::-1][:max(1, min(limit, MAX_ALERTS))]

    @app.get("/v1/eval")
    def eval_results() -> dict:
        """The latest offline evaluation results, for the console's Evaluation page."""
        out = {}
        for name in ("episode", "graph"):
            f = eval_dir / f"{name}_results.json"
            out[name] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return out

    # ---------- payment graph for the analyst console (contract C5, §9.7) ----------

    @app.get("/v1/graph/account/{upi_id}")
    def graph_account(upi_id: str, hops: int = 2) -> dict:
        out = graph.neighbourhood(upi_id, hops=max(1, min(hops, 3)))
        out["signals"] = graph.account_signals(upi_id, sim_now())
        return out

    @app.get("/v1/graph/story/{upi_id}")
    def graph_story(upi_id: str) -> dict:
        """The account's money flow as a short story: who baited whom, who paid it, where it went."""
        return graph.story(upi_id, sim_now())

    @app.get("/v1/graph/payments/{upi_id}")
    def graph_payments(upi_id: str, limit: int = 20) -> dict:
        """Payments into an account, newest first, each with its payment id, decision and outcome."""
        return graph.payments_for(upi_id, limit=max(1, min(limit, 100)))

    @app.get("/v1/payments/{payment_id}")
    def payment_by_id(payment_id: str) -> dict:
        """Trace one payment by its id (the /v1/decide request_id) from the check to its outcome."""
        p = graph.payment(payment_id)
        if p is None:
            raise HTTPException(404, "no payment with that id in the last 30 days")
        return p

    @app.get("/v1/graph/suspects")
    def graph_suspects(min_band: str = "med") -> dict:
        """Analysts see medium and high suspects by default; ?min_band=low shows everything."""
        if min_band not in discovery.BAND_RANK:
            raise HTTPException(422, "min_band must be low, med or high")
        return {"suspects": run_discovery(min_band), "last_run": found["last_run"],
                "approvals_needed": discovery.APPROVALS_NEEDED}

    @app.post("/v1/graph/suspects/{upi_id}/review")
    def graph_review(upi_id: str, body: ReviewBody) -> dict:
        if body.decision not in ("approve", "reject"):
            raise HTTPException(422, "decision must be approve or reject")
        r = discovery.review(reviews, upi_id, body.reviewer.strip(), body.decision)
        return {"upi_id": upi_id.strip().lower(), "approved_by": sorted(r.approved_by),
                "rejected_by": sorted(r.rejected_by), "approved": r.approved}

    @app.get("/console/graph", include_in_schema=False)
    def console_graph() -> RedirectResponse:
        """Old address of the graph page; the analyst console replaced it."""
        return RedirectResponse("/console/#/explore")

    @app.post("/v1/trusted/{user_id}/{payee_upi_id}")
    def mark_trusted(user_id: str, payee_upi_id: str) -> dict:
        """The user tapped "I know this person"."""
        trusted.setdefault(user_id, set()).add(payee_upi_id.strip().lower())
        return {"ok": True}

    @app.get("/v1/evidence/verify")
    def verify() -> dict:
        return evidence.verify()

    @app.get("/v1/evidence/{evidence_id}")
    def get_evidence(evidence_id: str) -> dict:
        row = evidence.get(evidence_id)
        if row is None:
            raise HTTPException(404, "no such evidence")
        return row

    @app.post("/sim/reset")
    async def reset() -> dict:
        tracker.reset()
        policy.reset()
        evidence.clear()
        trusted.clear()
        episodes.reset()
        graph.reset()
        reviews.clear()
        alerts.clear()
        found.update(suspects=[], last_run=None)
        if demo_clock:  # the mock may have moved its clock since startup
            await sync_clock()
        return {"ok": True}

    if CONSOLE_DIST.is_dir():
        app.mount("/console", StaticFiles(directory=CONSOLE_DIST, html=True), name="console")
    else:
        log.info("analyst console not built; run: cd console && npm install && npm run build")
    return app


THREAT_LEVEL = {"T0": "none", "T1": "low", "T2": "medium", "T3": "high", "T4": "critical"}


def threat_of(s: dict, d) -> Threat:
    """The threat analysis the app shows (c2-0.8): score 0..100, a level in words from the tier, the
    known scam it matches, and the families that agree."""
    return Threat(score=max(0, min(100, round(100 * s["p_scam"]))), level=THREAT_LEVEL[d.tier],
                  fraud_type=s["playbook"] if d.tier != "T0" else None, families=list(d.lit_families))


def load_scorer(name: str):
    """(score function, model version). Falls back to the rules if the trained model is missing."""
    if name == "ml":
        try:
            from model import predict  # ml/ is on PYTHONPATH (backend;ml;data)
            model = predict.load()
            return predict.score, model.version
        except Exception:
            log.exception("trained model not available; using the rules")
    return scorer.score, scorer.MODEL_VERSION


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(description="Run the RUKO risk engine")
    p.add_argument("--data", default="data")
    p.add_argument("--intel", default="http://127.0.0.1:8001")
    p.add_argument("--evidence", default="logs/evidence.db")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--demo-clock", action="store_true",
                   help="ignore request timestamps and use the mock's sim clock (live phone demos)")
    p.add_argument("--scorer", choices=("ml", "rules"), default="ml",
                   help="ml: the trained LightGBM model (falls back to rules if missing); rules: rules-only")
    a = p.parse_args()
    uvicorn.run(create_app(a.data, intel_url=a.intel, evidence_path=a.evidence, demo_clock=a.demo_clock,
                           discover_every_s=3600, scorer_name=a.scorer),
                host="0.0.0.0", port=a.port)


if __name__ == "__main__":
    main()
