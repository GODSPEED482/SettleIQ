"""The packaged result schema is the one the model is asked to fill (task 5.8)."""

from pathlib import Path

from settleiq_agent.contract import schema_json

PACKAGED = Path(__file__).parents[1] / "settleiq_agent" / "response_schema.json"


def test_packaged_schema_matches_the_model():
    assert PACKAGED.read_text(encoding="utf-8") == schema_json()
