"""Shared material workflow fixtures and assertions; no collected scenarios."""
from support.ai_material_fixtures import examples
from support.ai_material_fixtures import new_word


def request(identity="material-smoke"):
    return {"request_id": identity, "mode": "examples", "count": 2, "source_text": "", "topic": "",
            "target": None, "scope": None}


def response():
    return {"items": [new_word()], "example_pack": examples(count=2), "segments": []}
