"""Which probe questions each lesson has at its place on the main route (read only; design 02 section 4).

Probe first (2026-11-21): a new lesson starts with one probe per ability (word:
meaning, reading, use; grammar: meaning, application; pronunciation rule:
rule, application). A probe must be unguided, definite (a choice, a block
order, a pitch task, or a strict typed task answerable in kana) and its other
material must already be known. This tool walks the main route of the teaching
pack in use (the installed one, or the file named by KANAMEMORY_TEACHING_PACK)
and, for every lesson, takes "known" = the lessons before it on the route plus
every kana; it writes one tab-separated row per lesson with the probe kind found
for each ability, or "missing", and prints a summary.

    python -X utf8 tools/probe_readiness.py [--out FILE.tsv] [--summary FILE.txt]

Nothing is written to any learner record or pack. The input settings are the
defaults (no system Japanese keyboard).
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app" / "src"))

LABELS = {"word": {"understanding": "meaning", "reading": "reading", "use": "use"},
          "grammar": {"understanding": "meaning", "application": "application"},
          "pronunciation": {"understanding": "rule", "application": "application"}}


def route_lessons():
    """Lesson ids in main-route order (the core study path), each once."""
    from kanamemory.learning_content import learning_items
    from kanamemory.study_path_catalog import build_study_path, flatten_sections
    items = learning_items()
    path = build_study_path([], items)
    order, seen = [], set()
    for section in flatten_sections(path):
        for entry in section.get("entries", section.get("items", ())):
            cid = entry.get("content_id") if isinstance(entry, dict) else entry
            if isinstance(cid, str) and cid not in seen and not cid.startswith("kana:"):
                seen.add(cid)
                order.append(cid)
    by_id = {item["id"]: item for item in items}
    return [by_id[cid] for cid in order if cid in by_id]


def probe_for(item, ability, known):
    """The first qualifying probe kind of ``ability`` with ``known`` lessons before it, or None."""
    from kanamemory import probe_rules
    from kanamemory.answer_input import available
    from kanamemory.learning_practice_policy import _candidate_questions
    from kanamemory.learning_probe import probe_qualifies
    from kanamemory.learning_questions import build_learning_question, question_kinds, question_requires
    track = probe_rules.track(item.get("family"), item.get("domain"))
    present = set(question_kinds(item, ability=ability))
    kinds = [kind for kind in probe_rules.probe_kinds(track, ability) if kind in present]
    state = {"known_background_ids": known, "system_japanese_input": False}
    for candidate in _candidate_questions(item, ability, kinds, state):
        try:
            question = build_learning_question(item, candidate["kind"], variant=candidate["variant"])
        except (ValueError, KeyError, TypeError):
            continue
        if not probe_qualifies(question, track, ability, candidate=candidate):  # the effective answer policy
            continue
        requires = question_requires(item, question)
        if requires is not None and any(not (r in known or str(r).startswith("kana:")) for r in requires):
            continue
        if available(question, system_japanese=False):
            return candidate["kind"]
    return None


def kana_requirements(items):
    """Every kana requirement the lessons' templates declare (all kana count as known)."""
    from kanamemory.learning_questions import practice_templates, template_requires
    found = set()
    for item in items:
        for kind, variants in (practice_templates(item) or {}).items():
            for raw in variants if isinstance(variants, list) else ():
                try:
                    requires = template_requires(item, kind, raw) if isinstance(raw, dict) else None
                except (ValueError, KeyError, TypeError):
                    requires = None
                found |= {r for r in requires or () if str(r).startswith("kana:")}
    return frozenset(found)


def main(argv=None):
    from kanamemory import probe_rules
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    rows, counts, lessons = [], Counter(), Counter()
    lessons_in_order = route_lessons()
    kana = kana_requirements(lessons_in_order)
    before = []
    for position, item in enumerate(lessons_in_order, 1):
        track = probe_rules.track(item.get("family"), item.get("domain"))
        known = frozenset({*before, item["id"], *kana})
        found, missing = [], []
        for ability in probe_rules.order(track):
            if ability not in item.get("abilities", ()):
                continue
            label = LABELS[track][ability]
            kind = probe_for(item, ability, known)
            counts[(track, label, "ok" if kind else "missing")] += 1
            (found if kind else missing).append(f"{label}={kind}" if kind else label)
        lessons[(track, "complete" if not missing else "incomplete")] += 1
        rows.append([str(position), item["id"], track or "", item.get("title", ""), ";".join(found), ";".join(missing)])
        before.append(item["id"])
    header = ["position", "content_id", "track", "title", "probes", "missing"]
    text = "\n".join("\t".join(row) for row in [header, *rows]) + "\n"
    summary = [f"课程项 {len(rows)} 项（按主线顺序；已知＝主线上排在它前面的课程项和全部假名）。"]
    for track in ("word", "grammar", "pronunciation"):
        total = lessons[(track, "complete")] + lessons[(track, "incomplete")]
        if not total:
            continue
        summary.append(f"{track}: {total} 项，试探题齐全 {lessons[(track, 'complete')]} 项，缺至少一道 {lessons[(track, 'incomplete')]} 项。")
        for label in LABELS[track].values():
            ok, gap = counts[(track, label, "ok")], counts[(track, label, "missing")]
            if ok or gap:
                summary.append(f"  {label}: 有 {ok}，缺 {gap}")
    summary_text = "\n".join(summary) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    if args.summary:
        args.summary.write_text(summary_text, encoding="utf-8", newline="\n")
    sys.stdout.write(summary_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
