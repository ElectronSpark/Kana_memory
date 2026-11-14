"""Release by reviews and the stem-gloss tier (design 11 section 18, Q5-Q8). Synthetic data only."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kanamemory import content_gaps, content_pack, learning_content, learning_practice_policy as policy
from kanamemory import learning_questions as questions
from kanamemory.arrangement.model import Lane, StateRef
from kanamemory.arrangement.sources.deck_course import CourseDeckLane
from kanamemory.course_planning import enrolled_ids_reader, iter_question_plans
from kanamemory.learning_practice_policy import (_candidate_questions, guided_practice_sequence,
                                                 practice_candidates, review_count, select_practice_plan)
from kanamemory.learning_questions import build_learning_question, template_facts, template_fingerprint
from kanamemory.material_review import item_fingerprint
from kanamemory.route_mix import sources
from kanamemory.route_mix.params import ALGORITHM_VERSION, MixParams
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.learning_fixtures import learning_app  # noqa: F401
from support.learning_practice_policy_support import CID, lesson, observed, raw
from support.multi_cloze_support import question as multi_gap_question
from support.question_dependency_support import GRAMMAR_ID, bare_lesson, grammar, install, namespace

W1, W2, W3 = "word:synthetic:one", "word:synthetic:two", "word:synthetic:three"
RULE, SIMPLE = "grammar:synthetic:later", "grammar:synthetic:no"
OOV = "鍵"
OOV_TOKEN = questions.OOV_TOKEN_PREFIX + OOV


def needs(words=(), grammar=(), oov=(), stem_words=(), stem_oov=()):
    """One dependency entry, the stem-only fields included; empty fields are omitted."""
    return {name: list(values) for name, values in (("words", words), ("grammar", grammar), ("oov", oov),
                                                    ("stem_words", stem_words), ("stem_oov", stem_oov)) if values}


GLOSSED = needs(words=[W1], stem_words=[W1])  # one unmet, glossed stem-only word


class Reader:
    """A caller's enrolled-ids reader that counts its reads."""

    def __init__(self, ids=()):
        self.ids, self.calls = ids, 0

    def __call__(self):
        self.calls += 1
        return self.ids


def gloss(reader=None):
    return {"gloss_allowance": True, "enrolled_ids": reader or Reader()}


def declared(monkeypatch, *requirements, item=None):
    """The bare policy lesson (or ``item``) with its grammar_cloze templates 0.. declared as given (None: undeclared)."""
    item = item or bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace(*((CID, "grammar_cloze", cloze[index], value)
                                     for index, value in enumerate(requirements) if value is not None)))
    return item


def candidates(item, known=(), **state):
    return [(c["variant"], c.get("stem_gloss"), c.get("early_rules"), c.get("relaxed"), bool(c.get("top_up")))
            for c in _candidate_questions(item, "application", ["grammar_cloze"],
                                          {"known_background_ids": set(known), **state})]


def met(variant, top_up=False):
    return (variant, None, None, None, top_up)


def glossed(variant, *ids):
    return (variant, sorted(ids), None, None, False)


def relaxed(variant, count):
    return (variant, None, None, count, False)


@pytest.fixture
def gaps(monkeypatch):
    recorded = []
    monkeypatch.setattr(content_gaps, "record_gap", lambda *args: recorded.append(args))
    return recorded


# ------------------------------------------------------------------ the stem facts lookup

def test_template_facts_give_the_requirements_and_the_glossed_stem_only_tokens(monkeypatch):
    item = bare_lesson()
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0],
                                    needs(words=[W1, W2], grammar=[RULE], oov=[OOV, "錠"],
                                          stem_words=[W1], stem_oov=[OOV])),
                                   (CID, "grammar_cloze", cloze[1], needs(words=[W1]))))
    facts = template_facts(item, "grammar_cloze", cloze[0])
    assert facts.requires == {W1, W2, RULE, OOV_TOKEN, questions.OOV_TOKEN_PREFIX + "錠"}
    assert facts.stem_only == {W1, OOV_TOKEN}
    assert facts.requires == questions.template_requires(item, "grammar_cloze", cloze[0])
    # Without stem fields, undeclared, and for generated sound kinds: no stem facts.
    assert template_facts(item, "grammar_cloze", cloze[1]) == (frozenset({W1}), frozenset())
    assert template_facts(item, "grammar_cloze", cloze[2]) == (None, frozenset())
    assert template_facts(item, "vocab_reading_choice", {}).stem_only == frozenset()


def test_the_entry_is_looked_up_once_per_template(monkeypatch):
    item = declared(monkeypatch, GLOSSED, GLOSSED, GLOSSED)
    calls = []
    original = questions._template_fingerprint
    monkeypatch.setattr(questions, "_template_fingerprint", lambda value: calls.append(1) or original(value))
    validated = []
    check = questions.validate_question_dependency_requirements
    monkeypatch.setattr(questions, "validate_question_dependency_requirements",
                        lambda value, **kwargs: validated.append(1) or check(value, **kwargs))
    list(_candidate_questions(item, "application", ["grammar_cloze"], {"known_background_ids": set(), **gloss()}))
    assert len(validated) == 3
    assert len(calls) == 3  # no retirement table here: one fingerprint per template, for its entry


# ------------------------------------------------------------------ Q5 release by reviews

def leveled(*levels):
    """The policy lesson whose grammar_cloze templates carry these release levels (None: no key)."""
    item = lesson()
    for template, level in zip(item["question_templates"]["grammar_cloze"], levels):
        if level is not None:
            template["practice"]["opens_after_reviews"] = level
    return item


@pytest.mark.parametrize("count, served", [
    (None, [0]), (0, [0]), (1, [0, 1]), (2, [0, 1]), (3, [0, 1, 2]), (40, [0, 1, 2]),
    (True, [0]), (False, [0]), (-1, [0]), (2.5, [0]), ("3", [0])])
def test_a_template_is_offered_once_its_unit_has_its_reviews(monkeypatch, count, served):
    install(monkeypatch, namespace())
    item = leveled(0, 1, 3)
    state = {"known_background_ids": set()} if count is None else {"known_background_ids": set(),
                                                                    "review_count": count}
    assert [c["variant"] for c in _candidate_questions(item, "application", ["grammar_cloze"], state)] == served


def test_the_review_count_reads_only_a_plain_non_negative_int():
    assert [review_count(state) for state in ({}, {"review_count": 4}, {"review_count": 0},
                                              {"review_count": True}, {"review_count": None},
                                              {"review_count": -2}, {"review_count": 1.0})] == [0, 4, 0, 0, 0, 0, 0]


def test_a_template_not_yet_released_is_no_gap_and_never_relaxed(monkeypatch, gaps):
    item = leveled(2, None, 2)
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace((CID, "grammar_cloze", cloze[0], needs(words=[W1])),
                                   (CID, "grammar_cloze", cloze[1], needs(words=[W2]))))
    assert candidates(item) == [relaxed(1, 1)]
    assert gaps == [(CID, "application", 1, ["grammar_cloze"])]  # W1 of the held template is not missing
    gaps.clear()
    # Once released, the met template is served and the relaxed one is not needed.
    assert candidates(item, review_count=2) == [met(2)]
    assert gaps == []
    # Paths that give no count (guides, checks, the tutor) see level 0 only.
    assert [c["variant"] for c in practice_candidates(item, ["grammar_cloze"])] == [1]


def test_only_leveled_templates_yield_nothing_at_first(monkeypatch, gaps):
    item = leveled(1, 1, 2)
    cloze = item["question_templates"]["grammar_cloze"]
    install(monkeypatch, namespace(*((CID, "grammar_cloze", raw_, needs(words=[W1])) for raw_ in cloze)))
    assert candidates(item, **gloss()) == [] and candidates(item, early_allowance=True) == []
    assert gaps == []
    assert candidates(item, review_count=1) == [relaxed(0, 1), relaxed(1, 1)]


# ------------------------------------------------------------------ Q6 the stem-gloss tier: eligibility

def single(monkeypatch, requirement, known=(), **state):
    """Candidates of a lesson with one declared grammar_cloze template."""
    item = bare_lesson()
    item["question_templates"]["grammar_cloze"] = item["question_templates"]["grammar_cloze"][:1]
    declared(monkeypatch, requirement, item=item)
    return candidates(item, known, **state)


def test_one_or_two_unmet_glossed_stem_words_are_served_with_their_gloss(monkeypatch):
    assert single(monkeypatch, GLOSSED, **gloss()) == [glossed(0, W1)]
    both = needs(words=[W1], oov=[OOV], stem_words=[W1], stem_oov=[OOV])
    assert single(monkeypatch, both, **gloss()) == [glossed(0, W1, OOV)]  # oov as its bare surface
    two = needs(words=[W1, W2], stem_words=[W1, W2])
    assert single(monkeypatch, two, **gloss()) == [glossed(0, W1, W2)]
    assert single(monkeypatch, two, known={W2}, **gloss()) == [glossed(0, W1)]  # only the unmet one
    # Fully met: an ordinary task, whatever the stem facts say.
    assert single(monkeypatch, two, known={W1, W2}, **gloss()) == [met(0)]


def test_anything_else_unmet_leaves_the_template_to_the_relaxed_pass(monkeypatch):
    three = needs(words=[W1, W2, W3], stem_words=[W1, W2, W3])
    assert single(monkeypatch, three, **gloss()) == [relaxed(0, 3)]
    rule = needs(words=[W1], grammar=[RULE], stem_words=[W1])
    assert single(monkeypatch, rule, **gloss()) == [relaxed(0, 2)]
    unmarked = needs(words=[W1, W2], stem_words=[W1])
    assert single(monkeypatch, unmarked, **gloss()) == [relaxed(0, 2)]
    unmarked_oov = needs(oov=[OOV, "錠"], stem_oov=[OOV])
    assert single(monkeypatch, unmarked_oov, **gloss()) == [relaxed(0, 2)]
    assert single(monkeypatch, needs(words=[W1]), **gloss()) == [relaxed(0, 1)]  # no stem facts at all


def test_a_word_the_learner_has_enrolled_is_not_glossed(monkeypatch):
    assert single(monkeypatch, GLOSSED, **gloss(Reader((W1, W3)))) == [relaxed(0, 1)]
    both = needs(words=[W1], oov=[OOV], stem_words=[W1], stem_oov=[OOV])
    assert single(monkeypatch, both, **gloss(Reader([W1]))) == [relaxed(0, 2)]
    assert single(monkeypatch, both, **gloss(Reader({W3}))) == [glossed(0, W1, OOV)]
    # A reader whose answer is not a collection of ids glosses no word (an oov surface needs no reader).
    assert single(monkeypatch, GLOSSED, **gloss(Reader(None))) == [relaxed(0, 1)]
    assert single(monkeypatch, needs(oov=[OOV], stem_oov=[OOV]), **gloss(Reader(None))) == [glossed(0, OOV)]


def test_without_the_allowance_or_a_reader_the_tier_is_off(monkeypatch):
    assert single(monkeypatch, GLOSSED) == [relaxed(0, 1)]
    assert single(monkeypatch, GLOSSED, enrolled_ids=Reader()) == [relaxed(0, 1)]
    assert single(monkeypatch, GLOSSED, gloss_allowance=True) == [relaxed(0, 1)]
    assert single(monkeypatch, GLOSSED, gloss_allowance="yes", enrolled_ids=Reader()) == [relaxed(0, 1)]
    assert single(monkeypatch, GLOSSED, gloss_allowance=True, enrolled_ids=(W2,)) == [relaxed(0, 1)]


def test_a_multi_gap_question_is_never_glossed(monkeypatch):
    item = bare_lesson()
    gap = {key: value for key, value in multi_gap_question().items()
           if key not in {"id", "target_id", "ability", "kind", "family"}}
    item["question_templates"]["grammar_cloze"] = [gap]
    declared(monkeypatch, needs(words=[W1], stem_words=[W1]), item=item)
    assert candidates(item, **gloss()) == [relaxed(0, 1)]


# ------------------------------------------------------------------ Q6 placement

def test_glossed_tasks_come_after_met_tasks_and_top_ups(monkeypatch, gaps):
    item = declared(monkeypatch, needs(), None, GLOSSED)
    assert candidates(item) == [met(0), met(1, top_up=True)]
    assert candidates(item, **gloss()) == [met(0), met(1, top_up=True), glossed(2, W1)]
    # Undeclared fallbacks alone are not topped up; the glossed task still follows them.
    item = declared(monkeypatch, None, None, GLOSSED)
    assert candidates(item, **gloss()) == [met(0), met(1), glossed(2, W1)]
    assert gaps == []


def test_without_a_met_task_the_glossed_ones_come_instead_of_the_relaxed_pass(monkeypatch, gaps):
    item = declared(monkeypatch, GLOSSED, needs(words=[W2]), needs(words=[W2, W3]))
    assert candidates(item) == [relaxed(0, 1), relaxed(1, 1)]
    assert candidates(item, **gloss()) == [glossed(0, W1)]
    # The content-gap record keeps its condition: nothing fully met.
    assert len(gaps) == 2 and gaps[0] == gaps[1] == (CID, "application", 3, ["grammar_cloze"])


@pytest.fixture
def rules(monkeypatch):
    lessons = {SIMPLE: {"id": SIMPLE, "family": "grammar", "title": "の：所属与名词修饰", "level": "入门",
                        "revision": "1", "instruction": {"summary": "用前一名词限定后一名词", "rule": "名词A＋の＋名词B。"}},
               RULE: {"id": RULE, "family": "grammar", "title": "も：追加同类信息", "level": "基础", "revision": "1",
                      "instruction": {"summary": "概要", "rule": "规则说明。"}}}
    monkeypatch.setattr(learning_content, "get_learning_item", lambda cid: deepcopy(lessons.get(cid)))


def test_glossed_tasks_follow_the_opening_allowance_and_never_overlap_it(monkeypatch, rules):
    item = declared(monkeypatch, needs(grammar=[SIMPLE]), GLOSSED, needs())
    early = (0, None, [SIMPLE], None, False)
    assert candidates(item, early_allowance=True, **gloss()) == [met(2), early, glossed(1, W1)]
    item = declared(monkeypatch, needs(grammar=[SIMPLE]), GLOSSED, needs(words=[W2]))
    assert candidates(item, early_allowance=True, **gloss()) == [early, glossed(1, W1)]
    # A simple rule beside a glossed word is neither: it goes to the relaxed pass as before.
    mixed = needs(words=[W1], grammar=[SIMPLE], stem_words=[W1])
    assert single(monkeypatch, mixed, early_allowance=True, **gloss()) == [relaxed(0, 2)]


def test_a_glossed_task_never_repeats_a_stimulus_already_served(monkeypatch, rules):
    item = bare_lesson()
    window, door = item["question_templates"]["grammar_cloze"][:2]
    # Other templates with the same stimulus (prompt, task, answer).
    copies = [dict(window, feedback="另一个说明。"), dict(window, feedback="第三个说明。")]
    item["question_templates"]["grammar_cloze"] = [window, *copies]
    declared(monkeypatch, needs(), GLOSSED, GLOSSED, item=item)
    assert candidates(item, **gloss()) == [met(0)]
    declared(monkeypatch, GLOSSED, GLOSSED, needs(words=[W2]), item=item)
    assert candidates(item, **gloss()) == [glossed(0, W1)]
    # Nor one the opening allowance already added.
    item["question_templates"]["grammar_cloze"] = [window, copies[0], door]
    declared(monkeypatch, needs(grammar=[SIMPLE]), GLOSSED, needs(), item=item)
    assert candidates(item, early_allowance=True, **gloss()) == [met(2), (0, None, [SIMPLE], None, False)]


def test_admit_sees_the_glossed_descriptors(monkeypatch):
    item = declared(monkeypatch, GLOSSED, needs(words=[W2]), needs(words=[W2, W3]))
    seen = []
    served = list(_candidate_questions(item, "application", ["grammar_cloze"], {"known_background_ids": set(),
                                                                               **gloss()},
                                       admit=lambda descriptor, template: seen.append(descriptor.get("stem_gloss"))
                                       or not descriptor.get("stem_gloss")))
    assert [W1] in seen and [(c["variant"], c.get("relaxed")) for c in served] == [(1, 1)]


def test_the_enrolled_ids_are_read_once_and_only_for_an_eligible_word(monkeypatch):
    reader = Reader()
    item = declared(monkeypatch, GLOSSED, needs(words=[W2], stem_words=[W2]), GLOSSED)
    assert [c[1] for c in candidates(item, **gloss(reader))] == [[W1], [W2], [W1]]
    assert reader.calls == 1
    for requirement in (needs(words=[W1]), needs(oov=[OOV], stem_oov=[OOV]), needs(words=[W1, W2, W3],
                                                                                    stem_words=[W1, W2, W3])):
        reader = Reader()
        single(monkeypatch, requirement, **gloss(reader))
        assert reader.calls == 0
    reader = Reader()
    single(monkeypatch, GLOSSED, known={W1}, **gloss(reader))
    assert reader.calls == 0  # fully met: nothing to gloss


def test_selection_ranks_a_glossed_task_after_an_equal_one_without_a_gloss(monkeypatch):
    item = declared(monkeypatch, GLOSSED, needs(), needs(words=[W2]))
    state = {"known_background_ids": set(), **gloss()}
    assert candidates(item, **gloss()) == [met(1), glossed(0, W1)]
    assert select_practice_plan(item, "application", ["grammar_cloze"], state, [])["variant"] == 1
    # The tie-break itself: at this count the rotation alone would put the glossed task first.
    assert select_practice_plan(item, "application", ["grammar_cloze"], {**state, "attempt_count": 1},
                                [])["variant"] == 1
    history = [observed(item, "grammar_cloze", 1)]
    assert select_practice_plan(item, "application", ["grammar_cloze"], state, history)["variant"] == 0
    assert select_practice_plan(item, "application", ["grammar_cloze"],
                                {"known_background_ids": set()}, history)["variant"] == 1


def test_guided_paths_never_take_a_glossed_task(monkeypatch):
    item = lesson()
    rows = [(CID, kind, template, GLOSSED) for kind, templates in item["question_templates"].items()
            for template in templates]
    install(monkeypatch, namespace(*rows))
    kinds = list(item["question_templates"])
    assert guided_practice_sequence(item, kinds) == []
    assert all(c.get("relaxed") and not c.get("stem_gloss") for c in practice_candidates(item, kinds))
    assert len(guided_practice_sequence(item, kinds, known_background_ids=(W1,))) == 2  # the lesson can guide


# ------------------------------------------------------------------ who sets the allowance

def planned_states(monkeypatch, item, queries, *, unit=None, unit_states=None):
    states = []
    monkeypatch.setattr(policy, "select_practice_plan",
                        lambda item, ability, kinds, state, history, **kwargs: states.append(dict(state)))
    learning = SimpleNamespace(get_unit=lambda cid, ability: dict(unit or {"attempt_count": 0}),
                               practice_history=lambda cid, ability: [])
    list(iter_question_plans(item, enabled_kinds=["grammar_meaning", "grammar_cloze"], queries=queries,
                             learning_store=learning, system_japanese_input=False, unit_states=unit_states))
    return states


def queries(*, enrolled=None):
    calls = []
    value = SimpleNamespace(pending_abilities=lambda cid: set(), instruction_ready_ids=lambda: set())
    if enrolled is not None:
        value.course_ids = lambda: calls.append(1) or tuple(enrolled)
    return value, calls


def test_the_formal_course_plan_allows_the_tier_and_passes_the_review_count(monkeypatch):
    item = grammar()
    reader, calls = queries(enrolled=[W3])
    states = planned_states(monkeypatch, item, reader, unit={"attempt_count": 2, "review_count": 3})
    assert len(states) == 2  # understanding and application
    assert all(state["gloss_allowance"] is True and state["review_count"] == 3 for state in states)
    assert states[0]["enrolled_ids"] is states[1]["enrolled_ids"]  # one reader for the whole lesson
    assert calls == []  # nothing read yet: only an eligible template reads it
    assert states[0]["enrolled_ids"]() == (W3,) and states[1]["enrolled_ids"]() == (W3,) and calls == [1]
    # A caller-owned projection of the units (home previews) carries the count as the unit rows do.
    states = planned_states(monkeypatch, item, reader, unit_states={
        "understanding": {"attempt_count": 0, "review_count": 5}, "application": {"attempt_count": 1}})
    assert [state.get("review_count") for state in states] == [5, None]
    # A reader without course_ids (the practice deck's one-ability reader) leaves the tier off.
    plain, _ = queries()
    assert enrolled_ids_reader(plain) is None
    assert all("gloss_allowance" not in state and "enrolled_ids" not in state
               for state in planned_states(monkeypatch, item, plain))


def test_one_planned_lesson_reads_the_enrolled_ids_once_for_all_its_abilities(monkeypatch):
    item = grammar()
    rows = [(GRAMMAR_ID, kind, template, GLOSSED) for kind, templates in item["question_templates"].items()
            for template in templates]
    install(monkeypatch, namespace(*rows))
    reader, calls = queries(enrolled=[W3])
    learning = SimpleNamespace(get_unit=lambda cid, ability: {"attempt_count": 0},
                               practice_history=lambda cid, ability: [])
    plans = list(iter_question_plans(item, enabled_kinds=["grammar_meaning", "grammar_cloze"], queries=reader,
                                     learning_store=learning, system_japanese_input=False))
    assert [plan.question["ability"] for plan in plans] == ["understanding", "application"]
    assert calls == [1]


def test_the_practice_deck_plans_without_the_tier(monkeypatch):
    item = grammar()
    reader, calls = queries(enrolled=[])
    states = []
    monkeypatch.setattr(policy, "select_practice_plan",
                        lambda item, ability, kinds, state, history, **kwargs: states.append(dict(state)))
    learning = SimpleNamespace(get_item=lambda cid: deepcopy(item), get_unit=lambda cid, ability: {"attempt_count": 0,
                                                                                                    "review_count": 2},
                               practice_history=lambda cid, ability: [])
    lane = CourseDeckLane(SimpleNamespace(queries=reader), learning, ["grammar_meaning", "grammar_cloze"])
    assert lane.build(StateRef(Lane.COURSE, GRAMMAR_ID, "understanding"), None) is None
    assert len(states) == 1 and states[0]["review_count"] == 2
    assert "gloss_allowance" not in states[0] and calls == []


# ------------------------------------------------------------------ Q7 the course question page

def test_the_course_question_page_shows_the_reviewed_gloss_of_a_glossed_word(monkeypatch, learning_app):
    item = lesson()
    extension = raw("park")
    start = extension["prompt"].index("窓")
    gloss_row = {"start": start, "end": start + 1, "surface": "窓", "meaning": "窗户"}
    sha = template_fingerprint(extension)
    monkeypatch.setattr(questions, "_PRACTICE", {"schema_version": 1, "items": {CID: {
        "item_fingerprint": item_fingerprint(item), "item_revision": item["revision"],
        "templates": {"grammar_cloze": [extension]}, "contexts": {sha: {"background_glosses": [gloss_row]}}}}})
    install(monkeypatch, namespace((CID, "grammar_cloze", extension, needs(oov=["窓"], stem_oov=["窓"]))))
    others = {"known_background_ids": set(), **gloss()}
    served = [(c["variant"], c.get("stem_gloss")) for c in _candidate_questions(
        item, "application", ["grammar_cloze"], others)]
    assert served[-1] == (3, ["窓"])  # the extension template, after the lesson's own met tasks
    question = build_learning_question(item, "grammar_cloze", variant=3)
    assert question["practice_source"]["template_sha256"] == sha
    rows = [row.text for row in learning_app._learning_context_support(question, set())
            if isinstance(getattr(row, "text", None), str)]
    assert "窓：窗户" in rows


# ------------------------------------------------------------------ Q8 readiness

def test_readiness_counts_only_templates_open_at_introduction(monkeypatch):
    def template(prompt, **extra):
        return {"prompt": prompt, "answer": "答", **extra}
    open_, explicit = template("a"), template("b", practice={"background_ids": [], "opens_after_reviews": 0})
    later = template("c", practice={"background_ids": ["word:bg"], "opens_after_reviews": 2})
    declared_later = template("d", practice={"opens_after_reviews": 1})
    glossed_oov = template("e")
    item = {"id": "word:main", "family": "word", "title": "本", "reading": "ほん", "revision": "t1",
            "abilities": ["understanding"],
            "question_templates": {"vocab_meaning": [open_, explicit, later, declared_later, glossed_oov]}}
    sha = template_fingerprint
    entry = {"vocab_meaning": {sha(open_): {}, sha(declared_later): {"words": ["word:x"]},
                               sha(glossed_oov): {"oov": ["未知"], "stem_oov": ["未知"]}}}
    monkeypatch.setattr(questions, "_PRACTICE", {})
    namespaces = {content_pack.QUESTION_DEPENDENCIES: {"schema_version": 1, "items": {"word:main": entry}}}
    readiness = sources.read_readiness(["word:main"], {"word:main": item},
                                       load=lambda name, default: namespaces.get(name, default))
    assert readiness.abilities("word:main")["understanding"] == tuple(sorted([(sha(open_), ()), (sha(explicit), ())]))


def test_a_plan_of_the_old_readiness_is_not_reused():
    assert ALGORITHM_VERSION == 5 and MixParams().key()["algorithm"] == 5
