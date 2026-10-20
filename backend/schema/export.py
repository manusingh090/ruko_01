"""Write the C2 decide contract as JSON Schema for the app and anyone else who mirrors it.

    PYTHONPATH=backend:ml:data python -m schema.export        # writes contracts/c2_decide.schema.json

backend/tests/test_schema.py fails when the committed file is out of date with events.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from schema.events import CONTRACT_VERSION, DecideRequest, DecideResponse

OUT = Path(__file__).resolve().parents[2] / "contracts" / "c2_decide.schema.json"


def build_schema() -> dict:
    return {
        "title": "RUKO C2: POST /v1/decide",
        "contract_version": CONTRACT_VERSION,
        "request": DecideRequest.model_json_schema(),
        "response": DecideResponse.model_json_schema(),
    }


def render() -> str:
    return json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
