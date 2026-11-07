"""Apply exact public-text evidence; never read or modify learner databases."""
from copy import deepcopy
import json
from pathlib import Path

from kanamemory.sentence_annotations import KANA, _validated, validated_accent_phrases

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "data/enrichment/sentence_annotation_review_v1.json"
STATUSES = {"verified_reading", "verified_scalar", "verified_phrase_sequence", "pending"}


def load_reviews(path=REVIEW_PATH):
    """The ledger describes individual decisions, never global name/number rules."""
    data = json.loads(Path(path).read_text("utf-8"))
    if (not isinstance(data, dict) or data.get("format") != "kanamemory-sentence-annotation-review"
            or type(data.get("version")) is not int or data["version"] != 1
            or not isinstance(data.get("records"), list)):
        raise ValueError("Unsupported annotation review ledger")
    grouped, seen = {}, set()
    for entry in data.get("records", []):
        if not isinstance(entry, dict):
            raise ValueError("Invalid annotation review entry")
        text, start, end = entry.get("text"), entry.get("start"), entry.get("end")
        if (not isinstance(text, str) or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text) or entry.get("surface") != text[start:end]
                or entry.get("status") not in STATUSES
                or not isinstance(entry.get("review_id"), str) or not entry["review_id"]
                or not isinstance(entry.get("reason"), str) or not entry["reason"]
                or not isinstance(entry.get("review_reason"), str) or not entry["review_reason"]
                or "expected_reading" not in entry
                or entry["expected_reading"] is not None and (
                    not isinstance(entry["expected_reading"], str) or not KANA.fullmatch(entry["expected_reading"]))
                or not isinstance(entry.get("source_url"), str) or not entry["source_url"].startswith("https://")):
            raise ValueError("Invalid anchored annotation review")
        if entry["status"] != "pending" and (not isinstance(entry.get("reading"), str)
                                              or not KANA.fullmatch(entry["reading"])):
            raise ValueError("Verified review requires a nonempty kana reading")
        key = (text, start, end)
        if key in seen:
            raise ValueError("Duplicate annotation review")
        seen.add(key)
        if entry["status"] in {"pending", "verified_reading"} and (
                entry.get("accent_number") is not None or entry.get("accent_phrases")):
            raise ValueError("Pending or reading-only review cannot supply an accent")
        grouped.setdefault(text, []).append(deepcopy(entry))
    return grouped


def apply_reviews(text, plan, reviews=()):
    """Fail on changed anchors; pending reviews never alter phonetic values."""
    result = deepcopy(plan)
    for entry in reviews:
        matches = [s for s in result["segments"] if (s["start"], s["end"], s["surface"]) ==
                   (entry["start"], entry["end"], entry["surface"])]
        if (entry["text"] != text or len(matches) != 1
                or matches[0].get("reading") != entry.get("expected_reading")
                or matches[0].get("accent_number") is not None
                or matches[0].get("accent_phrases")):
            raise ValueError("Annotation review no longer matches unresolved source")
        part, status = matches[0], entry["status"]
        if status not in STATUSES or "expected_reading" not in entry:
            raise ValueError("Invalid annotation review decision")
        if status != "pending":
            if not isinstance(entry.get("reading"), str) or not KANA.fullmatch(entry["reading"]):
                raise ValueError("Verified review requires a nonempty kana reading")
            part.update(reading=entry["reading"], reading_source_id="curated-reading-reference",
                        reading_derivation="exact_context_reference", accent_reading=entry["reading"])
        if status in {"verified_scalar", "verified_phrase_sequence"}:
            part.update(accent_number=entry.get("accent_number"), accent_source_id="curated-accent-reference",
                        accent_scope="phrase_sequence" if status == "verified_phrase_sequence" else "word",
                        accent_phrases=deepcopy(entry.get("accent_phrases", [])),
                        accent_alternatives=[], accent_surface=part["surface"],
                        derivation="exact_context_reference")
            if status == "verified_phrase_sequence" and not validated_accent_phrases(part):
                raise ValueError("Invalid reviewed accent phrase sequence")
            if status == "verified_scalar" and type(part["accent_number"]) is not int:
                raise ValueError("Verified scalar requires a real numeric accent")
        prior_urls = [part.get("source_url", ""), *part.get("source_urls", [])]
        all_urls = [*entry.get("source_urls", []), *prior_urls]
        source_urls = list(dict.fromkeys(url for url in all_urls
            if isinstance(url, str) and url.startswith("https://") and url != entry["source_url"]))
        part.update(review_id=entry["review_id"], review_status=status,
                    review_note=entry["reason"], review_reason=entry["review_reason"],
                    source_url=entry["source_url"], source_urls=source_urls)
    checked = _validated(text, result)
    if checked is None:
        raise ValueError("Invalid follow-up annotations")
    return checked
