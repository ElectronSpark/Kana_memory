"""Read-only desktop environment checks; no GUI, SDK installation or product code."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
import json
import platform
import sqlite3
import sys
import unicodedata


def main() -> None:
    packages = {
        name: version(name)
        for name in ("briefcase", "toga", "toga-core", "toga-winforms", "fsrs", "jsonschema", "pytest")
    }
    for module in ("briefcase", "toga", "fsrs", "jsonschema", "pytest"):
        import_module(module)
    from fsrs import Card, Rating, Scheduler
    from jsonschema import validate

    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    card, review = Scheduler(enable_fuzzing=False).review_card(
        Card(), Rating.Good, review_datetime=now
    )
    assert card.due > now
    assert json.loads(review.to_json())["rating"] == int(Rating.Good)
    validate({"kana": "が"}, {"type": "object", "required": ["kana"]})
    assert unicodedata.normalize("NFC", "か\u3099") == "が"
    with sqlite3.connect(":memory:") as connection:
        text = "日本語 あいうえお アイウエオ 上海 苏州 gwok3"
        assert connection.execute("SELECT ?", (text,)).fetchone()[0] == text
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        fts5_available = True
    report = {
        "python": platform.python_version(),
        "executable": sys.executable,
        "base_prefix": sys.base_prefix,
        "platform": platform.platform(),
        "packages": packages,
        "checks": {
            "imports_without_gui": "passed",
            "sqlite_unicode_round_trip": "passed",
            "desktop_sqlite_fts5": fts5_available,
            "unicode_combining_dakuten": "passed",
            "jsonschema_validation": "passed",
            "fsrs_utc_review_and_serialization": "passed",
        },
        "sqlite_version": sqlite3.sqlite_version,
        "android_runtime_tested": False,
        "note": "Desktop import checks do not demonstrate Android runtime, IME, audio or handwriting behavior.",
    }
    target = Path(__file__).with_name("desktop_checks.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
