"""Write the word_sound_mix teaching namespace from the reviewed cognate list.

Every listed title must match exactly one bundled lesson word, so a renamed word
stops the build instead of silently keeping its full word-to-Chinese bank. The
private namespace binds id, title and reading; it holds no sentence or translation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))

SOURCE = ROOT / "config/cognate_words_20261106.json"
OUTPUT = ROOT / "data/content_packs/full/teaching/word_sound_mix.json"


def build(source=SOURCE):
    from kanamemory.learning_content import learning_items
    titles = json.loads(Path(source).read_text("utf8"))["titles"]
    if len(titles) != len(set(titles)):
        raise ValueError("Duplicate reviewed cognate title")
    by_title = {}
    for item in learning_items("word"):
        by_title.setdefault(item["title"], []).append(item)
    words = {}
    for title in titles:
        matches = by_title.get(title, [])
        if len(matches) != 1:
            raise ValueError(f"Reviewed cognate must match one lesson word: {title} ({len(matches)})")
        item = matches[0]
        words[item["id"]] = {"title": item["title"], "reading": item["reading"]}
    return {"schema_version": 1, "cognate_words": words}


def main():
    from kanamemory.content_pack import namespace_document
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    value = build()
    args.output.write_text(json.dumps(namespace_document("word_sound_mix", value), ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")) + "\n", "utf8")
    print(f"{args.output}: {len(value['cognate_words'])} words")


if __name__ == "__main__":
    main()
