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

from api import rule_scorer as scorer
from features.user_baseline import Baselines
from evidence.store import EvidenceStore, pseudonym
from features.build import build_features
from api.intel import IntelAdapter
from api.policy import Policy
from schema.events import CONTRACT_VERSION, DecideRequest, DecideResponse
from features.tracker import StageTracker

log = logging.getLogger("ruko")
DEADLINE_S = 0.200


def create_app(data_dir: str | Path, intel_client: httpx.AsyncClient | None = None,
               intel_url: str = "http://127.0.0.1:8001", evidence_path: str = ":memory:",
               intel_budget_s: float = 0.150, intel_cache_ttl_s: float = 10.0,
               log_path: str | Path | None = "logs/decide.jsonl", evidence_async: bool = True,
               demo_clock: bool = False) -> FastAPI:
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
        yield

    app = FastAPI(title="RUKO risk engine", lifespan=lifespan)
    intel = IntelAdapter(client, budget_s=intel_budget_s, cache_ttl_s=intel_cache_ttl_s)
    tracker, policy = StageTracker(), Policy()
    baselines = Baselines.from_data(data_dir)
    evidence = EvidenceStore(evidence_path)
    trusted: dict[str, set[str]] = {}
    # Swappable: anything with score(features) -> same dict shape (e.g. the trained model).
    app.state.score = scorer.score
    app.state.evidence, app.state.trusted = evidence, trusted
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def fail_open(req: DecideRequest) -> DecideResponse:
        return DecideResponse(request_id=req.request_id, tier="T0", reasons=["ruko_unavailable"], fail_open=True)

    async def decide_inner(req: DecideRequest, ms: dict) -> DecideResponse:
        t = time.perf_counter()
        base = baselines.get(req.user_id)
        upi = req.payee_upi_id.strip().lower()
        is_trusted = upi in trusted.get(req.user_id, set())
        state = tracker.update(req, new_payee=upi not in base.known_payees and not is_trusted)
        ms["tracker"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        call = req.context.call if req.context else None
        res = await intel.lookup(upi, call.caller_number if call else None)
        ms["intel"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        feats = build_features(req, state, res, base, is_trusted)
        ms["features"] = (time.perf_counter() - t) * 1000

        t = time.perf_counter()
        s = app.state.score(feats)
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
        return DecideResponse(
            request_id=req.request_id, tier=d.tier, reasons=reasons, message_key=d.message_key,
            pause_minutes=d.pause_minutes, playbook=s["playbook"] if d.tier != "T0" else None,
            stage=state.stage, evidence_id=evidence_id)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model_version": scorer.MODEL_VERSION, "policy_version": policy.version,
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
        if demo_clock:  # the mock may have moved its clock since startup
            await sync_clock()
        return {"ok": True}

    return app


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(description="Run the RUKO risk engine")
    p.add_argument("--data", default="data")
    p.add_argument("--intel", default="http://127.0.0.1:8001")
    p.add_argument("--evidence", default="logs/evidence.db")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--demo-clock", action="store_true",
                   help="ignore request timestamps and use the mock's sim clock (live phone demos)")
    a = p.parse_args()
    uvicorn.run(create_app(a.data, intel_url=a.intel, evidence_path=a.evidence, demo_clock=a.demo_clock),
                host="0.0.0.0", port=a.port)


if __name__ == "__main__":
    main()
