"""What the phone sends at the moment of Pay, and what RUKO answers.

Only derived flags leave the phone (no SMS text, audio, contacts). Any missing field means
"unknown", never "safe". `context: null` means the user opted out of sharing phone signals.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Bump on any shape change, and re-export contracts/ (python -m schema.export).
CONTRACT_VERSION = "c2-0.8"

Tier =Literal["T0", "T1", "T2", "T3", "T4"]
ReasonCode = Literal[
    "R1_live_unknown_call", "R2_remote_control", "R3_mule_like_payee", "R4_unusual_amount",
    "R5_lure_message", "R6_new_payee_typed", "R7_tranche_pattern", "R8_intel_hit",
    "R9_story_mismatch", "R10_bait_then_pay", "R11_ring_link", "R12_coached_session",
    "ruko_unavailable",
]
LureTag = Literal["arrest_threat", "kyc", "task_job", "trading_tip", "refund", "normal"]
# The known scam workflows (playbooks); the engine's and the phone's fraud class use the same names.
FraudType = Literal["digital_arrest", "fake_customer_care", "fake_kyc_apk", "trading_group", "task_job", "refund_qr"]
ThreatLevel = Literal["none", "low", "medium", "high", "critical"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallSignal(_M):
    active: Optional[bool] = None
    kind: Optional[Literal["sim", "voip"]] = None
    direction: Optional[Literal["incoming", "outgoing"]] = None
    caller_number: Optional[str] = None          # used only for FRI / registry lookups
    caller_in_contacts: Optional[bool] = None
    started_min_ago: Optional[float] = Field(None, ge=0)
    ended_min_ago: Optional[float] = Field(None, ge=0)  # for a call that just finished
    speaker_on: Optional[bool] = None            # c2-0.5: call audio on the loudspeaker at Pay


class DeviceSignal(_M):
    remote_access_active: Optional[bool] = None
    remote_app_installed_min_ago: Optional[float] = Field(None, ge=0)
    untrusted_accessibility: Optional[bool] = None
    sideloaded_last_24h: Optional[bool] = None


class LureSignal(_M):
    tag: LureTag
    conf: float = Field(ge=0, le=1)
    min_ago: float = Field(ge=0)


class EpisodeSignal(_M):
    """Task-scam episode kept on the phone across payees for up to 30 days (c2-0.3).

    Built by the phone's background watcher from bank SMS alerts and this app's payments, with
    the rules in contracts/episode_rules.json. Counts only; bait fields are null when SMS can't be read.
    """
    state: Literal["none", "watching", "active", "dismissed"]
    age_days: Optional[float] = Field(None, ge=0)          # since first bait credit
    bait_credits: Optional[int] = Field(None, ge=0)        # null when SMS can't be read
    bait_inr: Optional[float] = Field(None, ge=0)
    outs: Optional[int] = Field(None, ge=0)                # after first bait, any app, excl. this payment
    outs_inr: Optional[float] = Field(None, ge=0)
    new_payees: Optional[int] = Field(None, ge=0)
    escalation_steps: Optional[int] = Field(None, ge=0)
    last_out_inr: Optional[float] = Field(None, gt=0)
    out_to_in_ratio: Optional[float] = Field(None, ge=0)
    payee_seen_in_episode: Optional[bool] = None           # this payee already paid in this episode
    rules_version: Optional[str] = None                    # e.g. "episode-0.1"


class SessionSignal(_M):
    """How the user behaved on the Pay screen of THIS payment (c2-0.5).

    Measured inside the app, from opening Pay to tapping Pay. Only counts and rounded durations:
    never the keys typed, never which other app was open. Field meanings: contracts/README.md.
    """
    amount_digits: Optional[int] = Field(None, ge=0)              # digit key presses in the amount box
    amount_median_gap_s: Optional[float] = Field(None, ge=0)      # typical pause between those digits
    amount_max_gap_s: Optional[float] = Field(None, ge=0)         # longest pause between them
    amount_gap_vs_usual: Optional[float] = Field(None, ge=0)      # median gap / this user's usual; null until 3 past payments
    amount_edits: Optional[int] = Field(None, ge=0)               # deletions in the amount box
    payee_pasted: Optional[bool] = None                           # the UPI ID arrived in one paste
    pasted_within_s_of_return: Optional[float] = Field(None, ge=0)  # paste this soon after coming back to the app
    app_switches: Optional[int] = Field(None, ge=0)               # times the app went to the background
    away_s: Optional[float] = Field(None, ge=0)                   # total time away
    pay_screen_s: Optional[float] = Field(None, ge=0)             # opening Pay -> tapping Pay
    abandoned_flows_30m: Optional[int] = Field(None, ge=0)        # payments started and left unpaid, last 30 min


class DeviceVerdict(_M):
    """The phone's own model on what only the phone can see (c2-0.8; ml/model/device, DeviceModel.kt).

    Calls, apps, SMS (through the lure model), the episode, the Pay screen and the payment history
    are read on the phone and stay there; only this verdict leaves it. Family scores are 0..1,
    rounded to 2 decimals, and each counts only in its own family on the server (rule 5).
    """
    model_version: str                                   # e.g. "device-0.1"
    fraud_class: Optional[FraudType] = None              # the scam the phone's signals point to, if any
    level: Literal["low", "medium", "high"]              # the phone's own threat level
    c: Optional[float] = Field(None, ge=0, le=1)         # calls and remote control; null = not measured
    l: Optional[float] = Field(None, ge=0, le=1)         # lure messages and the task-scam episode
    b: Optional[float] = Field(None, ge=0, le=1)         # own spending and the Pay screen
    reasons: list[ReasonCode] = Field(default_factory=list, max_length=3)


class Context(_M):
    call: Optional[CallSignal] = None
    device: Optional[DeviceSignal] = None
    lures: list[LureSignal] = Field(default_factory=list)
    episode: Optional[EpisodeSignal] = None  # c2-0.3 (the c2-0.2 `money` group was removed in c2-0.4)
    session: Optional[SessionSignal] = None  # c2-0.5
    # c2-0.7: the payee's UPI ID, or the mobile number it was paid by, is a saved contact on the
    # phone. Only this yes/no leaves the phone, never names or numbers. null = contacts not readable.
    payee_in_contacts: Optional[bool] = None
    device_verdict: Optional[DeviceVerdict] = None  # c2-0.8: null when the phone's model did not run


class DecideRequest(_M):
    request_id: str
    user_id: str
    payee_upi_id: str
    amount_inr: float = Field(gt=0)
    entry_method: Literal["typed", "qr", "contact", "collect"]
    # Answer to "Who are you paying?" (asked for first-time payees over a threshold).
    payer_says: Optional[Literal["government", "company", "family", "shop", "investment", "other"]] = None
    ts: AwareDatetime
    context: Optional[Context] = None


class Threat(_M):
    """The server's threat analysis of this payment (c2-0.8): every family, the phone's verdict,
    payee intel (bank features, MuleHunter, FRI, I4C registry) and the payment graph."""
    score: int = Field(ge=0, le=100)                     # 0..100 (100 x the scorer's p_scam, rounded)
    level: ThreatLevel                                    # T0 none, T1 low, T2 medium, T3 high, T4 critical
    fraud_type: Optional[FraudType] = None               # the known scam it matches, if any
    families: list[Literal["C", "L", "P", "B"]] = Field(default_factory=list)  # families that agree


class DecideResponse(_M):
    request_id: str
    tier: Tier
    reasons: list[ReasonCode]
    message_key: Optional[str] = None
    pause_minutes: int = 0
    playbook: Optional[str] = None
    stage: Optional[str] = None
    evidence_id: Optional[str] = None
    fail_open: bool = False
    contract_version: str = CONTRACT_VERSION
    # c2-0.8: the threat analysis, shown to the user (a team decision that replaced "never a score").
    # null when RUKO failed open.
    threat: Optional[Threat] = None


# ---------- Episode events: phone -> server when the episode changes state (c2-0.4) ----------

EpisodeTrigger = Literal["watching", "active", "out_payment", "bait", "dismissed", "closed"]


class EpisodeAccount(_M):
    upi_id: str                                   # plain: needed for intel lookups and the graph
    role: Literal["bait_sender", "payee"]
    n: int = Field(ge=1)                          # times seen in this episode
    inr: float = Field(ge=0)                      # total with this account in this episode
    first_seen_min_ago: float = Field(ge=0)
    via: Optional[Literal["this_app", "other_app"]] = None   # payees only; other_app = debit SMS
    step: Optional[int] = Field(None, ge=1)       # payees only: position in the escalation run


class EpisodeEvent(_M):
    """POST /v1/episode. Sent only on state changes, and only with "Share suspicious accounts" on."""
    event_id: str                                 # UUID; the server ignores repeats (safe to retry)
    episode_id: str                               # UUID, stable for the whole episode
    user_id: str
    ts: AwareDatetime
    trigger: EpisodeTrigger
    rules_version: str                            # "episode-0.2"
    summary: EpisodeSignal                        # same object as context.episode
    accounts: list[EpisodeAccount] = Field(default_factory=list, max_length=10)


class EpisodeEventResponse(_M):
    event_id: str
    episode_risk: Literal["unknown", "low", "elevated", "high"]   # words, never a score
    reasons: list[ReasonCode]
    message_key: Optional[str] = None             # e.g. "episode_task_scam_confirmed"
    case_id: Optional[str] = None                 # set when an analyst case was opened
    contract_version: str = CONTRACT_VERSION


# ---------- Payment outcome: phone -> server after the PIN or Cancel (c2-0.6) ----------

PaymentStatus = Literal["completed", "cancelled"]


class PaymentOutcome(_M):
    """POST /v1/payment/outcome. What happened to a payment after its check. The payment id is the
    /v1/decide `request_id`. Payee and amount are repeated so the payment is recorded even when the
    check never reached the server (timeout, fail open). Safe to retry."""
    request_id: str                               # the payment id: same UUID as its /v1/decide request
    user_id: str
    payee_upi_id: str                             # plain, as in /v1/decide
    amount_inr: float = Field(gt=0)
    status: PaymentStatus                         # completed = PIN entered; cancelled = user left without paying
    ts: AwareDatetime                             # when it was completed or cancelled


class PaymentOutcomeResponse(_M):
    request_id: str
    status: PaymentStatus                         # the status the server holds (the first final one wins)
    recorded: bool                                # False: a repeat, a conflicting status, or not accepted
    contract_version: str = CONTRACT_VERSION
