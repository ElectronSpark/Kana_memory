"""Expand a compact authored question batch into a template-variants staging file.

Three commands:

``build AUTHORED.json [--plan PLAN] [--out-dir DIR] [--source-prefix P]``
    reads ``{"variants": [entry, ...]}`` (or a bare list of entries) and writes, next to the input unless
    ``--out-dir`` says otherwise (stem = file name without ``.authored.json``):

    ``<stem>.variants.json``  a ``kanamemory-template-variants`` v1 staging file; each variant is
                              ``{"key", "target_id", "kind", "template", ["new_word"]}``
    ``<stem>.fields.json``    ``{key: {"opens_after_reviews", "scenario_id", ["glosses"], ["needs"]}}``, the side fields the
                              merge tool writes into the template before fingerprinting

``assemble BATCH_DIR [--out-dir DIR] [--source-prefix P]``
    joins the parts ``BATCH_DIR/authored/<stem>.variants.json`` (ordered by stem) and their fields files into
    ``draft.variants.json`` (source ``P:<batch folder name>``; a new-word batch also carries a proxy ``note``) and
    ``fields.json``; keys must be unique across the batch. Run the authors' checks before; it does not.

``retarget VARIANTS.json --ids MAPPING.json [--fields FIELDS.json] -o OUTPUT.json``
    replaces the stand-in target of new-word variants by the real lesson id once the lesson exists, recomputes
    what depends on the target (``target_id``, ``practice.assessed_ids``, ``practice.context_id``) and drops
    ``new_word``. The result equals what ``build`` writes when the entry names the real id as ``target_id``.
    ``needs`` (in the fields file) are left alone; with ``--fields``, a need naming a stand-in id is reported.

Compact entry (one object per template):
  key          unique template key, the primary key of everything downstream
  target_id    the target lesson id; a new-word entry names ``word`` instead
  word         new-word batches: the new lesson's form; the target becomes the stand-in ``proxy_id`` of
               ``plan.json`` (``words[form]``), and the variant carries
               ``new_word = {form, entry_id, proxy_id, note}``
  kind         one of DEFAULT_FAMILY
  family       practice.task_family (default per kind, DEFAULT_FAMILY)
  prompt, task, answer, feedback
               as in the template (a reorder entry has no prompt; its task defaults to REORDER_TASK)
  choices      choice kinds: the options in authored order
  blocks       grammar_reorder: the answer in ordered blocks; prompt = first block + one cue per further block,
               choices = blocks, answer = the blocks joined
  accepted     further accepted answers of typed and reorder kinds
  span         quote marked as practice.target_spans[0] (default: the gap mark if the prompt has one, else the
               whole prompt; production and reorder: the whole prompt); ``span_index`` picks the n-th occurrence
  hint         folded Chinese hint (``hint_policy: fold_always``)
  readings     copied to the template
  scenario     fields ``scenario_id`` (required)
  release      fields ``opens_after_reviews``, 0..40 (default 0)
  glosses      fields ``glosses``: objects; only surface, reading, meaning, lesson_id and start are kept
  needs        fields ``needs``: 1..8 distinct content ids the question really depends on and the dependency tool
               cannot detect (never for timing); the merge puts them first in practice.background_ids. Never a
               new word's stand-in id (name the real lesson id)

The output is written exactly as the question writer's first builder wrote it (UTF-8, ``indent=1``, text mode,
so the platform's line ending), which keeps old batches byte-identical when rebuilt.
Validation only rejects input; it never changes the output of a valid entry. Each built template is also
checked with the app's ``validate_practice`` when the app package is importable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

FORMAT = "kanamemory-template-variants"
VERSION = 1
SOURCE_PREFIX = "scenario_20261114"
GAP = "（　）"
REORDER_CUE = "＿＿"
REORDER_TASK = "排成一句话。"
MAX_RELEASE = 40
MAX_NEEDS = 8  # tools/practice_authoring_fields.MAX_NEEDS; the merge checks the ids against the catalogue
GLOSS_FIELDS = ("surface", "reading", "meaning", "lesson_id", "start")
PROXY_NOTE = "target_id / assessed_ids are a proxy: replace by the new lesson id"
ASSEMBLED_PROXY_NOTE = "target_id / assessed_ids are proxies: replace by the new lesson id (new_word.entry_id)"
PART_SUFFIX = ".variants.json"

GRAMMAR_FAMILY = {"grammar_meaning": "grammar_in_context", "grammar_cloze": "contextual_completion",
                  "grammar_reorder": "sentence_reconstruction", "grammar_transform": "form_transformation",
                  "targeted_correction": "targeted_correction",
                  "controlled_sentence_production": "controlled_sentence_production"}
WORD_FAMILY = {"vocab_context_choice": "context_choice", "vocab_orthography": "contextual_orthography",
               "vocab_usage": "vocab_usage", "vocab_meaning": "word_in_context"}
DEFAULT_FAMILY = {**GRAMMAR_FAMILY, **WORD_FAMILY}
# Typed grammar kinds: text input, no audio/reading/accent support.
TEXT_KINDS = frozenset({"grammar_transform", "targeted_correction", "controlled_sentence_production"})
# Kinds whose options are meanings, so showing them first reveals nothing (``safe``).
SAFE_CHOICE_KINDS = frozenset({"grammar_meaning", "vocab_meaning"})
# Kinds that assess exactly their target (``coverage_scope: focused``).
FOCUSED_KINDS = frozenset({"targeted_correction", "controlled_sentence_production", *WORD_FAMILY})
SCAFFOLD = {"choice": "recognition", "text": "retrieval", "reorder": "cued"}
NO_SUPPORT = {"audio_safe": False, "reading_safe": False, "accent_safe": False}


class BatchError(ValueError):
    """An authored batch, plan or mapping that cannot be built; the message names every offending key."""


# ---------------------------------------------------------------------------------------------------------------
# Pure rules


def input_mode(entry):
    """The response control of an entry: reorder, text (typed kinds and cloze without options) or choice."""
    kind = entry["kind"]
    if kind == "grammar_reorder":
        return "reorder"
    if kind in TEXT_KINDS or kind == "vocab_usage":
        return "text"
    if kind == "grammar_cloze" and not entry.get("choices"):
        return "text"
    return "choice"


def context_id(target_id, kind, prompt, task):
    """The practice context id; it covers the target, so a new target needs a new id."""
    text = json.dumps([target_id, kind, prompt, task], ensure_ascii=False)
    return "context:" + hashlib.md5(text.encode()).hexdigest()


def reorder_prompt(blocks):
    """The first block, then one cue per block the learner places."""
    return blocks[0] + " " + " ".join([REORDER_CUE] * (len(blocks) - 1))


def accepted_answers(answer, entry):
    """The answer first, then the authored alternatives, without repeats."""
    return list(dict.fromkeys([answer, *entry.get("accepted", [])]))


def span_quote(entry, mode, prompt):
    """The quote marked as the target span: authored, else the gap mark, else the whole prompt."""
    quote = entry.get("span")
    if mode == "reorder":
        return prompt
    if entry["kind"] == "controlled_sentence_production" and not quote:
        return prompt
    if not quote:
        return GAP if GAP in prompt else prompt
    return quote


def find_occurrence(prompt, quote, index):
    """Start of the ``index``-th occurrence of ``quote`` (overlaps counted), or -1 when there are fewer."""
    start = -1
    for _ in range(index + 1):
        start = prompt.find(quote, start + 1)
        if start < 0:
            return -1
    return start


def needs_problems(key, needs, target_id=None):
    """Messages for an authored ``needs`` list: 1..MAX_NEEDS distinct non-empty strings, never the target.
    Whether each is a known learning item is the merge tool's check (it has the catalogue)."""
    if (not isinstance(needs, list) or not 1 <= len(needs) <= MAX_NEEDS
            or any(not isinstance(cid, str) or not cid.strip() for cid in needs)):
        return [f"{key}: needs must be a list of 1..{MAX_NEEDS} non-empty content ids (leave it out when none)"]
    problems = []
    if len(set(needs)) != len(needs):
        problems.append(f"{key}: needs repeats an id")
    if target_id in needs:
        problems.append(f"{key}: needs names the entry's own target {target_id!r}")
    return problems


def proxy_need_problems(key, needs, proxies, target_id=None):
    """A need naming a new word's stand-in id is a mistake: stand-ins are replaced only as targets (retarget),
    never inside needs, so it would stay a dependency nobody can learn. (The entry's own target is reported by
    needs_problems.)"""
    if not isinstance(needs, list):
        return []
    return [f"{key}: need {cid!r} is a new word's stand-in id; name the real lesson id" for cid in needs
            if cid in proxies and cid != target_id]


def entry_problems(entry):
    """Messages for an entry the builder cannot turn into a valid template (empty when it can)."""
    key = entry.get("key")
    kind = entry.get("kind")
    if not isinstance(kind, str) or kind not in DEFAULT_FAMILY:
        return [f"{key}: kind {kind!r} not allowed"]
    problems = []
    required = ["feedback"]
    mode = input_mode(entry)
    if mode == "reorder":
        blocks = entry.get("blocks")
        if (not isinstance(blocks, list) or not blocks
                or any(not isinstance(b, str) or not b for b in blocks)):
            problems.append(f"{key}: reorder needs blocks, a list of non-empty strings")
        elif "answer" in entry and entry["answer"] != "".join(blocks):
            problems.append(f"{key}: reorder blocks {blocks!r} do not form the answer {entry['answer']!r}")
    else:
        required += ["prompt", "task", "answer"]
        if mode == "choice":
            required.append("choices")
            if "choices" in entry and not isinstance(entry["choices"], list):
                problems.append(f"{key}: choices must be a list")
        if "prompt" in entry and not isinstance(entry["prompt"], str):
            problems.append(f"{key}: prompt must be a string")
    problems += [f"{key}: required field {name!r} missing" for name in required if name not in entry]
    if "accepted" in entry and not isinstance(entry["accepted"], list):
        problems.append(f"{key}: accepted must be a list")
    if "target_id" not in entry and not entry.get("word"):
        problems.append(f"{key}: required field 'target_id' (or 'word') missing")
    if not entry.get("scenario"):
        problems.append(f"{key}: scenario missing")
    release = entry.get("release", 0)
    if type(release) is not int or not 0 <= release <= MAX_RELEASE:
        problems.append(f"{key}: release {release!r} outside 0..{MAX_RELEASE}")
    glosses = entry.get("glosses")
    if glosses and (not isinstance(glosses, list) or any(not isinstance(g, dict) for g in glosses)):
        problems.append(f"{key}: glosses must be a list of objects")
    if "needs" in entry:
        problems += needs_problems(key, entry["needs"], entry.get("target_id"))
    index = entry.get("span_index", 0)
    if type(index) is not int or index < 0:
        problems.append(f"{key}: span_index {index!r} must be a non-negative integer")
    if problems:
        return problems
    prompt = reorder_prompt(entry["blocks"]) if mode == "reorder" else entry["prompt"]
    quote = span_quote(entry, mode, prompt)
    if not isinstance(quote, str):
        return [f"{key}: span must be a string"]
    if find_occurrence(prompt, quote, index) < 0:
        count = prompt.count(quote)
        return [f"{key}: span {quote!r} occurrence {index} not in prompt {prompt!r} ({count} found)"]
    return []


def build_template(entry):
    """The template of one valid entry, key order as the staging format has always had it."""
    kind = entry["kind"]
    mode = input_mode(entry)
    template = {}
    if mode == "reorder":
        blocks = entry["blocks"]
        answer = "".join(blocks)
        template.update(prompt=reorder_prompt(blocks), task=entry.get("task") or REORDER_TASK, answer=answer,
                        accepted_answers=accepted_answers(answer, entry), input_mode="reorder",
                        choices=list(blocks), strict=False, safe=False)
    else:
        template.update(prompt=entry["prompt"], task=entry["task"], answer=entry["answer"])
        if mode == "text":
            template.update(accepted_answers=accepted_answers(entry["answer"], entry), choices=[],
                            input_mode="text", strict=False, safe=False)
            if kind == "targeted_correction":
                template["known_wrong"] = [entry["prompt"]]
        else:
            template.update(choices=list(entry["choices"]), input_mode="choice", strict=True,
                            safe=kind in SAFE_CHOICE_KINDS)
    template["feedback"] = entry["feedback"]
    if kind in TEXT_KINDS or kind in WORD_FAMILY:
        template["support"] = dict(NO_SUPPORT)
    prompt = template["prompt"]
    quote = span_quote(entry, mode, prompt)
    start = find_occurrence(prompt, quote, entry.get("span_index", 0))
    practice = {"schema_version": 1, "task_family": entry.get("family") or DEFAULT_FAMILY[kind],
                "context_id": context_id(entry["target_id"], kind, prompt, template["task"]),
                "scaffold": SCAFFOLD[mode],
                "target_spans": [{"start": start, "end": start + len(quote), "quote": quote}],
                "background_ids": []}
    if kind in FOCUSED_KINDS:
        practice.update(coverage_scope="focused", assessed_ids=[entry["target_id"]])
    practice["reviewed"] = True
    template["practice"] = practice
    if entry.get("readings"):
        template["readings"] = dict(entry["readings"])
    if entry.get("hint"):
        template["hint"] = entry["hint"]
        template["hint_policy"] = "fold_always"
    return template


def build_variant(entry, plan_words=None):
    """One staging variant; a new-word entry (target already set to its proxy) also carries ``new_word``."""
    variant = {"key": entry["key"], "target_id": entry["target_id"], "kind": entry["kind"],
               "template": build_template(entry)}
    if entry.get("word"):
        variant["new_word"] = {"form": entry["word"], "entry_id": plan_words[entry["word"]]["entry_id"],
                               "proxy_id": entry["target_id"], "note": PROXY_NOTE}
    return variant


def fields_of(entry):
    """The side fields of one entry: release level, scenario and (optional) glosses."""
    fields = {"opens_after_reviews": int(entry.get("release", 0)), "scenario_id": entry["scenario"]}
    if entry.get("glosses"):
        fields["glosses"] = [{name: gloss[name] for name in GLOSS_FIELDS if name in gloss}
                             for gloss in entry["glosses"]]
    if "needs" in entry:
        fields["needs"] = list(entry["needs"])
    return fields


def key_problems(entries):
    """Missing or repeated keys (every key must be a unique string)."""
    keys = [entry.get("key") if isinstance(entry, dict) else None for entry in entries]
    missing = sum(1 for key in keys if not isinstance(key, str) or not key)
    seen, duplicated = set(), set()
    for key in keys:
        if isinstance(key, str) and key:
            (duplicated if key in seen else seen).add(key)
    if missing or duplicated:
        return [f"every entry needs a unique key; missing {missing}, duplicated {sorted(duplicated)[:10]}"]
    return []


def resolve_words(entries, plan):
    """Entries with the stand-in target of each new word set (copies; the input stays as read)."""
    words = (plan or {}).get("words", {})
    problems = [f"{e['key']}: word {e['word']!r} not in plan.json words" for e in entries
                if e.get("word") and not isinstance(words.get(e["word"]), dict)]
    problems += [f"{e['key']}: plan.json word {e['word']!r} needs proxy_id and entry_id" for e in entries
                 if e.get("word") and isinstance(words.get(e["word"]), dict)
                 and not {"proxy_id", "entry_id"} <= set(words[e["word"]])]
    if problems:
        raise BatchError("\n".join(problems))
    return [dict(e, target_id=words[e["word"]]["proxy_id"]) if e.get("word") else e for e in entries]


def build_batch(entries, source_name, plan=None, source_prefix=SOURCE_PREFIX):
    """The staging document and the fields map of an authored batch; BatchError lists every problem."""
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise BatchError("an authored file holds a list of entry objects")
    problems = key_problems(entries)
    if problems:
        raise BatchError("\n".join(problems))
    if any(e.get("word") for e in entries) and plan is None:
        raise BatchError("new-word entries need plan.json (--plan)")
    entries = resolve_words(entries, plan)
    problems = [message for entry in entries for message in entry_problems(entry)]
    plan_words = (plan or {}).get("words", {})
    proxies = {row.get("proxy_id") for row in plan_words.values() if isinstance(row, dict)} - {None}
    problems += [message for entry in entries
                 for message in proxy_need_problems(entry["key"], entry.get("needs"), proxies, entry.get("target_id"))]
    if problems:
        raise BatchError("\n".join(problems))
    document = {"format": FORMAT, "version": VERSION, "source": f"{source_prefix}:{source_name}",
                "variants": [build_variant(entry, plan_words) for entry in entries]}
    fields = {entry["key"]: fields_of(entry) for entry in entries}
    return document, fields


# ---------------------------------------------------------------------------------------------------------------
# Retarget


def lesson_id_index(mapping):
    """``{entry_id or form: {lesson ids}}`` from a mapping file.

    Accepted shapes: an object ``{entry_id or form: lesson id}``, or the arranger's ``new_items.json`` (a list of
    catalogue lessons, each with ``id`` and ``word_id`` and/or ``title``).
    """
    index = {}
    if isinstance(mapping, dict):
        pairs = list(mapping.items())
    elif isinstance(mapping, list) and all(isinstance(row, dict) for row in mapping):
        pairs = [(row.get(name), row.get("id")) for row in mapping for name in ("word_id", "title")]
        pairs = [(name, lesson) for name, lesson in pairs if name is not None]
    else:
        raise BatchError("--ids: an object {entry_id or form: lesson id} or a list of lessons with id")
    bad = sorted(str(name) for name, lesson in pairs if not isinstance(lesson, str) or not lesson)
    if bad:
        raise BatchError(f"--ids: no lesson id for {bad[:10]}")
    for name, lesson in pairs:
        index.setdefault(name, set()).add(lesson)
    return index


def real_lesson_id(new_word, index):
    """The lesson id of a new word: by entry_id, else by form; None unless exactly one id fits."""
    for name in (new_word.get("entry_id"), new_word.get("form")):
        found = index.get(name, set())
        if len(found) == 1:
            return next(iter(found))
    return None


def retarget_variant(variant, lesson_id):
    """A copy of a new-word variant aimed at its real lesson, without ``new_word``."""
    proxy = variant["new_word"]["proxy_id"]
    template = deepcopy(variant["template"])
    practice = template["practice"]
    practice["context_id"] = context_id(lesson_id, variant["kind"], template["prompt"], template["task"])
    if "assessed_ids" in practice:
        practice["assessed_ids"] = [lesson_id if cid == proxy else cid for cid in practice["assessed_ids"]]
    out = {name: value for name, value in variant.items() if name != "new_word"}
    out["target_id"] = lesson_id
    out["template"] = template
    return out


def proxy_problems(variant):
    """Messages when a new-word variant is no longer as the builder wrote it (so a recompute could be wrong)."""
    key, proxy = variant.get("key"), variant["new_word"].get("proxy_id")
    template = variant.get("template", {})
    practice = template.get("practice", {})
    problems = []
    if variant.get("target_id") != proxy:
        problems.append(f"{key}: target_id is not new_word.proxy_id")
    if practice.get("context_id") != context_id(proxy, variant.get("kind"), template.get("prompt"),
                                                template.get("task")):
        problems.append(f"{key}: context_id does not match the proxy target (template edited after build?)")
    return problems


def retarget_document(document, index):
    """The staging document with every new-word variant aimed at its real lesson; BatchError otherwise."""
    if not isinstance(document, dict) or document.get("format") != FORMAT or not isinstance(
            document.get("variants"), list):
        raise BatchError(f"not a {FORMAT} file")
    problems, unmapped, variants = [], [], []
    for variant in document["variants"]:
        if not isinstance(variant, dict) or "new_word" not in variant:
            variants.append(variant)
            continue
        lesson = real_lesson_id(variant["new_word"], index)
        if lesson is None:
            word = variant["new_word"]
            unmapped.append(f"{variant.get('key')} ({word.get('form')}, {word.get('entry_id')})")
            continue
        problems += proxy_problems(variant)
        variants.append(retarget_variant(variant, lesson))
    if unmapped:
        problems.insert(0, f"{len(unmapped)} new-word variants unmapped: " + "; ".join(unmapped))
    if problems:
        raise BatchError("\n".join(problems))
    result = {**document, "variants": variants}
    if result.get("note") == ASSEMBLED_PROXY_NOTE and not any(
            isinstance(variant, dict) and "new_word" in variant for variant in variants):
        del result["note"]  # an assembled draft's proxy note, now untrue
    return result


def fields_needs_problems(document, fields):
    """Messages for needs in a fields map that name a stand-in id of the document's new-word variants.

    retarget rewrites stand-ins only as targets; a need is the author's own declaration and is never rewritten.
    """
    proxies = {variant["new_word"].get("proxy_id") for variant in document.get("variants", ())
               if isinstance(variant, dict) and isinstance(variant.get("new_word"), dict)} - {None}
    return [message for key, entry in fields.items() if isinstance(entry, dict)
            for message in proxy_need_problems(key, entry.get("needs"), proxies)]


def fields_key_problems(document, fields):
    """Messages when a fields map does not have exactly the variant keys."""
    keys = [variant.get("key") for variant in document["variants"]]
    missing = [key for key in keys if key not in fields]
    extra = [key for key in fields if key not in set(keys)]
    problems = []
    if missing:
        problems.append(f"fields file lacks {len(missing)} keys: {missing[:10]}")
    if extra:
        problems.append(f"fields file has {len(extra)} keys without a variant: {extra[:10]}")
    return problems


# ---------------------------------------------------------------------------------------------------------------
# Assemble


def assemble_batch(parts, batch_name, source_prefix=SOURCE_PREFIX):
    """The batch-level staging document and fields map from ``[(stem, variants document, fields map)]``.

    Parts are concatenated in the order given, variants in file order; keys must be unique across the batch and
    every part's fields map must have exactly its variant keys. A batch of new-word variants also carries NOTE.
    """
    problems, variants, fields, seen = [], [], {}, set()
    for stem, document, part_fields in parts:
        if not isinstance(document, dict) or document.get("format") != FORMAT or not isinstance(
                document.get("variants"), list):
            problems.append(f"{stem}: not a {FORMAT} file")
            continue
        if not isinstance(part_fields, dict):
            problems.append(f"{stem}: the fields file is not an object")
            continue
        malformed = [variant for variant in document["variants"] if not isinstance(variant, dict)
                     or not {"key", "target_id", "kind", "template"} <= set(variant)]
        if malformed:
            problems.append(f"{stem}: {len(malformed)} variants lack key, target_id, kind or template")
            continue
        for variant in document["variants"]:
            if variant["key"] in seen:
                problems.append(f"{stem}: duplicate key {variant['key']}")
            seen.add(variant["key"])
            variants.append(variant)
            if variant["key"] in part_fields:
                fields[variant["key"]] = part_fields[variant["key"]]
        problems += [f"{stem}: {message}" for message in fields_key_problems(document, part_fields)]
    if problems:
        raise BatchError("\n".join(problems))
    document = {"format": FORMAT, "version": VERSION, "source": f"{source_prefix}:{batch_name}"}
    if any("new_word" in variant for variant in variants):
        document["note"] = ASSEMBLED_PROXY_NOTE
    document["variants"] = variants
    return document, fields


# ---------------------------------------------------------------------------------------------------------------
# App check and files


def app_practice_validator():
    """``kanamemory.learning_questions.validate_practice`` when the app package beside tools/ imports, else None."""
    src = str(ROOT / "app" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from kanamemory.learning_questions import validate_practice
    except ImportError:
        return None
    return validate_practice


def app_problems(document, validate):
    """Keys whose built practice the app rejects. The check reads copies, so it cannot change the output."""
    problems = []
    for variant in document["variants"]:
        try:
            validate(deepcopy(variant["template"]), target_id=variant["target_id"])
        except ValueError as error:
            problems.append(f"{variant['key']}: practice rejected by the app: {error}")
    return problems


def read_json(path):
    return json.loads(Path(path).read_text("utf8"))


def write_json(path, value):
    """UTF-8, indent 1, trailing newline, text mode: the bytes the first builder wrote."""
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", "utf8")


def output_stem(source):
    """``a.authored.json`` -> ``a`` (as the first builder named its outputs)."""
    return source.name.replace(".authored.json", "").replace(".json", "")


def run_build(args, validate):
    source = Path(args.authored).resolve()
    data = read_json(source)
    entries = data.get("variants") if isinstance(data, dict) else data
    plan = None
    if isinstance(entries, list) and any(isinstance(e, dict) and e.get("word") for e in entries):
        plan_path = Path(args.plan) if args.plan else source.parent.parent / "plan.json"
        if not plan_path.is_file():
            raise BatchError(f"new-word entries need plan.json; not found: {plan_path}")
        plan = read_json(plan_path)
    document, fields = build_batch(entries, source.name, plan, args.source_prefix)
    if validate is not None:
        problems = app_problems(document, validate)
        if problems:
            raise BatchError("\n".join(problems))
    out_dir = Path(args.out_dir) if args.out_dir else source.parent
    stem = output_stem(source)
    target = out_dir / (stem + ".variants.json")
    write_json(target, document)
    write_json(out_dir / (stem + ".fields.json"), fields)
    print(f"built {len(document['variants'])} variants -> {target}")


def part_files(folder):
    """``(stem, variants file, fields file)`` of every built part in ``folder``, ordered by stem."""
    found = sorted((path.name[:-len(PART_SUFFIX)], path) for path in folder.glob("*" + PART_SUFFIX))
    return [(stem, path, path.with_name(stem + ".fields.json")) for stem, path in found]


def run_assemble(args, validate):
    batch = Path(args.batch).resolve()
    parts = part_files(batch / "authored")
    if not parts:
        raise BatchError(f"no *{PART_SUFFIX} in {batch / 'authored'}")
    absent = [str(fields) for _, _, fields in parts if not fields.is_file()]
    if absent:
        raise BatchError("fields files missing: " + ", ".join(absent))
    document, fields = assemble_batch([(stem, read_json(path), read_json(fields_path))
                                       for stem, path, fields_path in parts], batch.name, args.source_prefix)
    out_dir = Path(args.out_dir) if args.out_dir else batch
    write_json(out_dir / "draft.variants.json", document)
    write_json(out_dir / "fields.json", fields)
    print(f"assembled {len(document['variants'])} templates from {len(parts)} files -> {out_dir}")


def run_retarget(args, validate):
    document = read_json(args.variants)
    result = retarget_document(document, lesson_id_index(read_json(args.ids)))
    problems = []
    if args.fields:
        fields = read_json(args.fields)
        problems = fields_key_problems(result, fields) + fields_needs_problems(document, fields)
    if validate is not None:
        problems += app_problems(result, validate)
    if problems:
        raise BatchError("\n".join(problems))
    write_json(args.output, result)
    changed = sum(1 for variant in document["variants"] if isinstance(variant, dict) and "new_word" in variant)
    print(f"retargeted {changed} of {len(result['variants'])} variants -> {args.output}")


def parser():
    top = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = top.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="expand an authored file into variants and fields files")
    build.add_argument("authored", help="<stem>.authored.json")
    build.add_argument("--plan", help="new-word plan (default: ../plan.json beside the authored folder)")
    build.add_argument("--out-dir", help="output folder (default: the authored file's folder)")
    build.add_argument("--source-prefix", default=SOURCE_PREFIX, help="label before ':<file name>' in 'source'")
    assemble = commands.add_parser("assemble", help="join a batch's built parts into draft.variants.json and fields.json")
    assemble.add_argument("batch", help="batch folder; reads authored/*.variants.json and their fields files")
    assemble.add_argument("--out-dir", help="output folder (default: the batch folder)")
    assemble.add_argument("--source-prefix", default=SOURCE_PREFIX, help="label before ':<batch name>' in 'source'")
    retarget = commands.add_parser("retarget", help="aim new-word variants at their real lessons")
    retarget.add_argument("variants", help="a variants file written by build")
    retarget.add_argument("--ids", required=True,
                          help="JSON {entry_id or form: lesson id}, or new_items.json (lessons with id, word_id, title)")
    retarget.add_argument("--fields", help="the batch's fields file, checked to have exactly the variant keys")
    retarget.add_argument("-o", "--output", required=True, help="output variants file")
    return top


def main(argv=None, validate=None):
    """Run a command; ``validate`` replaces the app's practice check (tests), otherwise it is imported."""
    args = parser().parse_args(argv)
    command = {"build": run_build, "assemble": run_assemble, "retarget": run_retarget}[args.command]
    if command is not run_assemble:
        validate = validate if validate is not None else app_practice_validator()
        if validate is None:
            print("note: kanamemory not importable; practice not checked by the app", file=sys.stderr)
    try:
        command(args, validate)
    except BatchError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
