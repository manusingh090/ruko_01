"""The published C2 contract (contracts/) must match schema/events.py exactly."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema.events import CONTRACT_VERSION, DecideRequest, DecideResponse
from schema.export import OUT, render

EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "examples"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_committed_schema_is_current():
    # Fails after any change to events.py until `python -m schema.export` is re-run and committed.
    assert OUT.read_text(encoding="utf-8") == render()


@pytest.mark.parametrize("name", sorted(p.name for p in EXAMPLES.glob("decide_request_*.json")))
def test_request_examples_validate(name):
    DecideRequest.model_validate(load(name))


@pytest.mark.parametrize("name", sorted(p.name for p in EXAMPLES.glob("decide_response_*.json")))
def test_response_examples_validate(name):
    r = DecideResponse.model_validate(load(name))
    assert r.contract_version == CONTRACT_VERSION
    assert "p_scam" not in load(name)  # the user never sees a score


def test_unknown_fields_are_rejected():
    body = {**load("decide_request_minimal.json"), "payee_vpa_hash": "abc"}
    with pytest.raises(ValidationError):
        DecideRequest.model_validate(body)


def test_missing_context_means_unknown_not_safe():
    req = DecideRequest.model_validate(load("decide_request_meena.json"))
    assert req.context.device.untrusted_accessibility is None
    assert req.context.device.sideloaded_last_24h is None
