"""Point-in-time replay: a simulated world -> one training row per payment by an app user (c2-0.5).

Every payment becomes the request the phone would have sent (phone_view.py, episodes.py) and goes
through the live engine's decide path (backend/api/main.py) without HTTP:
    tracker -> intel (the mock services' own code, clock at the payment) -> payment graph (every
    checked payment, plus the episode events phones with "Share suspicious accounts" on sent to
    POST /v1/episode, handled by the engine's own EpisodeService) -> build_features
After each check the phone reports the payment completed (POST /v1/payment/outcome, c2-0.6), so
"paid here before" counts as a known payee exactly as in the engine.
The payer's baseline uses only earlier payments (Baselines.get(at=...), as the engine does).
Device-only features (never sent) get the `dev_` prefix. Ground truth only labels the rows.

    PYTHONPATH=backend;ml;data python -m dataset.replay --data ml/data/worlds/dev --out ml/data/tables/dev.parquet
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import httpx
import pandas as pd
from fastapi import HTTPException

from api.episode_events import EpisodeService
from api.intel import NUMBER_UPI, IntelAdapter, IntelResult
from dataset.device_features import device_features
from dataset.episodes import EpisodeFeed
from dataset.graph import ReplayGraphStore
from dataset.phone_view import PhoneView, epoch_seconds, keyed_rng, load_assumptions
from evidence.store import EvidenceStore
from features.build import build_features
from features.tracker import StageTracker
from features.user_baseline import Baselines, UserBaseline
from intel_mock import bank, dot_fri, i4c_registry, npci
from intel_mock.app import create_app as create_mock
from intel_mock.store import Store
from model.device import score as device_score
from model.device.verdict import verdict as device_verdict
from schema.events import Context, DecideRequest, DeviceVerdict, EpisodeEvent

TABLES = ("users", "accounts", "ledger", "context_events", "journeys")
DEVICE_MODEL = Path(__file__).resolve().parents[1] / "model" / "artifacts" / "v0" / "device_model.json"
STATUS = {404: "not_found", 403: "not_shared"}  # same mapping as api.intel.IntelAdapter._get


def load_world(data_dir: str | Path) -> tuple[dict[str, pd.DataFrame], dict]:
    d = Path(data_dir)
    tables = {name: pd.read_parquet(d / f"{name}.parquet") for name in TABLES}
    return tables, json.loads((d / "manifest.json").read_text())


def world_start(manifest: dict) -> datetime:
    c = manifest["config"]
    return datetime.fromisoformat(c["end"]) - timedelta(days=c["n_days"])


class IntelReplay:
    """api.intel.IntelAdapter.lookup without HTTP: the mock routers' own functions at a chosen moment."""

    def __init__(self, store: Store):
        self.store = store
        # The routers only read request.app.state.store and .mulehunter_cache.
        self.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store, mulehunter_cache={})))

    @staticmethod
    def _call(fn, *args, **kwargs) -> tuple[str, dict | None]:
        try:
            return "ok", fn(*args, **kwargs)
        except HTTPException as e:
            return STATUS.get(e.status_code, "error"), None

    def lookup(self, payee_upi: str, caller_number: str | None, at: datetime) -> IntelResult:
        self.store.now = at  # every mock reads its clock from the store
        upi = payee_upi.strip().lower()
        res, req = IntelResult(), self.request
        calls = {"npci": self._call(npci.resolve, upi, req)}
        status, body = calls["npci"]
        calls["bank"] = (self._call(bank.payee_risk, body["bank_code"], body["upi_id"], req) if status == "ok"
                         else ("skipped", None))
        calls["payee_registry"] = self._call(i4c_registry.registry, req, upi_id=upi)
        m = NUMBER_UPI.match(upi)
        if m:
            calls["payee_fri"] = self._call(dot_fri.fri, "+91" + m.group(1), req)
        if caller_number:
            calls["caller_registry"] = self._call(i4c_registry.registry, req, mobile=caller_number)
            calls["caller_fri"] = self._call(dot_fri.fri, caller_number, req)
        for name, (status, body) in calls.items():
            res.status[name] = status
            setattr(res, name, body)
        return res


class EngineReplay:
    """The live engine's decide path up to the scorer, plus POST /v1/episode."""

    def __init__(self, store: Store, data_dir: str | Path):
        self.store = store
        self.tracker = StageTracker()
        self.intel = IntelReplay(store)
        self.graph = ReplayGraphStore()
        self._data_dir = data_dir
        self._episodes: EpisodeService | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def episode_event(self, body: dict) -> None:
        """The engine's own EpisodeService. Its lookups go over in-process HTTP to the same mock
        world; episode events are rare, so speed does not matter here."""
        if self._episodes is None:
            mock = create_mock(self._data_dir)
            mock.state.store = self.store  # one simulated world and clock for both paths
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock), base_url="http://mock")
            self._episodes = EpisodeService(IntelAdapter(client, cache_ttl_s=0), EvidenceStore(), graph=self.graph)
            self._loop = asyncio.new_event_loop()
        ev = EpisodeEvent(**body)
        self.store.now = ev.ts
        self._loop.run_until_complete(self._episodes.handle(ev, ev.ts))

    def step(self, req: DecideRequest, base: UserBaseline, trusted: bool = False, with_features: bool = True):
        upi = req.payee_upi_id.strip().lower()
        # Known payee: the bank's history, or a payment here the phone reported completed (c2-0.6).
        paid_here = self.graph.paid_before(req.user_id, upi, req.ts)
        state = self.tracker.update(req, new_payee=upi not in base.known_payees and not paid_here and not trusted)
        # Every checked payment is a graph edge and a payment record; old ones expire (api/main.py).
        self.graph.add_payment(req.user_id, upi, req.amount_inr, req.ts, request_id=req.request_id)
        self.graph.expire(req.ts)
        if not with_features:
            return state, None, None
        call = req.context.call if req.context else None
        res = self.intel.lookup(upi, call.caller_number if call else None, req.ts)
        ring = self.graph.account_signals(upi, req.ts)
        ring["approved_suspect"] = False  # two analysts approve discovered suspects live; none in a replay
        return state, res, build_features(req, state, res, base, trusted, ring=ring, paid_here=paid_here)

    def outcome(self, req: DecideRequest, status: str = "completed", ts: datetime | None = None) -> None:
        """POST /v1/payment/outcome (c2-0.6): every payment in the world was completed."""
        self.graph.record_outcome(req.request_id, req.user_id, req.payee_upi_id, req.amount_inr, status, ts or req.ts)


def phone_stream(tables: dict[str, pd.DataFrame], view: PhoneView, feed: EpisodeFeed,
                 seed: int = 0, device_model: dict | None = None) -> Iterator[tuple]:
    """What every RUKO phone sends, in time order: ("episode", event body) and ("pay", request, info)
    for every payment a RUKO user makes. Credits reach the phones' watchers as they happen; a
    phone's episode events go out before its owner's next payment. With `device_model`, each
    request carries the phone's verdict (c2-0.8) computed from its private features, as the app does."""
    upi_user = {upi: uid for uid, upi in view.user_upi.items()}
    playbook = dict(zip(tables["journeys"].journey_id, tables["journeys"].playbook))
    led = tables["ledger"].sort_values("ts", kind="stable")
    journey_paid: dict[str, int] = {}  # scam payments so far per journey
    cols = zip(led.tx_id.tolist(), led.ts.tolist(), epoch_seconds(led.ts), led.from_upi.tolist(),
               led.to_upi.tolist(), led.amount.tolist(), led.entry_method.tolist(), led.flow.tolist(),
               led.journey_id.tolist(), led.label_is_scam.tolist(), led.label_stage.tolist())
    for tx, ts, t, frm, to, amount, entry, flow, jid, is_scam, stage in cols:
        ts = ts.to_pydatetime()
        if to in upi_user:
            feed.credit(upi_user[to], ts, amount, frm, flow)
        uid = upi_user.get(frm)
        if uid is None:
            continue
        for body in feed.due(ts):
            yield "episode", body
        is_scam = bool(is_scam)
        jid = jid if isinstance(jid, str) else None
        pb = playbook.get(jid) if jid else None
        pay_index = None
        if is_scam and jid:
            pay_index = journey_paid.get(jid, 0)  # 0 = the journey's first scam payment
            journey_paid[jid] = pay_index + 1
        ctx, raw = view.context_at(uid, t, feed.summary(uid, t, to), payee=to, entry=entry)
        tr = view.traits[uid]
        dev = device_features(raw, view.own_history(uid, t), float(amount), entry, to, t, tr.any_shield,
                              feed.rules["escalation_factor"])
        verdict = None
        if device_model is not None and tr.any_shield:
            verdict = device_verdict(device_model, dev)
            ctx = (ctx or Context()).model_copy(update={"device_verdict": DeviceVerdict(**verdict)})
        says = view.payer_says(keyed_rng(seed, "payment", tx), entry, amount, is_scam, pb, to)
        req = DecideRequest(request_id=tx, user_id=uid, payee_upi_id=to, amount_inr=float(amount),
                            entry_method=entry, payer_says=says, ts=ts, context=ctx)
        yield "pay", req, {"tx": tx, "t": t, "flow": flow, "journey_id": jid, "playbook": pb, "is_scam": is_scam,
                           "stage": stage if isinstance(stage, str) else None, "pay_index": pay_index, "raw": raw,
                           "dev": dev, "verdict": verdict}
        feed.debit(uid, ts, amount, to)


def replay(data_dir: str | Path, sample_normal: float = 0.25, warmup_days: float = 14, seed: int = 0,
           assumptions: dict | None = None, log_every: int = 0,
           device_model: str | Path | None = DEVICE_MODEL) -> pd.DataFrame:
    """One row per kept payment. Scam payments are always kept; normal ones with probability
    `sample_normal` and weight 1/sample_normal. The first `warmup_days` build history only."""
    tables, manifest = load_world(data_dir)
    a = assumptions or load_assumptions()
    view = PhoneView(tables, a, seed)
    start = world_start(manifest)
    feed = EpisodeFeed(view.traits, start)
    engine = EngineReplay(Store.load(data_dir), data_dir)
    baselines = Baselines.from_data(data_dir)
    keep_from = (start + timedelta(days=warmup_days)).timestamp()

    u = tables["users"]
    audit = {uid: (age, lang, region) for uid, age, lang, region in zip(u.user_id, u.age_band, u.language, u.region)}
    acc = tables["accounts"].set_index("upi_id")
    rows, n_pay, n_events, t0 = [], 0, 0, time.perf_counter()
    dm = device_score.load(device_model) if device_model is not None and Path(device_model).exists() else None
    for item in phone_stream(tables, view, feed, seed, dm):
        if item[0] == "episode":
            engine.episode_event(item[1])
            n_events += 1
            continue
        _, req, info = item
        n_pay += 1
        keep = info["t"] >= keep_from and (info["is_scam"] or keyed_rng(seed, "keep", info["tx"]).random() < sample_normal)
        base = baselines.get(req.user_id, at=req.ts)  # only what the bank knew before this payment
        state, res, feats = engine.step(req, base, with_features=keep)
        engine.outcome(req)  # the phone reports the payment completed after the PIN
        if keep:
            uid, to, raw = req.user_id, req.payee_upi_id, info["raw"]
            tr = view.traits[uid]
            age, lang, region = audit[uid]
            payee = acc.loc[to] if to in acc.index else None
            rows.append({
                "tx_id": info["tx"], "ts": req.ts, "user_id": uid, "payee_upi": to, "amount": req.amount_inr,
                "entry_method": req.entry_method, "flow": info["flow"], "journey_id": info["journey_id"],
                "playbook": info["playbook"], "label_is_scam": info["is_scam"], "label_stage": info["stage"],
                "journey_pay_index": info["pay_index"], "weight": 1.0 if info["is_scam"] else 1.0 / sample_normal,
                "payer_says": req.payer_says, "has_context": req.context is not None,
                # What api/main.py hands the policy (S3 anywhere in the journey, or remote control now).
                "control_seen": any(s == "S3" for s, _ in state.stage_path) or bool(feats["remote_control"]),
                "has_episode": raw["episode"] is not None, "has_session": raw["session"] is not None,
                "consent_call": tr.call_signals, "consent_app": tr.app_checks, "consent_sms": tr.sms,
                "consent_share": tr.share_accounts, "consent_session": tr.session_signals,
                "android_15": tr.android_15, "intel_status": json.dumps(res.status, sort_keys=True),
                # Audit only: never model inputs.
                "audit_age_band": age, "audit_language": lang, "audit_region": region,
                "payee_is_mule": None if payee is None else bool(payee["is_mule"]),
                "payee_mule_kind": None if payee is None else payee["mule_kind"],
                "payee_merchant_kind": None if payee is None else payee["merchant_kind"],
                "dv_level": (info["verdict"] or {}).get("level"), "dv_class": (info["verdict"] or {}).get("fraud_class"),
                **feats,
                **info["dev"],
            })
        if log_every and n_pay % log_every == 0:
            print(f"  {n_pay:,} payments, {n_events:,} episode events, {len(rows):,} rows, "
                  f"{time.perf_counter() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, type=Path, help="a world folder written by sim.generate")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--sample-normal", type=float, default=0.25)
    p.add_argument("--warmup-days", type=float, default=14)
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    t0 = time.perf_counter()
    df = replay(a.data, a.sample_normal, a.warmup_days, a.seed, log_every=50_000)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(a.out, index=False)
    scam = df[df.label_is_scam]
    print(f"{len(df):,} rows ({len(scam):,} scam payments, {scam.journey_id.nunique()} journeys) "
          f"in {time.perf_counter() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
