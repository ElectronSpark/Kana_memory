"""The author's optional template fields become exactly what the App reads and shows (design 11 section 18).

Synthetic lessons and sentences only.
"""
from copy import deepcopy

import pytest

from kanamemory import learning_content, learning_questions
from kanamemory.learning_questions import build_learning_question, template_fingerprint, validate_practice
from kanamemory.question_support import _practice_background_context, learning_question_display
from practice_authoring_fields import (AuthoringFieldError, apply_pacing, check_scenario, context_row, gloss_rows,
                                       pacing_keys, scenario_ids, scenario_payload, split_fields)
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.learning_fixtures import learning_app  # noqa: F401

CID = "grammar:synthetic:place_de"
HALL = "word:synthetic:hall"
KIND = "grammar_cloze"
PROMPT = "公民館（　）囲碁を打ちます。"


def template(**changes):
    raw = {"prompt": PROMPT, "task": "填空", "input_mode": "choice", "choices": ["で", "を", "へ"], "answer": "で",
           "safe": False, "strict": True, "feedback": "で表示动作的场所。",
           "practice": {"schema_version": 1, "task_family": "synthetic_place", "context_id": "context:synthetic",
                        "scaffold": "recognition", "target_spans": [{"start": 3, "end": 6, "quote": "（　）"}],
                        "background_ids": [], "reviewed": True}}
    raw.update(changes)
    return raw


HALL_GLOSS = {"surface": "公民館", "reading": "こうみんかん", "meaning": "社区活动中心", "lesson_id": HALL}
GO_GLOSS = {"surface": "囲碁", "reading": "いご", "meaning": "围棋"}


def rows(raw=None, entries=(HALL_GLOSS, GO_GLOSS), **options):
    return gloss_rows(raw or template(), KIND, CID, [deepcopy(e) for e in entries], key="k1", **options)


# ------------------------------------------------------------------ pacing and scenarios

def test_release_level_zero_is_not_written_and_other_levels_are():
    assert pacing_keys({"opens_after_reviews": 0}, key="k") == {}
    assert pacing_keys({"opens_after_reviews": 4, "scenario_id": "station_lost"}, key="k") == {
        "opens_after_reviews": 4, "scenario_id": "station_lost"}
    raw = apply_pacing(template(), {"opens_after_reviews": 0}, key="k")
    assert raw == template()
    paced = apply_pacing(template(), {"opens_after_reviews": 6, "scenario_id": "station_lost"}, key="k")
    assert paced["practice"]["opens_after_reviews"] == 6 and paced["practice"]["scenario_id"] == "station_lost"
    assert validate_practice(paced, target_id=CID)["opens_after_reviews"] == 6
    # The pacing keys are part of the template, so they change its fingerprint; a level of 0 does not.
    assert template_fingerprint(paced) != template_fingerprint(template())
    assert template_fingerprint(raw) == template_fingerprint(template())


@pytest.mark.parametrize("fields", [{"opens_after_reviews": True}, {"opens_after_reviews": -1},
                                    {"opens_after_reviews": 41}, {"opens_after_reviews": "2"},
                                    {"scenario_id": ""}, {"scenario_id": "Station"}, {"scenario_id": 3},
                                    {"scenario_id": "a" * 65}])
def test_invalid_pacing_is_refused_with_the_template_key(fields):
    with pytest.raises(AuthoringFieldError, match="^k7: "):
        pacing_keys(fields, key="k7")


def test_unknown_fields_and_a_second_pacing_source_are_refused():
    with pytest.raises(AuthoringFieldError, match="k: unknown authoring field"):
        split_fields({"opens_after_review": 2}, key="k")
    with pytest.raises(AuthoringFieldError, match="glosses must be a list"):
        split_fields({"glosses": {}}, key="k")
    assert split_fields({"glosses": [GO_GLOSS], "scenario_id": "x"}, key="k") == ({"scenario_id": "x"}, [GO_GLOSS])
    raw = template()
    raw["practice"]["opens_after_reviews"] = 2
    with pytest.raises(AuthoringFieldError, match="already carries opens_after_reviews"):
        apply_pacing(raw, {}, key="k")


def test_scenario_catalogue_shape_and_membership():
    payload = {"schema_version": 1, "scenarios": {"station_lost": {"title": "车站问路", "summary": "在车站找出口。"}}}
    ids = scenario_ids(payload)
    assert ids == {"station_lost"}
    from kanamemory.content_pack import namespace_document
    assert scenario_payload(payload) == payload
    assert scenario_payload(namespace_document("question_scenarios", payload)) == payload
    with pytest.raises(AuthoringFieldError, match="not the question_scenarios namespace"):
        scenario_payload(namespace_document("varied_practice", payload))
    check_scenario({"scenario_id": "station_lost"}, ids, key="k")
    check_scenario({"scenario_id": "anything"}, None, key="k")
    with pytest.raises(AuthoringFieldError, match="k: scenario_id 'market' is not in question_scenarios"):
        check_scenario({"scenario_id": "market"}, ids, key="k")
    for damaged in ({**payload, "notes": 1}, {"schema_version": 2, "scenarios": payload["scenarios"]},
                    {"schema_version": 1, "scenarios": {"Bad": {"title": "a", "summary": "b"}}},
                    {"schema_version": 1, "scenarios": {"ok": {"title": "a"}}},
                    {"schema_version": 1, "scenarios": {"ok": {"title": " ", "summary": "b"}}}):
        with pytest.raises(AuthoringFieldError):
            scenario_ids(damaged)


# ------------------------------------------------------------------ glosses

def test_gloss_entries_become_stored_rows_in_prompt_order():
    result = rows(entries=(GO_GLOSS, HALL_GLOSS), known_items={CID, HALL})
    assert result == [
        {"start": 0, "end": 3, "surface": "公民館", "reading": "こうみんかん", "meaning": "社区活动中心", "target_id": HALL},
        {"start": 6, "end": 8, "surface": "囲碁", "reading": "いご", "meaning": "围棋"}]
    assert context_row(result) == {"background_glosses": result}
    assert rows(entries=()) == []


def test_a_repeated_surface_needs_its_start():
    raw = template(prompt="囲碁（　）囲碁を打ちます。")
    raw["practice"]["target_spans"] = [{"start": 2, "end": 5, "quote": "（　）"}]
    with pytest.raises(AuthoringFieldError, match="occurs 2 times"):
        rows(raw, entries=(GO_GLOSS,))
    assert rows(raw, entries=({**GO_GLOSS, "start": 5},))[0]["start"] == 5
    with pytest.raises(AuthoringFieldError, match="does not stand in the prompt at 1"):
        rows(raw, entries=({**GO_GLOSS, "start": 1},))


def _changed(name):
    raw, entries, options = template(), [deepcopy(HALL_GLOSS), deepcopy(GO_GLOSS)], {}
    if name == "incomplete":
        del entries[1]["meaning"]
    elif name == "unknown key":
        entries[1]["note"] = "x"
    elif name == "absent surface":
        entries[1]["surface"] = "将棋"
    elif name == "overlapping glosses":
        entries.append({"surface": "民館", "reading": "みんかん", "meaning": "民间会馆", "start": 1})
    elif name == "target span":
        raw["practice"]["target_spans"] = [{"start": 3, "end": 8, "quote": "（　）囲碁"}]
    elif name == "answer":
        raw.pop("choices")
        raw.update(answer="囲碁を", input_mode="text")
        raw["practice"]["scaffold"] = "retrieval"
    elif name == "accepted answer":
        raw.update(accepted_answers=["で", "囲碁で"])
    elif name == "option":
        raw.update(choices=["で", "を", "囲碁"])
    elif name == "meaning reveals the answer":
        entries[1]["meaning"] = "在で"
    elif name == "own target":
        entries[0]["lesson_id"] = CID.replace("grammar:", "word:")
        options["target"] = CID.replace("grammar:", "word:")
    elif name == "unknown lesson":
        options["known_items"] = {CID}
    elif name == "not a word lesson":
        entries[0]["lesson_id"] = "grammar:synthetic:other"
    elif name == "multi-cloze":
        raw.update(input_mode="multi_cloze", multi_cloze={"slots": []})
    elif name == "closed kind":
        options["kind"] = "vocab_reading"
    elif name == "reading not kana":
        entries[1]["reading"] = "igo"
    elif name == "meaning repeats the surface":
        entries[1]["meaning"] = "囲碁"
    elif name == "existing gloss":
        options["existing"] = [{"start": 6, "end": 8, "surface": "囲碁", "meaning": "围棋"}]
    return raw, entries, options


REFUSALS = ["incomplete", "unknown key", "absent surface", "overlapping glosses", "target span", "answer",
            "accepted answer", "option", "meaning reveals the answer", "own target", "unknown lesson",
            "not a word lesson", "multi-cloze", "closed kind", "reading not kana", "meaning repeats the surface",
            "existing gloss"]


@pytest.mark.parametrize("name", REFUSALS)
def test_every_unsafe_or_incomplete_gloss_is_refused_with_the_template_key(name):
    raw, entries, options = _changed(name)
    kind, target = options.pop("kind", KIND), options.pop("target", CID)
    with pytest.raises(AuthoringFieldError, match="^slot-9: "):
        gloss_rows(raw, kind, target, entries, key="slot-9", **options)


# ------------------------------------------------------------------ the App's own display filter

def _question(raw):
    """The fields of a built question that the App's background-gloss filter reads."""
    return {"target_id": CID, "family": "grammar", "ability": "application", "kind": KIND,
            "prompt": raw["prompt"], "answer": raw["answer"],
            "accepted_answers": list(dict.fromkeys([raw["answer"], *raw.get("accepted_answers", [])])),
            "practice_source": {"template_sha256": "synthetic-test-binding"}, "practice": raw["practice"]}


def _shown(monkeypatch, raw, stored):
    monkeypatch.setattr(learning_questions, "reviewed_practice_context",
                        lambda question: {"background_glosses": deepcopy(stored)})
    return _practice_background_context(_question(raw)).get("background_glosses", [])


def test_the_app_keeps_every_row_the_tool_produces(monkeypatch):
    produced = rows()
    assert _shown(monkeypatch, template(), produced) == produced


@pytest.mark.parametrize("name", ["target span", "answer", "accepted answer", "meaning reveals the answer",
                                  "meaning repeats the surface"])
def test_what_the_tool_refuses_the_app_would_have_dropped_silently(monkeypatch, name):
    raw, entries, _ = _changed(name)
    gloss = next(entry for entry in entries if entry["surface"] == "囲碁")
    start = raw["prompt"].index(gloss["surface"])
    stored = {"start": start, "end": start + len(gloss["surface"]), **gloss}
    assert stored not in _shown(monkeypatch, raw, [stored])


# ------------------------------------------------------------------ through the App: lesson, pack record, page

def _lesson():
    base = template(prompt="図書館（　）本を読みます。")
    del base["practice"]
    return {"id": CID, "family": "grammar", "revision": "synthetic-v1", "title": "场所で",
            "abilities": ["application"], "depends_on": [],
            "instruction": {"rule": "で表示动作的场所。", "example": "公園で遊びます。", "translation": "在公园玩。"},
            "question_templates": {KIND: [base]}}


@pytest.fixture
def glossed_question(monkeypatch, request):
    from kanamemory import learning_conditions, learning_dependencies, question_support, structured_lessons
    from kanamemory.material_review import item_fingerprint
    lesson = _lesson()
    monkeypatch.setattr(learning_content, "_ITEMS", {CID: deepcopy(lesson)})
    raw = apply_pacing(template(), {"scenario_id": "community_hall"}, key="k1")
    validate_practice(raw, target_id=CID)
    record = {"item_fingerprint": item_fingerprint(lesson), "item_revision": lesson["revision"],
              "templates": {KIND: [raw]},
              "contexts": {template_fingerprint(raw): context_row(rows(raw, known_items={CID, HALL}))}}
    monkeypatch.setattr(learning_questions, "_PRACTICE", {"schema_version": 1, "items": {CID: record}})
    for cached in (learning_conditions._reviewed_index, learning_dependencies._bundled_dependency_signature,
                   learning_dependencies._bundled_fingerprint, question_support._background_word_senses,
                   learning_questions._word_pool, structured_lessons.catalogue):
        cached.cache_clear()
        request.addfinalizer(cached.cache_clear)
    question = build_learning_question(lesson, KIND, variant=1)
    assert question["prompt"] == PROMPT and question["practice_source"]["template_sha256"] == template_fingerprint(raw)
    return question


def test_a_built_question_shows_the_stored_glosses(glossed_question):
    display = learning_question_display(glossed_question)
    assert [(row["surface"], row.get("target_id")) for row in display["background_glosses"]] == [
        ("公民館", HALL), ("囲碁", None)]


def test_the_page_shows_a_gloss_while_the_word_is_unknown_and_drops_it_once_known(
        glossed_question, learning_app, monkeypatch):
    from kanamemory import learning_dependencies

    def notes(ready):
        monkeypatch.setattr(learning_dependencies, "independent_ready_ids", lambda learning: set(ready))
        return [row.text for row in learning_app._learning_context_support(glossed_question, set())
                if isinstance(getattr(row, "text", None), str)]
    assert notes(set()) == ["公民館：社区活动中心 · 囲碁：围棋"]
    # The learner's independent review of the lesson word hides its gloss; a word that is no lesson stays glossed.
    assert notes({HALL}) == ["囲碁：围棋"]
