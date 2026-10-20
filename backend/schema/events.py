"""What the phone sends at the moment of Pay, and what RUKO answers.

Only derived flags leave the phone (no SMS text, audio, contacts). Any missing field means
"unknown", never "safe". `context: null` means the user opted out of sharing phone signals.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Bump on any shape change, and re-export contracts/ (python -m schema.export).
CONTRACT_VERSION = "c2-0.1"

Tier =Literal["T0", "T1", "T2", "T3", "T4"]
ReasonCode = Literal[
    "R1_live_unknown_call", "R2_remote_control", "R3_mule_like_payee", "R4_unusual_amount",
    "R5_lure_message", "R6_new_payee_typed", "R7_tranche_pattern", "R8_intel_hit",
    "R9_story_mismatch", "ruko_unavailable",
]
LureTag = Literal["arrest_threat", "kyc", "task_job", "trading_tip", "refund", "normal"]


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


class DeviceSignal(_M):
    remote_access_active: Optional[bool] = None
    remote_app_installed_min_ago: Optional[float] = Field(None, ge=0)
    untrusted_accessibility: Optional[bool] = None
    sideloaded_last_24h: Optional[bool] = None


class LureSignal(_M):
    tag: LureTag
    conf: float = Field(ge=0, le=1)
    min_ago: float = Field(ge=0)


class Context(_M):
    call: Optional[CallSignal] = None
    device: Optional[DeviceSignal] = None
    lures: list[LureSignal] = Field(default_factory=list)


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
    # Never p_scam: the user sees reasons in words, never a score.
