"""Collect immutable public Japanese text for build-time sentence annotation.

This tool never opens learner databases, rewrites content IDs, or reads private
AI input. Candidate text is not permission to display hints in a quiz: runtime
question support must still independently enforce the anti-answer-leak gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "app/src/kanamemory/resources"
sys.path.insert(0, str(ROOT / "app/src"))


def public_prompt_parts(question):
    """Only Japanese text actually printed in a question, never answer/audio."""
    from kanamemory.question_support import _plain_prompt_parts
    if (question.get("prompt_visible") is False or question.get("audio_only") is True
            or question.get("requires_audio") is True
            or question.get("has_hidden_transcript") is True):
        return ()
    prompt = question.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return ()
    return tuple(text for text, role in _plain_prompt_parts(question, prompt)
                 if role == "study" and isinstance(text, str) and text.strip())


def lesson_text_records(items):
    """Yield explicit example fields and each local public prompt variant."""
    from kanamemory.learning_questions import build_learning_question, question_kinds, practice_templates
    for item in items:
        identity = item["id"]
        instruction = item.get("instruction", {})
        example = instruction.get("example")
        if isinstance(example, str) and example.strip():
            yield example, {"category": "instruction_example", "id": identity}
        for origin, examples in (("item_examples", item.get("examples", [])),
                                 ("instruction_examples", instruction.get("examples", [])),
                                 ("instruction_audio_examples", instruction.get("audio_examples", []))):
            for index, sample in enumerate(examples):
                if not isinstance(sample, dict) or sample.get("text_role", "study") != "study":
                    continue
                text = sample.get("text")
                if isinstance(text, str) and text.strip():
                    yield text, {"category": origin, "id": identity, "index": index}
        if item.get("instruction_status") == "instruction_missing":
            continue
        templates = practice_templates(item)
        for kind in question_kinds(item):
            # The original grammar builder has two authored variants; expanded
            # curricula enumerate their own templates. Repeated prompts dedupe.
            count = len(templates[kind]) if templates is not None else 2
            for variant in range(count):
                question = build_learning_question(item, kind, variant=variant)
                for text in public_prompt_parts(question):
                    yield text, {"category": "public_question_prompt", "id": identity,
                                 "question_kind": kind, "variant": variant}


def collect_sentence_records(*, items=None, resource_dir=RESOURCE_DIR, include_lexical=True):
    """Return sorted text records, preserving source categories and exact text.

    Dictionaries are read through ContentRepository.get_example so fingerprinted
    semantic-review corrections match the app. Never normalize punctuation,
    kana, whitespace or Unicode: annotation offsets target the original text.
    """
    from kanamemory.content import ContentRepository
    from kanamemory.example_catalogue import bundled_example_pack
    from kanamemory.learning_content import learning_items
    items = learning_items() if items is None else list(items)
    records = {}

    def add(text, source):
        if not isinstance(text, str) or not text.strip():
            return
        record = records.setdefault(text, {"text": text, "sources": []})
        if source not in record["sources"]:
            record["sources"].append(source)

    for example in bundled_example_pack(items)["examples"]:
        add(example["text"], {"category": "shared_example", "id": example["id"],
                             "revision": example["revision"]})
    for text, source in lesson_text_records(items):
        add(text, source)
    if include_lexical:
        with ContentRepository(Path(resource_dir) / "content.sqlite3") as repo:
            ids = [row[0] for row in repo._db.execute("SELECT id FROM example ORDER BY id")]
            for identity in ids:
                example = repo.get_example(identity)
                if example:
                    add(example["text"], {"category": "lexical_example", "id": identity,
                        "review_state": example.get("review_state", "unreviewed")})
    for record in records.values():
        record["sources"].sort(key=lambda s: json.dumps(s, ensure_ascii=False, sort_keys=True))
    return [records[text] for text in sorted(records)]


def collect_sentences(**kwargs):
    """Simple builder API: sorted unique exact public Japanese text strings."""
    return [row["text"] for row in collect_sentence_records(**kwargs)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    records = collect_sentence_records()
    counts = Counter(source["category"] for record in records for source in record["sources"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"format": "kanamemory-public-sentence-corpus",
            "version": 1, "records": records}, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"unique_texts": len(records), "source_counts": dict(sorted(counts.items()))},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
