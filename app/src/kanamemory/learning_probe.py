"""Probe-first start of a new course item: the learning store's side (design 02 section 4; 2026-11-21).

probe_rules decides; this module reads the learning records it needs and,
inside a transaction the LearningStore owns, writes what a start writes:

- open_scope_in_transaction: the start of one introduction scope that has no
  profile yet. A probe (the scope's single ability, an untaught lesson, a
  qualifying question): profile ``probe_rules.probe_profile``, stage check,
  the unit ready, the probe question in the unit's queue (the planned one when
  it qualifies, else an exchange with a replacement receipt). Teaching (the
  lesson's very first scope when nothing can be probed, its explanation was
  already shown, or its own page was opened within the last 24 hours, design 12
  N15): ``probe_rules.plain_profile``, stage intro. An ordinary
  first check (a later ability once the lesson was taught; listening):
  ``plain_profile``, stage bridge with the after-teaching spacing.
- GateReader: one dispatch or count pass's view of which introduction rows
  of probing lessons are held back (an earlier probe still open; the spacing
  of three other answers or ten minutes).

No page imports this module; pages call LearningStore.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json

from . import probe_rules
from . import ability_teaching as _teaching

# Checkpoint keys an introduction may carry and still be probed.
_PROBE_KEYS = frozenset({"supports_initial_familiarity", "familiarity_draft", "prerequisite_acknowledged",
                         "prerequisite_acknowledged_at_utc", "prerequisite_evidence", "prerequisite_version"})


def _load(value):
    return json.loads(value) if value else None


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


class LessonView:
    """Read once: every scope of one lesson, its units and its last answer."""

    def __init__(self, learning, content_id: str):
        db = learning._db
        self.content_id = content_id
        row = db.execute("SELECT * FROM learning_content WHERE content_id=?", (content_id,)).fetchone()
        self.row = row
        content = _load(row["content_json"]) if row else {}
        self.track = probe_rules.track(content.get("family"), content.get("domain"))
        state = _teaching.read(db, content_id)
        original = _load(row["checkpoint_json"]) if row else {}
        self.batch_route = original.get("intro_route") == "batch_v1"
        self.scopes = {}   # ability -> (scope or None, stage, checkpoint)
        originals = state["original_abilities"] if state else [r[0] for r in db.execute(
            "SELECT ability FROM learning_unit WHERE content_id=? ORDER BY rowid", (content_id,))]
        for ability in originals:
            self.scopes[ability] = (None, row["stage"], original)
        for ability, value in ((state or {}).get("additions") or {}).items():
            self.scopes[ability] = (ability, value["stage"], json.loads(value["checkpoint_json"]))
        self.checkpoints = [original, *(json.loads(value["checkpoint_json"])
                                        for value in ((state or {}).get("additions") or {}).values())]
        self.units = {unit["ability"]: dict(unit) for unit in db.execute(
            "SELECT * FROM learning_unit WHERE content_id=?", (content_id,))}
        latest = db.execute("""SELECT COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc) AS at
            FROM learning_event e JOIN learning_unit u ON u.unit_id=e.unit_id WHERE u.content_id=?
            ORDER BY julianday(COALESCE(json_extract(e.result_json,'$.finalized_at_utc'),e.occurred_at_utc)) DESC
            LIMIT 1""", (content_id,)).fetchone()
        self.last_answer = _instant(latest["at"]) if latest else None

    @property
    def probing(self) -> bool:
        """Its next abilities open as probes: a lesson started by a probe that was never taught.

        A lesson with no profile at all is probing only for its first start (fresh); one saved
        by an earlier release without a probe keeps its own route (ordinary first checks).
        """
        started = any(probe_rules.is_probe(checkpoint.get("initial_familiarity")) for checkpoint in self.checkpoints)
        return (started or self.fresh) and probe_rules.probing(self.checkpoints, batch_route=self.batch_route)

    @property
    def shown(self) -> bool:
        return probe_rules.explanation_shown(self.checkpoints, batch_route=self.batch_route)

    @property
    def fresh(self) -> bool:
        """No scope of the lesson has a profile yet."""
        return (self.row is not None and self.row["stage"] == "intro"
                and not any(checkpoint.get("initial_familiarity") for checkpoint in self.checkpoints))

    def abilities(self) -> dict:
        """{ability: {"active", "rated"}} for probe_rules."""
        result = {}
        for ability, unit in self.units.items():
            profile = (self.scopes.get(ability, (None, None, {}))[2].get("initial_familiarity") or {})
            result[ability] = {"active": profile.get("route") == "diagnostic" and profile.get("diagnostic_state") == "active"
                               and ability in profile.get("levels", {}),
                               "rated": bool(unit["review_count"])}
        return result

    def other_candidates(self, answered: str) -> tuple[str, ...]:
        return probe_rules.other_probe_candidates(self.track, self.abilities(), answered, probing_now=self.probing)


def next_probe_available(learning, view: LessonView, answered: str, *, enabled_kinds, known,
                         system_japanese: bool) -> bool:
    """Whether another ability of the lesson can be probed now (reads only).

    Its probe is already open (an active, unrated diagnostic), or its scope has
    no profile yet and a qualifying probe exists for it at this moment.
    """
    candidates = view.other_candidates(answered)
    if not candidates:
        return False
    source = None
    abilities = view.abilities()
    for ability in candidates:
        if abilities[ability]["active"]:
            return True
        scope, stage, checkpoint = view.scopes.get(ability, (None, None, {}))
        if checkpoint.get("initial_familiarity") or stage != "intro":
            continue
        if source is None:
            from .personal_content_versions import teaching_source, resolve
            source = resolve(learning.store, teaching_source(_load(view.row["content_json"])))
        kept, planned = qualifying_probe(learning, view.content_id, ability, source, enabled_kinds=enabled_kinds,
                                         known=known, system_japanese=system_japanese)
        if kept is not None or planned is not None:
            return True
    return False


def qualifying_probe(learning, content_id, ability, source, *, enabled_kinds, known, system_japanese: bool):
    """(the queued question id when it qualifies as it stands, else None; a planned probe, else None)."""
    from .learning_store import _unit_id
    from .retention_session import reservation
    db = learning._db
    if reservation(learning.store, _unit_id(content_id, ability)) is not None:
        return None, None
    pending = db.execute("SELECT * FROM learning_question WHERE unit_id=? AND status='pending'",
                         (_unit_id(content_id, ability),)).fetchone()
    if pending is not None and pending_qualifies(learning, source, ability, pending, enabled_kinds=enabled_kinds,
                                                 known=known, system_japanese=system_japanese):
        return pending["question_id"], None
    return None, plan_probe(learning, source, ability, enabled_kinds=enabled_kinds, known=known,
                            system_japanese=system_japanese)


class GateReader:
    """Which pending rows of probing lessons wait (one dispatch or count pass; reads only)."""

    def __init__(self, learning, now: datetime):
        self._learning = learning
        self._now = now
        self._views = {}
        self._others = {}

    def view(self, content_id: str) -> LessonView:
        if content_id not in self._views:
            self._views[content_id] = LessonView(self._learning, content_id)
        return self._views[content_id]

    def __call__(self, row, content_row):
        """None: open as before; "hold": not yet (an earlier probe is open, or teaching comes first);
        an ISO time: waits for the spacing until then at the latest."""
        if row["result_json"] is not None:
            return None
        return self.unit(row["content_id"], row["ability"], content_row)

    def unit(self, content_id, ability, content_row):
        """The same answer for one unit, queued or not (``content_row``: its ability_instruction_row).

        Everything it reads is stored (the lesson's profiles and its answers), so a unit whose question an
        app start ended waits as its queued question would (design 12 N6); the home's unqueued preview
        reads it for the units without a question.
        """
        if content_row["stage"] != "intro":
            return None
        checkpoint = _load(content_row["checkpoint_json"]) or {}
        if checkpoint.get("initial_familiarity"):
            return None
        view = self.view(content_id)
        if view.fresh or view.track is None:
            return None   # the lesson's very first start, or not a course track
        if not view.probing:
            # Taught: later abilities open as ordinary first checks. A failed probe whose teaching
            # has not been opened yet goes first.
            return "hold" if probe_rules.teaching_pending(view.checkpoints) and not view.shown else None
        if probe_rules.earlier_in_flight(view.track, ability, view.abilities()):
            return "hold"
        anchor = view.last_answer
        if anchor is None:
            return None
        others = self._others.get(content_id)
        if others is None:
            others = self._others[content_id] = self._learning.other_answers_since(
                content_id, anchor, limit=probe_rules.OTHER_ANSWERS)
        if probe_rules.spacing_ready(anchor, others, self._now):
            return None
        return probe_rules.spacing_until(anchor).isoformat()


def kana_answer(question) -> bool:
    from .reading_form_answers import applies
    return probe_rules.accepts_kana(question) or applies(question)


def policy_view(question):
    """The question with its effective answer policy (answer_policy: strict and accepted answers with the
    overlay row of its own template). A detached copy for checks only: the probe stays the question."""
    from .answer_policy import overlay, source
    return overlay.view(question, source.policy_for(question))


def probe_qualifies(question, track, ability, *, candidate=None) -> bool:
    """probe_rules.qualifies on the effective policy: a non-strict typed template whose row says strict,
    with a kana answer, can be a probe; one whose row says not strict cannot."""
    view = policy_view(question)
    return probe_rules.qualifies(view, track, ability, kana_answer=kana_answer(view), candidate=candidate)


def _requirements_met(item, question, known) -> bool:
    from .learning_questions import question_requires
    try:
        requires = question_requires(item, question)
    except (ValueError, KeyError, TypeError):
        return False
    return requires is None or set(requires) <= {*known, item["id"]}


def _eligible(learning, item, question, system_japanese: bool) -> bool:
    from .assessment_policy import assessment_decision
    from .answer_input import available
    return (assessment_decision(item, question, store=learning.store)["assessment_eligible"]
            and available(question, system_japanese=system_japanese))


def plan_probe(learning, source, ability: str, *, enabled_kinds, known, system_japanese: bool):
    """A qualifying probe question of ``ability`` built from the lesson's teaching ``source``, or None.

    The strict dependency gate only (no relaxed tier, no opening allowance, no
    glossed stem words), unguided, definite, eligible for formal scoring and
    answerable with the current input settings.
    """
    from .learning_practice_policy import _candidate_questions
    from .learning_questions import build_learning_question, question_kinds
    track = probe_rules.track(source.get("family"), source.get("domain"))
    enabled = set(enabled_kinds)
    available = set(question_kinds(source, ability=ability))
    kinds = [kind for kind in probe_rules.probe_kinds(track, ability) if kind in enabled and kind in available]
    if not kinds:
        return None
    state = {"known_background_ids": frozenset(known), "system_japanese_input": system_japanese}
    for candidate in _candidate_questions(source, ability, kinds, state):
        try:
            question = build_learning_question(source, candidate["kind"], variant=candidate["variant"])
        except (ValueError, KeyError, TypeError):
            continue
        if (probe_qualifies(question, track, ability, candidate=candidate)
                and _requirements_met(source, question, known)
                and _eligible(learning, source, question, system_japanese)):
            return question
    return None


def probe_by_form(learning, source, ability: str, *, enabled_kinds, system_japanese: bool) -> bool:
    """Whether some template of ``ability`` qualifies as a probe BY ITS FORM (brief J6 R5; reads only).

    The rules of plan_probe without the dependency gate: a probe kind that is enabled and present, a
    template that is not retired, unguided and definite under its effective answer policy (probe_qualifies:
    a non-strict typed template an overlay row makes strict qualifies; typed only for a word's use), eligible for formal scoring and answerable with the current input settings. False: this
    ability can never be probed, whatever the learner learns first.
    """
    from .learning_questions import build_learning_question, practice_templates, question_kinds
    from .learning_questions import retired_practice_variants
    track = probe_rules.track(source.get("family"), source.get("domain"))
    enabled, available = set(enabled_kinds), set(question_kinds(source, ability=ability))
    kinds = [kind for kind in probe_rules.probe_kinds(track, ability) if kind in enabled and kind in available]
    if not kinds:
        return False
    templates = practice_templates(source)
    retired = retired_practice_variants(source, templates)
    for kind in kinds:
        variants = templates.get(kind) if isinstance(templates, dict) else None
        for variant in range(len(variants) if isinstance(variants, list) else 4):
            if (kind, variant) in retired:
                continue
            try:
                question = build_learning_question(source, kind, variant=variant)
            except (ValueError, KeyError, TypeError):
                continue
            # The effective answer policy (probe_qualifies: the overlay row of the template), as plan_probe reads it.
            if probe_qualifies(question, track, ability) and _eligible(learning, source, question, system_japanese):
                return True
    return False


def open_probe_plan(learning, content_id, ability, unit, *, enabled_kinds, known, system_japanese: bool):
    """A qualifying probe of ``ability`` when its scope is an open probe, else None (reads only).

    Open: the scope's profile is a probe's, its diagnostic is active and the
    unit is unrated (LearningStore._initial_diagnostic). The queue pass plans
    such a unit with this question under the unit's usual id, so a probe whose
    queued question an app start archived comes back as a probe; None leaves
    the ordinary plan. ``unit``: the unit's saved row (None: not enrolled).
    """
    if unit is None or unit["review_count"]:
        return None
    row = learning.ability_instruction_row(content_id, ability)
    profile = (_load(row["checkpoint_json"]) or {}).get("initial_familiarity") if row is not None else None
    if not (probe_rules.is_probe(profile) and learning._initial_diagnostic(row, unit)):
        return None
    from .personal_content_versions import teaching_source, resolve
    source = resolve(learning.store, teaching_source(_load(row["content_json"])))
    return plan_probe(learning, source, ability, enabled_kinds=enabled_kinds, known=known,
                      system_japanese=system_japanese)


def pending_qualifies(learning, source, ability, pending, *, enabled_kinds, known, system_japanese: bool) -> bool:
    """Whether the unit's queued, untouched question can be the probe as it stands."""
    question = json.loads(pending["snapshot_json"])
    track = probe_rules.track(source.get("family"), source.get("domain"))
    return (question.get("kind") in set(enabled_kinds) and str(question.get("revision", source.get("revision")))
            == str(source.get("revision"))
            and probe_qualifies(question, track, ability)
            and _requirements_met(source, question, known)
            and _eligible(learning, source, question, system_japanese))


def _untouched(learning, row, abilities) -> bool:
    checkpoint = _load(row["checkpoint_json"]) or {}
    has_events, has_draft = learning._intro_started(row["content_id"], set(abilities))
    return (row["stage"] == "intro" and not has_events and not has_draft
            and checkpoint.get("supports_initial_familiarity") is True
            and not set(checkpoint) - _PROBE_KEYS)


def _write_profile(db, row, profile, **fields):
    checkpoint = _load(row["checkpoint_json"]) or {}
    checkpoint.pop("familiarity_draft", None)
    checkpoint["initial_familiarity"] = profile
    from .learning_store import _dump
    _teaching.update(db, row, checkpoint_json=_dump(checkpoint), **fields)


def _place_question(learning, cid, ability, source, question, now) -> str:
    """Put ``question`` in the unit's queue (append, or exchange the untouched queued one); its id."""
    from .learning_store import _unit_id
    db = learning._db
    uid = _unit_id(cid, ability)
    pending = db.execute("SELECT * FROM learning_question WHERE unit_id=? AND status='pending'", (uid,)).fetchone()
    if pending is not None:
        from .learning_replacement import LearningReplacementStore
        return LearningReplacementStore(learning).exchange_in_transaction(pending["question_id"], question, now=now)
    count = db.execute("SELECT COUNT(*) FROM learning_event WHERE unit_id=?", (uid,)).fetchone()[0]
    question = dict(question, id=f"{cid}:{ability}:review:{count}")
    added = learning._append_questions_in_transaction(question, now=now)
    if not added:
        raise ValueError("The probe question could not be queued")
    return added[0]


def _claim(db, row, now):
    from .explicit_learning_start import claim
    claim(db, row["content_id"], introduction_id=row["introduction_id"], now=now)


def _count_new(db, fair_new):
    if fair_new:
        from .new_dispatch import completed
        completed(db, "course")


def open_scope_in_transaction(learning, content_id, *, instruction_scope, expected_introduction_id,
                              enabled_kinds, known, system_japanese, now, fair_new=False) -> dict:
    """Start one introduction scope that has no profile; the caller owns the transaction.

    Returns {"route": "probe", "question_id", "ability", "instruction_scope"},
    {"route": "teach"}, {"route": "check"}, {"route": "wait"} (no qualifying
    probe now; nothing written) or {"route": "existing"} (a profile is
    already saved: nothing written).
    """
    from .learning_store import _retrieval_delay
    db = learning._db
    row = learning.instruction_row(content_id, instruction_scope)
    if row is None:
        raise KeyError(content_id)
    if row["introduction_id"] != expected_introduction_id:
        raise ValueError("The introduction is no longer current")
    learning.require_available(content_id)
    checkpoint = _load(row["checkpoint_json"]) or {}
    if checkpoint.get("initial_familiarity") or row["stage"] != "intro":
        return {"route": "existing"}
    view = LessonView(learning, content_id)
    abilities = list(_teaching.abilities(db, row))
    from .personal_content_versions import teaching_source, resolve
    source = resolve(learning.store, teaching_source(_load(row["content_json"])))
    track = view.track

    def probe_question(ability):
        return qualifying_probe(learning, content_id, ability, source, enabled_kinds=enabled_kinds,
                                known=known, system_japanese=system_japanese)

    def start_probe(row, ability, kept, planned):
        _write_profile(db, row, probe_rules.probe_profile(ability, now), stage="check")
        row = learning.instruction_row(content_id, _teaching.scope_for(db, content_id, ability))
        _teaching.ready_units(db, row)
        question_id = kept or _place_question(learning, content_id, ability, source, planned, now)
        _claim(db, row, now)
        _count_new(db, fair_new)
        return {"route": "probe", "question_id": question_id, "ability": ability,
                "instruction_scope": dict(row).get("instruction_scope")}

    untouched = track is not None and _untouched(learning, row, abilities)
    if instruction_scope is None and view.fresh:
        # Design 12 N15: a lesson whose own page was opened within the learning day (24 hours) is taught, not probed.
        from .arrangement.rules.recent import LEARNING_DAY
        from .retention_check import page_viewed_at
        read = probe_rules.page_read_recently(page_viewed_at(db, content_id), now, LEARNING_DAY)
        if untouched and view.probing and not read:
            for ability in probe_rules.order(track):
                if ability not in abilities:
                    continue
                kept, planned = probe_question(ability)
                if kept is None and planned is None:
                    continue
                if len(abilities) > 1:
                    # The draft is dropped first: separation keeps only an untouched checkpoint.
                    if "familiarity_draft" in checkpoint:
                        clean = {k: v for k, v in checkpoint.items() if k != "familiarity_draft"}
                        from .learning_store import _dump
                        _teaching.update(db, row, checkpoint_json=_dump(clean))
                        row = learning.instruction_row(content_id, None)
                    _teaching.separate_untouched(db, row, first_ability=ability)
                    row = learning.instruction_row(content_id, None)
                    if list(_teaching.abilities(db, row)) != [ability]:
                        break   # the abilities could not be given their own starts: teach the lesson
                return start_probe(row, ability, kept, planned)
            abilities = list(_teaching.abilities(db, row))
        # Nothing to probe, the explanation was already shown, or its page was just read (N15): the lesson is taught.
        _write_profile(db, row, probe_rules.plain_profile(abilities, now))
        _claim(db, row, now)
        return {"route": "teach"}
    if view.probing and untouched and len(abilities) == 1 and probe_rules.probed(track, abilities[0]):
        kept, planned = probe_question(abilities[0])
        if kept is not None or planned is not None:
            return start_probe(row, abilities[0], kept, planned)
        if probe_by_form(learning, source, abilities[0], enabled_kinds=enabled_kinds,
                         system_japanese=system_japanese):
            return {"route": "wait"}   # a probe exists by its form; only its requirements are not met yet
        # J6 R5: no template of this ability can ever be its probe: an ordinary first check (below), as an
        # ability outside the probe order opens, instead of waiting for ever.
    if probe_rules.teaching_pending(view.checkpoints) and not view.shown:
        return {"route": "wait"}
    # An ordinary first check: the lesson was taught, or the ability is never probed (listening).
    profile = probe_rules.plain_profile(abilities, now)
    _write_profile(db, row, profile, stage="bridge", guided_at_utc=now.isoformat(),
                   retrieval_at_utc=(now + timedelta(seconds=_retrieval_delay({"initial_familiarity": profile}))).isoformat())
    _teaching.ready_units(db, learning.instruction_row(content_id, instruction_scope))
    _claim(db, row, now)
    _count_new(db, fair_new)
    return {"route": "check"}
