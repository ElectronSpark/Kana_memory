"""Caches derived from the course content: one registry (content_caches), dropped together.

A test once patched a synthetic course in, a result page read the course word table through
question_readings._course_words, and that table stayed cached for the rest of the process: later
result pages showed a course word without its kana. Every cached function and every module-level
table of the app is therefore either registered with content_caches (content_pack drops them all in
clear_cache and whenever it builds a catalogue with another token) or exempt here with its reason;
a new cache that is neither fails the first test.
"""
import ast
import importlib
from pathlib import Path
import re
import threading

import pytest

from kanamemory import content_caches, content_pack, learning_content
from kanamemory.learning_conditions import _single_flight
from support.content_cache_fixtures import drop_content_caches  # noqa: F401 (a fixture)

SOURCE = Path(__file__).resolve().parents[1] / "app" / "src" / "kanamemory"
CACHING = re.compile(r"(^|\.)(lru_cache|cache|_single_flight)(\(|$)")
EMPTY_TABLES = frozenset({"{}", "[]", "[None, None]", "dict()", "threading.local()"})
REGISTERED = "registered"

# "file::name" -> REGISTERED, or why the cache cannot outlive the content it was computed from.
CACHES = {
    # Derived from the catalogue (learning_content and the namespaces read at import): registered.
    "question_readings.py::_course_words": REGISTERED,
    "question_readings.py::_lesson_forms": REGISTERED,
    "question_readings.py::_ENTRY_MEMO": REGISTERED,  # its key does not name the lesson it was located in
    "read_only_lessons.py::_MEMO": REGISTERED,  # per connection; dropped with the catalogue it compared against
    "result_readings.py::_TITLES": REGISTERED,
    "learning_questions.py::_word_pool": REGISTERED,
    "learning_questions.py::_READING_FORMS": REGISTERED,
    "question_support.py::_background_word_senses": REGISTERED,
    "learning_conditions.py::_reviewed_index": REGISTERED,
    "learning_dependencies.py::_bundled_dependency_signature": REGISTERED,
    "learning_dependencies.py::_bundled_fingerprint": REGISTERED,
    "structured_lessons.py::catalogue": REGISTERED,
    "lesson_diagrams.py::catalogue": REGISTERED,
    "kana_dependency_runtime.py::_bundled_graph": REGISTERED,
    "loanword_origins.py::_installed_items": REGISTERED,
    "kana_reading_rules.py::_SHARED": REGISTERED,   # keyed by the pack file only, not by the library layers
    "semantic_errors.py::_SHARED": REGISTERED,      # keyed by the pack file only, not by the library layers
    "semantic_errors.py::_LOCAL": "a per-thread flag while an entry is resolved; holds no content",
    "content_library_service.py::_DEPENDENCIES": REGISTERED,  # keyed by the catalogue ids only
    "study_routes.py::_BUILT": REGISTERED,
    "answer_policy/source.py::_LOCATED": REGISTERED,  # its key does not name the lesson it was located in
    "answer_policy/source.py::lesson_forms": REGISTERED,
    "answer_policy/kana_answer.py::_JUDGED": REGISTERED,  # judged with the catalogue's word tables
    # The source itself: keyed by file path and stamp, dropped by content_pack.clear_cache.
    "content_pack.py::_mirror": "the registry mirror, by path and stamp; clear_cache",
    "content_pack.py::_layers_for": "the checked library layers of the frozen registry; clear_cache",
    "content_pack.py::_pack_manifest": "a pack's manifest, by path and stamp; clear_cache",
    "content_pack.py::_teaching_sha256": "the bundled teaching database's content_sha256, by path and stamp; clear_cache",
    "content_pack.py::_frozen": "the registry this process started with; clear_cache",
    "content_pack.py::_effective": "the effective catalogue itself; clear_cache",
    "content_pack.py::_built_token": "the token of the last catalogue built; compared, never served",
    "content_pack.py::_merge_reports": "merge diagnostics; clear_cache",
    "content_pack.py::_catalogue_rejections": "keyed by the catalogue token",
    "content_pack.py::_catalogue_bystanders": "keyed by the catalogue token",
    "content_pack.py::_SHARD_READS": "per-thread connections inside shard_reads() only",
    "content_caches.py::_CLEARS": "the registry itself",
    # Keyed by everything they are computed from.
    "example_store.py::_MEMO": "keyed by the catalogue token, the builders and the items",
    "example_store.py::_MEMO_BUILDING": "builds in flight, keyed like _MEMO",
    "example_store.py::_CATALOGUE_FINGERPRINTS": "validated encodings, keyed by their sha256",
    "learning_questions.py::_GRAMMAR_FOLD": "keyed by the reading table it compiles",
    "material_review.py::_INDEX": "keyed by the identity of the review data object",
    "material_review.py::review_data": "the review file bundled with the app build, not the catalogue",
    "learning_store.py::_DICTIONARY_WORDS": "keyed by the lexicon path (an installed core library has its own)",
    "natural_translations.py::_lookup": "keyed by database path, stamp and text",
    "order_translations.py::_lookup": "keyed by database path, stamp and text",
    "sentence_annotations.py::_lookup": "keyed by text, database path and stamp",
    "word_sound_questions.py::_cohort_rows": "pure function of its arguments (the pool is an argument)",
    "word_sound_questions.py::_ranked_rows": "pure function of its arguments (the pool is an argument)",
    "word_sound_questions.py::_reading_options_of": "pure function of its arguments (the pool is an argument)",
    "word_sound_questions.py::_sound_key": "pure function of a reading",
    "word_sound_questions.py::_has_han": "pure function of a text",
    "word_sound_questions.py::_VOWEL_OF": "a constant built from literals at import",
    # Local to one call, or not content at all.
    "learning_store.py::LearningStore._counts.content_row": "local to one counts() call",
    "learning_store.py::LearningStore._counts.dependency_item": "local to one counts() call",
    "word_blocks.py::structure_diagnostic.unique_order.visit": "local to one call",
    "ink_renderer.py::_android_path_type": "a platform class, not content",
    "app.py::_HEAP_FROZEN": "whether the start-up heap was frozen, not content",
    "app_clock.py::_provider": "the clock, not content",
    "learning_evidence.py::_REQUIRED_OBJECTS": "names of the learner database schema, not content",
    "public_excerpt_fetch.py::_DNS_JOB": "a network lookup in flight, not content",
    "learning_content.py::_DIAGNOSTICS": "rows of the catalogue build at import (diagnostics)",
    "course_prerequisites.py::_SKIPPED": "diagnostics of the last apply",
}

# Module-level content read at import (load_namespace calls outside functions): the catalogue itself, a
# process constant. clear_all does not reload it: a library change takes effect after a restart.
LOADED_AT_IMPORT = {
    "_vocabulary_catalog.py": 1, "course_prerequisites.py": 1, "detail_enrichment.py": 1,
    "exam_grammar_curriculum.py": 1, "example_catalogue.py": 1, "example_expansion_grammar_a.py": 1,
    "example_expansion_grammar_a_tail.py": 1, "example_expansion_grammar_b.py": 1,
    "example_expansion_pronunciation.py": 1, "example_expansion_signatures.py": 1, "example_expansion_words.py": 1,
    "grammar_curriculum.py": 1, "kana_learning_intro.py": 1, "learning_content.py": 1, "learning_questions.py": 5,
    "lesson_diagrams_grammar_a.py": 1, "lesson_diagrams_grammar_b.py": 1, "lesson_diagrams_words_sounds.py": 1,
    "pronunciation_curriculum.py": 1, "question_contexts.py": 1, "question_readings.py": 1,
    "sentence_annotations.py": 1, "structured_lessons_grammar_a.py": 1, "structured_lessons_grammar_b.py": 1,
    "structured_lessons_pronunciation.py": 1, "structured_lessons_words.py": 1, "study_path_catalog.py": 1,
    "usage_grammar_curriculum.py": 1, "vocabulary_lessons.py": 1, "answer_policy/source.py": 1,
}


def _scan(path):
    """(cached functions as (qualified name, marked content_cache), module-level tables, module-level loads)."""
    tree = ast.parse(path.read_text("utf-8"))
    functions, tables = [], []

    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not isinstance(child, ast.ClassDef):
                    decorators = [ast.unparse(decorator) for decorator in child.decorator_list]
                    if any(CACHING.search(decorator) for decorator in decorators):
                        functions.append((".".join([*scope, child.name]), "content_cache" in decorators))
                walk(child, [*scope, child.name])
            else:
                walk(child, scope)
    walk(tree, [])
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and ast.unparse(node.value) in EMPTY_TABLES):
            tables.append(node.targets[0].id)
    # A module-level name a function rebinds (global): a memo however it starts.
    tables.extend(name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names
                  if name not in tables)
    loads = sum(1 for node in tree.body if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and ast.unparse(sub.func).endswith("load_namespace"))
    return functions, tables, loads


def _module_name(key):
    file, name = key.split("::")
    return file[:-3].replace("/", "."), name


def test_every_cache_and_module_table_is_registered_or_exempt_with_a_reason():
    found, marked, loads = {}, {}, {}
    for path in sorted(SOURCE.rglob("*.py")):
        if "resources" in path.relative_to(SOURCE).parts:
            continue
        file = path.relative_to(SOURCE).as_posix()
        functions, tables, count = _scan(path)
        for name, is_marked in functions:
            found[file + "::" + name] = True
            marked[file + "::" + name] = is_marked
        for name in tables:
            found[file + "::" + name] = True
        if count:
            loads[file] = count
    # A new cache must be added to CACHES with its decision, not left to chance.
    assert sorted(found) == sorted(CACHES)
    assert loads == LOADED_AT_IMPORT
    for key, decision in CACHES.items():
        assert decision == REGISTERED or (isinstance(decision, str) and decision.strip()), key
        if key in marked:  # a cached function: registered exactly when it carries @content_cache
            assert marked[key] == (decision == REGISTERED), key
    expected = set()
    for key, decision in CACHES.items():
        if decision == REGISTERED:
            module, name = _module_name(key)
            importlib.import_module("kanamemory." + module)
            expected.add(module + "." + name)
    assert set(content_caches.registered()) == expected


def test_register_rejects_what_it_cannot_clear():
    with pytest.raises(TypeError):
        content_caches.register(None, "x")
    with pytest.raises(TypeError):
        content_caches.content_cache(lambda: None)


def _word(cid, title, reading):
    return {"id": cid, "family": "word", "revision": "synthetic-v1", "title": title, "reading": reading,
            "meaning": "义", "abilities": ["understanding"], "depends_on": []}


def _annotated(text):
    from kanamemory.result_readings import annotate
    return annotate({"target_id": "grammar:synthetic:none", "kind": "grammar_cloze", "ability": "application"}, text)


def test_a_course_patched_in_leaves_no_word_table_behind(monkeypatch, drop_content_caches):
    """The leak, reproduced: without clear_all the table of the course before stays, and its words get no kana."""
    from kanamemory import learning_questions, question_readings, result_readings
    monkeypatch.setattr(learning_content, "_ITEMS", {"word:synthetic:park": _word("word:synthetic:park", "公園", "こうえん")})
    assert "こうえん" in _annotated("公園へ行きます")
    assert question_readings._course_words.cache_info().currsize == 1 and result_readings._TITLES[0] is not None
    monkeypatch.setattr(learning_content, "_ITEMS",
                        {"word:synthetic:school": _word("word:synthetic:school", "学校", "がっこう")})
    assert _annotated("学校へ行きます") == "学校へ行きます"  # the table of the course before: no kana
    content_caches.clear_all()
    assert question_readings._course_words.cache_info().currsize == 0
    assert learning_questions._word_pool.cache_info().currsize == 0 and result_readings._TITLES == [None, None]
    assert "がっこう" in _annotated("学校へ行きます")


def test_a_catalogue_with_another_token_drops_every_content_cache(monkeypatch):
    calls = []
    monkeypatch.setitem(content_caches._CLEARS, "test.probe", lambda: calls.append(1))
    monkeypatch.setattr(content_pack, "_effective", [])  # monkeypatch restores the process's own afterwards
    monkeypatch.setattr(content_pack, "_built_token", [])
    token = content_pack.effective_catalogue().token  # the first catalogue: nothing to drop
    assert calls == [] and content_pack._built_token == [token]
    monkeypatch.setattr(content_pack, "_effective", [])
    content_pack.effective_catalogue()  # built again with the same token: nothing is dropped
    assert calls == []
    monkeypatch.setattr(content_pack, "_effective", [])
    monkeypatch.setattr(content_pack, "_built_token", ["another token"])
    assert content_pack.effective_catalogue().token == token
    assert calls == [1] and content_pack._built_token == [token]


def test_clear_cache_drops_every_content_cache(monkeypatch):
    calls = []
    monkeypatch.setitem(content_caches._CLEARS, "test.probe", lambda: calls.append(1))
    content_pack.clear_cache()
    assert calls == [1]


def test_single_flight_cleared_from_inside_its_computation_neither_waits_nor_keeps_it():
    calls = []

    @_single_flight
    def computed():
        calls.append(1)
        if len(calls) == 1:
            computed.cache_clear()  # e.g. a catalogue rebuilt while it reads (content_caches.clear_all)
        return len(calls)
    assert computed() == 1
    assert computed() == 2 and computed() == 2


def test_single_flight_cleared_from_another_thread_waits_and_drops_the_running_computation():
    started, release, cleared = threading.Event(), threading.Event(), threading.Event()
    calls = []

    @_single_flight
    def computed():
        calls.append(1)
        started.set()
        release.wait(5)
        return len(calls)
    worker = threading.Thread(target=computed)
    worker.start()
    assert started.wait(5)
    clearer = threading.Thread(target=lambda: (computed.cache_clear(), cleared.set()))
    clearer.start()
    assert not cleared.wait(0.2)  # it waits for the computation
    release.set()
    worker.join(5)
    clearer.join(5)
    assert cleared.is_set() and computed() == 2
