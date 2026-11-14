"""Write the word_sound_mix teaching namespace from the reviewed cognate class table.

Source: config/cognate_classes_20261109.json (design 11 section 4, S1). Every class-S
row binds into word_sound_mix.cognate_words as {class, reading, title}. A row must
name an existing bundled lesson word by id with the same title and reading, so a
renamed or re-read word stops the build instead of silently keeping its full
word-to-Chinese bank. The private namespace holds no sentence or translation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))

SOURCE = ROOT / "config/cognate_classes_20261109.json"
OUTPUT = ROOT / "data/content_packs/full/teaching/word_sound_mix.json"
MIXED_CLASSES = ("S",)


def build(source=SOURCE):
    from kanamemory.learning_content import learning_items
    rows = json.loads(Path(source).read_text("utf8"))["words"]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate cognate class row")
    by_id = {item["id"]: item for item in learning_items("word")}
    words = {}
    for row in rows:
        if row["class"] not in MIXED_CLASSES:
            continue
        item = by_id.get(row["id"])
        if item is None or (item["title"], item["reading"]) != (row["title"], row["reading"]):
            raise ValueError(f"Cognate class row must match one lesson word: {row['title']} ({row['id']})")
        words[item["id"]] = {"class": row["class"], "reading": item["reading"], "title": item["title"]}
    return {"schema_version": 1, "cognate_words": dict(sorted(words.items()))}


def main():
    from kanamemory.content_pack import namespace_document
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    value = build(args.source)
    args.output.write_text(json.dumps(namespace_document("word_sound_mix", value), ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")) + "\n", "utf8")
    print(f"{args.output}: {len(value['cognate_words'])} words")


if __name__ == "__main__":
    main()
