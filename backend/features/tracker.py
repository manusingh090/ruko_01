"""Stage tracker: follows one user's possible scam journey for 72 hours.

S0 nothing · S1 contacted · S2 hooked · S3 under control · S4 paying · S5 paying again.
In-memory for now; the interface is small so a Redis version can replace it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from features.episode import effective_episode
from schema.events import DecideRequest

WINDOW = timedelta(hours=72)
CALL_MEMORY = timedelta(minutes=30)   # a call that ended this recently still counts as "on call"
CONTROL_AFTER_CONTACT = timedelta(hours=2)
STAGES = ("S0", "S1", "S2", "S3", "S4", "S5")


@dataclass
class Attempt:
    ts: datetime
    payee: str
    amount: float
    new_payee: bool


@dataclass
class Session:
    contacts: list[datetime] = field(default_factory=list)
    control_ts: datetime | None = None
    lure_ts: datetime | None = None
    episode_hooked: bool = False   # the phone's task-scam episode is ACTIVE
    attempts: list[Attempt] = field(default_factory=list)
    stage_path: list[tuple[str, datetime]] = field(default_factory=list)


@dataclass
class TrackerState:
    """What the rest of the engine needs from the tracker for one decision."""
    stage: str
    stage_path: list[tuple[str, datetime]]
    on_call: bool | None
    attempts_72h: int          # earlier attempts to new payees in the window, not counting this one
    new_payee_sum_72h: float   # including this one
    payee_in_run: bool         # this payee is new, or one of those earlier new payees
    minutes_since_first_contact: float | None


class StageTracker:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def reset(self) -> None:
        self.sessions.clear()

    def update(self, req: DecideRequest, new_payee: bool) -> TrackerState:
        now = req.ts
        s = self.sessions.setdefault(req.user_id, Session())
        self._expire(s, now)
        ctx = req.context
        call = ctx.call if ctx else None
        dev = ctx.device if ctx else None

        on_call = None
        if call is not None:
            recent_end = call.ended_min_ago is not None and call.ended_min_ago <= CALL_MEMORY.total_seconds() / 60
            on_call = bool(call.active) or recent_end
            # A WhatsApp-type (VoIP) call never shows the number, so its contact status is null:
            # unknown is not safe (rule 1), so it counts as a possible contact like a stranger's call.
            unknown_caller = call.caller_in_contacts is False or (
                call.kind == "voip" and call.caller_in_contacts is None)
            if (call.active or recent_end) and unknown_caller:
                started = now - timedelta(minutes=call.started_min_ago or 0)
                if not any(abs((c - started).total_seconds()) < 60 for c in s.contacts):
                    s.contacts.append(started)
        if ctx:
            for lure in ctx.lures:
                if lure.tag != "normal" and lure.conf >= 0.6:
                    ts = now - timedelta(minutes=lure.min_ago)
                    s.lure_ts = max(s.lure_ts or ts, ts)
        ep = effective_episode(ctx)
        if ep and ep["state"] in ("watching", "active"):
            # The first bait credit is first contact (S1); an active episode means hooked (S2).
            started = now - timedelta(days=ep["age_days"] or 0)
            s.lure_ts = min(s.lure_ts or started, started)
            s.episode_hooked = s.episode_hooked or ep["state"] == "active"
        if dev is not None:
            control_signal = dev.remote_access_active or dev.untrusted_accessibility or (
                dev.remote_app_installed_min_ago is not None and dev.remote_app_installed_min_ago <= 120)
            if control_signal:
                ago = dev.remote_app_installed_min_ago or 0
                s.control_ts = s.control_ts or now - timedelta(minutes=ago)

        earlier_new = [a for a in s.attempts if a.new_payee]
        if new_payee:
            s.attempts.append(Attempt(now, req.payee_upi_id, req.amount_inr, True))
        stage = self._stage(s, call, new_payee, earlier_new)
        if not s.stage_path or s.stage_path[-1][0] != stage:
            s.stage_path.append((stage, now))
        first = min([*s.contacts, *([s.lure_ts] if s.lure_ts else [])], default=None)
        return TrackerState(
            stage=stage, stage_path=list(s.stage_path), on_call=on_call,
            attempts_72h=len(earlier_new),
            new_payee_sum_72h=sum(a.amount for a in s.attempts if a.new_payee),
            payee_in_run=new_payee or any(a.payee == req.payee_upi_id for a in earlier_new),
            minutes_since_first_contact=(now - first).total_seconds() / 60 if first else None,
        )

    @staticmethod
    def _stage(s: Session, call, new_payee: bool, earlier_new: list[Attempt]) -> str:
        contacted = bool(s.contacts) or s.lure_ts is not None
        if not contacted and s.control_ts is None:
            return "S0"
        stage = "S1"
        long_call = call is not None and (call.started_min_ago or 0) >= 15 and (
            call.caller_in_contacts is False or (call.kind == "voip" and call.caller_in_contacts is None))
        if len(s.contacts) >= 2 or (s.contacts and s.lure_ts) or long_call or s.episode_hooked:
            stage = "S2"
        first = min([*s.contacts, *([s.lure_ts] if s.lure_ts else [])], default=None)
        if s.control_ts and (first is None or s.control_ts - first <= CONTROL_AFTER_CONTACT):
            stage = "S3"
        if new_payee:
            stage = "S5" if earlier_new else "S4"
        return stage

    @staticmethod
    def _expire(s: Session, now: datetime) -> None:
        cutoff = now - WINDOW
        s.contacts = [c for c in s.contacts if c > cutoff]
        s.attempts = [a for a in s.attempts if a.ts > cutoff]
        if s.lure_ts and s.lure_ts <= cutoff:
            s.lure_ts = None
        if s.control_ts and s.control_ts <= cutoff:
            s.control_ts = None
        s.stage_path = [p for p in s.stage_path if p[1] > cutoff]
