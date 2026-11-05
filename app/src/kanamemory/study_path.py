"""A curriculum overlay over existing memory identities, never a new scheduler.

Only explicit additions write the small enrollment setting. Reading the
path derives progress from real learner records, including older app versions.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from datetime import datetime
import json
import secrets
from types import MappingProxyType

from .domain import WORD_SKILLS, HANDWRITING_SKILLS, LISTENING_SKILLS, _utc, question_identities
from .learning_content import learning_items
from .kana_question_policy import direction_allowed, question_allowed

PATH_SETTING = "study_path_v1"
_UNREAD = object()


class _StudyPathRead:
    """One synchronous UI action. Never retained by a page or used to enroll.

    No transaction/cache is created. The next action gets current settings,
    content and queue state again; writes independently revalidate their inputs.
    """

    def __init__(self, service: StudyPathService, now: datetime):
        self.service, self.now = service, now
        self._checkpoint, self._states = _UNREAD, None
        self.active = True

    @property
    def pending(self) -> dict | None:
        self.check_active()
        if self._checkpoint is _UNREAD:
            self._checkpoint = self.service.store.load_session_progress(lane="main", lazy_questions=True)
        return self._checkpoint

    @property
    def states(self) -> Mapping[str, str]:
        self.check_active()
        if self._states is None:
            self._states = MappingProxyType(self.service._entry_states(pending=self.pending))
        return self._states

    def check_active(self) -> None:
        if not self.active:
            raise RuntimeError("Study path read operation has ended")

    def overview(self):
        self.check_active()
        return self.service._overview(self.states)

    def preview_entries(self, entry_ids):
        self.check_active()
        return self.service._entry_plan(entry_ids, states=self.states)[0]

    def plan_next(self, count=1):
        self.check_active()
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        return self.service._plan_next(count, self.overview())


def validate_path_state(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("学习路线记录版本无效。")
    for key in ("added_sections", "reserved_entries", "reference_read"):
        items = value.get(key)
        if (not isinstance(items, list) or len(items) > 20000
                or any(not isinstance(x, str) or not x or len(x) > 1024 for x in items)
                or len(set(items)) != len(items)):
            raise ValueError("学习路线记录无效：" + key)
    return deepcopy(value)


class StudyPathService:
    def __init__(self, repo, store, learning_store, catalogue=None):
        from .study_path_catalog import build_study_path, flatten_sections
        self.repo, self.store, self.learning = repo, store, learning_store
        items = learning_items() if catalogue is None else list(catalogue)
        saved = learning_store.list_items()
        self.items = {item["id"]: item for item in items}
        self.items.update({item["id"]: item for item in saved})
        self.path = build_study_path(repo.kana_list(), items, extra_items=saved)
        self.sections = flatten_sections(self.path)
        self.by_id = {section["id"]: section for section in self.sections}
        self.entries = {entry["id"]: entry for section in self.sections for entry in section["entries"]}
        self.entry_sections = {entry["id"]: section["id"] for section in self.sections for entry in section["entries"]}
        self.learning_entries = {entry["content_id"]: entry["id"] for entry in self.entries.values()
                                 if entry["kind"] == "learning"}

    def _state(self):
        return validate_path_state(self.store.get_setting(PATH_SETTING, {
            "version": 1, "added_sections": [], "reserved_entries": [], "reference_read": []}))

    @contextmanager
    def read_operation(self, *, now: datetime | None = None) -> Iterator[_StudyPathRead]:
        """Share reads only inside one synchronous action, with no await/write."""
        read = _StudyPathRead(self, _utc(now))
        try:
            yield read
        finally:
            read.active = False
            read._checkpoint = read._states = None

    def _entry_states(self, *, pending=_UNREAD):
        state = self._state()
        reserved, read = set(state["reserved_entries"]), set(state["reference_read"])
        learning_rows = self.store.queries.course_progress_rows()
        stages = {}
        for row in learning_rows:
            stages.setdefault(row['content_id'], []).append(row['stage'])
        learning = {cid: 'done' if all(s == 'done' for s in values)
                    else 'intro' if all(s == 'intro' for s in values) else 'guide'
                    for cid, values in stages.items()}
        needs_teaching = {row["content_id"] for row in learning_rows
                          if json.loads(row["checkpoint_json"]).get("initial_familiarity", {}).get(
                              "diagnostic_state") == "needs_teaching"}
        reviewed = {(row["content_id"], row["script"]) for row in self.store.queries.reviewed_kana_rows()
            if row["skill"] not in WORD_SKILLS}
        if pending is _UNREAD:
            pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        # Progress needs only identity headers, including completed history.
        # The public header projection avoids constructing/decorating old Questions.
        queued = {(cid, script) for cid, kind, script in
                  (self.store.session_question_headers(pending["session_id"]) if pending else ())
                  if kind not in WORD_SKILLS}
        introductions = self.store.get_setting("kana_introductions_v1", {}).get("introduced", {})
        result = {}
        for section in self.sections:
            for entry in section["entries"]:
                eid, cid = entry["id"], entry["content_id"]
                status = "added" if eid in reserved else "new"
                if entry["kind"] == "reference":
                    if eid in read or (entry.get("practice_retired") and (cid, entry["script"]) in reviewed):
                        status = "completed"
                elif entry["kind"] == "learning":
                    stage = learning.get(cid)
                    if stage == "done":
                        status = "completed"
                    elif stage and (stage != "intro" or cid in needs_teaching):
                        status = "started"
                    elif stage:
                        status = "added"
                else:
                    key = cid, entry["script"]
                    if key in reviewed:
                        status = "completed"
                    elif cid + "|" + entry["script"] in introductions:
                        status = "started"
                    elif key in queued:
                        status = "added"
                result[eid] = status
        return result

    @staticmethod
    def _progress(states):
        states = list(states)
        return {"total": len(states), "added": sum(s != "new" for s in states),
                "started": sum(s in {"started", "completed"} for s in states),
                "completed": sum(s == "completed" for s in states)}

    @staticmethod
    def _summary_state(progress):
        if progress["total"] and progress["completed"] == progress["total"]:
            return "completed"
        if progress["started"]:
            return "started"
        if progress["added"] == progress["total"]:
            return "added"
        return "partial" if progress["added"] else "new"

    @staticmethod
    def _section_progress(sections):
        """Parent progress counts whole sections, retaining a partial subtotal."""
        return {"total": len(sections),
                "added": sum(bool(s["progress"]["total"]) and s["new_count"] == 0 for s in sections),
                "partial": sum(0 < s["progress"]["added"] < s["progress"]["total"] for s in sections),
                "started": sum(s["progress"]["started"] > 0 for s in sections),
                "completed": sum(s["state"] == "completed" for s in sections)}

    def overview(self):
        return self._overview(self._entry_states())

    def _overview(self, states):
        result = deepcopy(self.path)
        sections = []
        for stage in result["stages"]:
            stage_sections = []
            for unit in stage["units"]:
                for section in unit["sections"]:
                    for entry in section["entries"]:
                        entry["state"] = states[entry["id"]]
                    section["progress"] = self._progress(states[e["id"]] for e in section["entries"])
                    section["state"] = self._summary_state(section["progress"])
                    section["new_count"] = section["progress"]["total"] - section["progress"]["added"]
                unit["progress"] = self._section_progress(unit["sections"])
                stage_sections.extend(unit["sections"])
            stage["progress"] = self._section_progress(stage_sections)
            sections.extend(stage_sections)
        required = [s for s in sections if not s.get("optional")]
        def fully_added(section):
            return bool(section["progress"]["total"]) and section["new_count"] == 0
        def partially_added(section):
            return 0 < section["progress"]["added"] < section["progress"]["total"]
        result.update(required_total_sections=len(required),
            required_added_sections=sum(fully_added(s) for s in required),
            required_partial_sections=sum(partially_added(s) for s in required),
            required_completed_sections=sum(s["state"] == "completed" for s in required),
            total_sections=len(sections),
            added_sections=sum(fully_added(s) for s in sections),
            partial_sections=sum(partially_added(s) for s in sections),
            completed_sections=sum(s["state"] == "completed" for s in sections),
            in_progress_sections=sum(s["progress"]["added"] > 0 and s["state"] != "completed" for s in sections),
            queued_section_ids=[s["id"] for s in sections if s["progress"]["added"] > 0 and s["state"] != "completed"],
            next_section=next((s for s in sections if s["new_count"] and not s.get("optional")), None))
        return result

    def section(self, section_id):
        for stage in self.overview()["stages"]:
            for unit in stage["units"]:
                for section in unit["sections"]:
                    if section["id"] == section_id:
                        return section
        raise KeyError(section_id)

    def plan_next(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        return self._plan_next(count, self.overview())

    @staticmethod
    def _plan_next(count, overview):
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        sections = [s for stage in overview["stages"] for u in stage["units"] for s in u["sections"]]
        return [s for s in sections if s["new_count"] and not s.get("optional")][:count]

    def add_sections(self, section_ids, *, start_learning=False, action_id=None):
        if type(start_learning) is not bool:
            raise ValueError('Invalid explicit start choice')
        initial_states = self._entry_states() if start_learning else {}
        started_at = _utc(None)
        if isinstance(section_ids, str):
            section_ids = [section_ids]
        chosen, visiting = set(), set()
        def visit(sid):
            if sid in chosen:
                return
            if sid not in self.by_id or sid in visiting:
                raise ValueError("学习小节或前置顺序无效。")
            visiting.add(sid)
            for dep in self.by_id[sid].get("depends_on", []):
                visit(dep)
            visiting.remove(sid)
            chosen.add(sid)
        for sid in section_ids:
            visit(sid)
        sections = [s for s in self.sections if s["id"] in chosen]
        targets = [self.items[e["content_id"]] for s in sections for e in s["entries"] if e["kind"] == "learning"]
        from .learning_dependencies import build_dependency_plan
        planned_items = build_dependency_plan([item["id"] for item in targets], self.items, self.learning)["items"] if targets else []
        state = self._state()
        added = [s["id"] for s in sections if s["id"] not in state["added_sections"]]
        state["added_sections"] = list(dict.fromkeys([*state["added_sections"], *(s["id"] for s in sections)]))
        state["reserved_entries"] = list(dict.fromkeys([*state["reserved_entries"], *(e["id"] for s in sections for e in s["entries"])]))
        validate_path_state(state)
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        def reserve(db):
            # Keep whole-section enrollment and reservations in one transaction,
            # including kana-only sections and retries after a failed save.
            db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (PATH_SETTING, encoded))
            roots = list(dict.fromkeys([*(e['id'] for s in sections for e in s['entries']),
                *(self.learning_entries[item['id']] for item in planned_items if item['id'] in self.learning_entries)]))
            if start_learning and any(initial_states.get(eid) == 'new'
                    and self.entries[eid]['kind'] in {'learning', 'kana'} for eid in roots):
                from .explicit_learning_start import arm, candidates
                arm(db, candidates(self, roots), action_id=action_id, now=started_at, service=self)
        added_learning = self.learning.enroll(planned_items, _after_enroll=reserve)
        return {"added_sections": added, "added_learning": added_learning,
                "reserved_entries": state["reserved_entries"], "sections": sections}

    def _entry_plan(self, entry_ids, *, states=None):
        """Resolve exact new entries and their own prerequisites, without writes.

        Section dependencies aggregate every member's prerequisites. They are
        intentionally not used here: selecting one member must not enroll its
        siblings or their unrelated prerequisites. An enrolled but unstarted
        prerequisite still needs its own foundations; actual prior study and
        preserved answer drafts keep their existing route.
        """
        from .learning_dependencies import dependencies_of, effective_dependencies, prerequisite_status
        if isinstance(entry_ids, str):
            entry_ids = [entry_ids]
        try:
            requested = list(entry_ids)
        except TypeError as exc:
            raise ValueError("学习项选择无效。") from exc
        if any(not isinstance(eid, str) or eid not in self.entries for eid in requested):
            raise ValueError("学习项选择含无效标识。")
        # Old bookmarks for standalone signs now open the one shared rule.
        # Keep historical entries/records intact; only a new selection maps.
        requested = [self.learning_entries.get(self.entries[eid].get("reading_rule_id"), eid)
                     for eid in requested]
        states = self._entry_states() if states is None else states
        requested = set(requested)
        selected = [eid for eid in self.entries if eid in requested and states[eid] == "new"]
        chosen, visited, visiting, dependency_order = set(), set(), set(), []
        kana_state = None

        def visit(eid):
            nonlocal kana_state
            if eid in visited:
                return
            if eid in visiting:
                raise ValueError("学习项前置关系成环。")
            visiting.add(eid)
            entry = self.entries[eid]
            source = self.items.get(entry["content_id"]) if entry["kind"] == "learning" else entry
            if source is None:
                raise ValueError("学习项缺少教学材料。")
            dependencies = dependencies_of(source)
            if entry["kind"] == "learning":
                dependencies = effective_dependencies(self.learning, source)
                if states[eid] != "new":
                    status = prerequisite_status(self.learning, source)
                    if status["grandfathered"] or status["waived"]:
                        dependencies = []
            elif entry["kind"] == "kana" and states[eid] != "new":
                from .kana_dependency_runtime import kana_dependency_state
                if kana_state is None:
                    kana_state = kana_dependency_state(self.store)
                if eid in kana_state.reviewed:
                    dependencies = []
            elif states[eid] != "new":
                dependencies = []
            for dependency in dependencies:
                target = self.learning_entries.get(dependency, dependency)
                if target not in self.entries:
                    raise ValueError("学习路线缺少前置内容：" + dependency)
                visit(target)
            visiting.remove(eid)
            visited.add(eid)
            if states[eid] == "new":
                chosen.add(eid)
                dependency_order.append(eid)

        for eid in selected:
            visit(eid)
        combined = [eid for eid in self.entries if eid in chosen]
        prerequisites = [eid for eid in combined if eid not in requested]
        section_set = {self.entry_sections[eid] for eid in combined}
        sections = [s["id"] for s in self.sections if s["id"] in section_set]
        summary = "尚未选择新内容"
        if selected:
            parts = [f"已选 {len(selected)} 项"]
            if prerequisites:
                parts.append(f"自动加入前置 {len(prerequisites)} 项")
            parts.append(f"涉及 {len(sections)} 小节")
            summary = " · ".join(parts)
        preview = {"selected_entry_ids": selected, "prerequisite_entry_ids": prerequisites,
                   "entry_ids": combined, "selected_count": len(selected),
                   "prerequisite_count": len(prerequisites), "total_count": len(combined),
                   "section_ids": sections, "section_count": len(sections),
                   "selected_section_count": len({self.entry_sections[eid] for eid in selected}),
                   "summary": summary}
        return preview, dependency_order, states

    def preview_entries(self, entry_ids):
        """Return a stable, read-only preview of explicit and prerequisite entries."""
        return self._entry_plan(entry_ids)[0]

    def add_entries(self, entry_ids, *, start_learning=False, action_id=None):
        """Append exact selected identities; never replace a queue or schedule."""
        if type(start_learning) is not bool:
            raise ValueError('Invalid explicit start choice')
        started_at = _utc(None)
        preview, dependency_order, states = self._entry_plan(entry_ids)
        state = self._state()
        if not preview["entry_ids"]:
            return {**preview, "added_sections": [], "added_learning": [],
                    "reserved_entries": state["reserved_entries"], "sections": []}
        added_ids = set(preview["entry_ids"])
        sections = [self.by_id[sid] for sid in preview["section_ids"]]
        full_sections = [s["id"] for s in sections if s["entries"] and all(
            states[e["id"]] != "new" or e["id"] in added_ids for e in s["entries"])]
        added_sections = [sid for sid in full_sections if sid not in state["added_sections"]]
        state["added_sections"] = list(dict.fromkeys([*state["added_sections"], *full_sections]))
        state["reserved_entries"] = list(dict.fromkeys([*state["reserved_entries"], *preview["entry_ids"]]))
        validate_path_state(state)
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        targets = [self.items[self.entries[eid]["content_id"]] for eid in dependency_order
                   if self.entries[eid]["kind"] == "learning"]
        def reserve(db):
            # Use the enrollment transaction directly. Calling set_setting
            # here would open a nested context and could commit too early.
            db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (PATH_SETTING, encoded))
            if start_learning and any(self.entries[eid]['kind'] in {'learning', 'kana'}
                                      for eid in preview['entry_ids']):
                from .explicit_learning_start import arm, candidates
                arm(db, candidates(self, preview['entry_ids']), action_id=action_id, now=started_at, service=self)
        added_learning = self.learning.enroll(targets, _after_enroll=reserve)
        return {**preview, "added_sections": added_sections, "added_learning": added_learning,
                "reserved_entries": state["reserved_entries"], "sections": deepcopy(sections)}

    def reference_entry(self, entry_id):
        """Return one reference's saved state without enrolling its siblings."""
        entry = self.entries.get(entry_id)
        if not entry or entry["kind"] != "reference":
            raise ValueError("参考项无效。")
        section = self.by_id[self.entry_sections[entry_id]]
        return {**deepcopy(entry), "state": self._entry_states()[entry_id],
                "section_id": section["id"], "section_title": section["title"]}

    def next_reference_entry(self):
        """Resume the first unread enrolled reference in catalogue order."""
        states = self._entry_states()
        for entry in self.entries.values():
            if entry["kind"] == "reference" and states[entry["id"]] == "added":
                section = self.by_id[self.entry_sections[entry["id"]]]
                return {**deepcopy(entry), "state": "added", "section_id": section["id"],
                        "section_title": section["title"]}
        return None

    def mark_reference_entry_read(self, entry_id):
        """Acknowledge only the displayed, already enrolled reference.

        This is deliberately stricter than the legacy section-level browsing
        action. Opening or completing one item must never add or read siblings.
        """
        entry = self.entries.get(entry_id)
        state = self._state()
        if (not entry or entry["kind"] != "reference"
                or entry_id not in state["reserved_entries"]):
            raise ValueError("只能标记已加入的参考项。")
        if entry_id in state["reference_read"]:
            return False
        state["reference_read"].append(entry_id)
        self.store.set_setting(PATH_SETTING, state)
        return True

    def mark_reference_read(self, section_id):
        state = self._state()
        section = self.by_id[section_id]
        refs = [e["id"] for e in section["entries"] if e["kind"] == "reference"]
        if not refs:
            return
        reserved = set(state["reserved_entries"])
        selected_refs = [eid for eid in refs if eid in reserved]
        if selected_refs:
            # An entry-level selection must remain exact when its reference
            # page is later marked read. Do not silently enroll its siblings.
            refs = selected_refs
        else:
            # Directly browsing a wholly unselected section and explicitly
            # marking that section read retains the existing whole-section UI.
            self.add_sections([section_id])
            state = self._state()
        state["reference_read"] = list(dict.fromkeys([*state["reference_read"], *refs]))
        self.store.set_setting(PATH_SETTING, state)

    def kana_advancement_targets(self, kinds, *, now=None, pending=_UNREAD, stages=None, lazy_questions=False):
        """Read-only unlocked, ungraded directions shared by home and resume."""
        from .domain import _adaptive_stages, _ADAPTIVE_LEVELS
        kinds = [kind for kind in kinds if kind not in WORD_SKILLS and _ADAPTIVE_LEVELS[kind] > 0]
        if not kinds:
            return []
        now = _utc(now)
        pending = self.store.load_session_progress(lane="main", lazy_questions=lazy_questions) if pending is _UNREAD else pending
        active = {(q.content_id, q.kind, q.script) for q in
                  (pending["questions"][pending["question_index"]:] if pending else [])
                  if question_allowed(q)}
        cards = {(r["content_id"], r["skill"], r["script"]): r for r in self.store.scheduling_cards(aliases=True)}
        from .kana_routing import identity
        occupied = {identity(self.store, cid, kind, script) for cid, kind, script in active}
        reviewed = {(cid, script) for (cid, _, script), card in cards.items() if card["review_count"] > 0}
        blocked = active | cards.keys()
        stages = stages if stages is not None else {
            script: _adaptive_stages(self.store, script, now) for script in ("hiragana", "katakana")}
        targets = []
        for entry in self.entries.values():
            cid, script = entry["content_id"], entry.get("script")
            if (entry["kind"] != "kana" or (cid, script) not in reviewed
                    or not self.store.is_content_available(cid, now)):
                continue
            stage = stages[script].get(cid, 0)
            for kind in kinds:
                if (_ADAPTIVE_LEVELS[kind] <= stage and (cid, kind, script) not in blocked
                        and identity(self.store, cid, kind, script) not in occupied
                        and direction_allowed(cid, kind, script)):
                    targets.append((entry, kind, stage))
        return targets

    def ensure_kana_queue(self, kinds, *, audio_available=False, handwriting_available=False, now=None,
                          lazy_questions=False):
        """Append exact reserved script identities, retaining old snapshots/drafts.

        Current settings are authoritative. An unavailable stimulus leaves the
        reservation visible for later, without inventing a question or success.
        """
        from .domain import (SKILLS, build_questions, _BatchQuestionStore,
                             _adaptive_stages, _adaptive_new_kinds,
                             _eligible_kana, _check_build_cancelled)
        from .kana_dependency_repair import ensure_kana_prerequisites
        if isinstance(kinds, (str, bytes)) or any(k not in SKILLS for k in kinds):
            raise ValueError("未知假名题型。")
        requested_kinds = list(kinds)
        kinds = [k for k in dict.fromkeys(kinds) if k not in WORD_SKILLS
                 and (audio_available or k not in LISTENING_SKILLS)
                 and (handwriting_available or k not in HANDWRITING_SKILLS)]
        def with_prerequisites(result):
            repaired = ensure_kana_prerequisites(self.repo, self.store, kinds, now=now,
                audio_available=audio_available, handwriting_available=handwriting_available,
                lazy_questions=lazy_questions)
            from .kana_context_words import ensure_kana_context_word, ensure_kana_rule_probe
            probe = ensure_kana_rule_probe(self.repo, self.store, kinds, now=now)
            context = ensure_kana_context_word(self.repo, self.store, requested_kinds,
                audio_available=audio_available, now=now)
            return {"added": result["added"] + repaired["added"] + context["added"],
                    "session_id": result["session_id"] or repaired["session_id"] or context["session_id"] or probe["session_id"]}
        if not kinds:
            return with_prerequisites({"added": 0, "session_id": None})
        now = _utc(now)
        reserved = set(self._state()["reserved_entries"])
        pending = self.store.load_session_progress(lane="main", lazy_questions=lazy_questions)
        active = (set(question_identities(pending["questions"], pending["question_index"],
                                         excluded_kinds=WORD_SKILLS)) if pending else set())
        active_scripts = {(cid, script) for cid, _, script in active}
        reviewed = {(r["content_id"], r["script"]) for r in self.store.queries.reviewed_kana_rows()}
        targets = [e for s in self.sections for e in s["entries"] if e["kind"] == "kana"
                   and e["id"] in reserved and (e["content_id"], e["script"]) not in reviewed | active_scripts
                   and self.store.is_content_available(e["content_id"], now)]
        seed, questions = secrets.randbits(32), []
        audio_candidates = None
        from .kana_routing import identity, candidate_cards, deduplicate_questions
        occupied = {identity(self.store, cid, kind, script) for cid, kind, script in active}

        def can_build(content_id, kind, script):
            nonlocal audio_candidates
            if identity(self.store, content_id, kind, script) in occupied:
                return False
            if not direction_allowed(content_id, kind, script):
                _check_build_cancelled(self.store)
                return False
            if kind != "kana_handwriting":
                return True
            if audio_candidates is None:
                # Advancement can repeatedly offer an intrinsically ambiguous
                # isolated sound (e.g. お/を). The single-skill builder already
                # excludes it, but would reread the entire catalogue each time.
                # Use that same predicate once per visit; retain no stale cache
                # across repository or settings changes. Other builders keep
                # their original eligibility and choice generation unchanged.
                rows = self.repo.kana_list()
                audio_candidates = {
                    alphabet: {row.get("id", row.get("entry_id")) for row in rows
                               if _eligible_kana(row, alphabet, True, audio_only=True)}
                    for alphabet in ("hiragana", "katakana")}
            eligible = content_id in audio_candidates[script]
            if not eligible:
                # The skipped builder would still honor a worker cancellation.
                _check_build_cancelled(self.store)
            return eligible

        # Reuse existing due cards without bringing future reviews forward.
        for card in candidate_cards(self.store, kinds, now):
            if datetime.fromisoformat(card["due_at_utc"]) > now:
                continue
            cid, script, kind = card["content_id"], card["script"], card["skill"]
            if (kind not in kinds or (cid, kind, script) in active
                    or not self.store.is_content_available(cid, now)):
                continue
            if not can_build(cid, kind, script):
                continue
            made = build_questions(self.repo, _BatchQuestionStore(self.store, [cid]), kind=kind,
                script=script, limit=1, seed=seed, now=now, include_extended=True)
            for q in made:
                q.metadata.update(study_path_due_v1=True, not_before_utc=card["due_at_utc"])
                q.metadata["practice_config"].update(schedule_mode="spaced", kinds=kinds, rounds=1)
            questions.extend(made)
        stages = {script: _adaptive_stages(self.store, script, now) for script in ("hiragana", "katakana")}
        for entry in targets:
            cid, script = entry["content_id"], entry["script"]
            selected = _adaptive_new_kinds(kinds, stages[script].get(cid, 0))
            for kind in selected:
                card = self.store.get_scheduling_card(cid, kind, script)
                if card and card.get("suspended"):
                    continue
                if not can_build(cid, kind, script):
                    continue
                made = build_questions(self.repo, _BatchQuestionStore(self.store, [cid]), kind=kind,
                    script=script, limit=1, seed=seed, now=now, include_extended=True)
                for q in made:
                    q.metadata.update(kana_intro_v1=True, kana_familiarity_v1=True, study_path_entry=entry["id"],
                                      schedule_mode="spaced", adaptive_policy="scaffold-v1",
                                      adaptive_stage=stages[script].get(cid, 0))
                    q.metadata["practice_config"].update(schedule_mode="spaced", adaptive=True,
                        kinds=kinds, requested_script=script, rounds=1)
                questions.extend(made)
        # Initial enrollment is not the only opportunity to unlock a direction.
        # Later independent recalls can support typing/grid/writing while the
        # original cards retain their own due dates. Include legacy learned
        # symbols too: the path already treats them as enrolled/completed.
        for entry, kind, stage in self.kana_advancement_targets(kinds, now=now, pending=pending, stages=stages):
            cid, script = entry["content_id"], entry["script"]
            if not can_build(cid, kind, script):
                continue
            made = build_questions(self.repo, _BatchQuestionStore(self.store, [cid]), kind=kind,
                script=script, limit=1, seed=seed, now=now, include_extended=True)
            for q in made:
                q.metadata.update(study_path_entry=entry["id"], schedule_mode="spaced",
                                  adaptive_policy="scaffold-v1", adaptive_stage=stage)
                q.metadata["practice_config"].update(schedule_mode="spaced", adaptive=True,
                    kinds=kinds, requested_script=script, rounds=1)
            questions.extend(made)
        questions = deduplicate_questions(self.store, questions, now, new_abilities=True)
        if not questions:
            return with_prerequisites({"added": 0, "session_id": None})
        old_lane = self.store.practice_lane
        self.store.practice_lane = "main"
        try:
            if pending:
                sid = pending["session_id"]
                self.store.append_session(sid, questions, seed=seed,
                    expected_token=self.store.session_append_token(sid), distinct_scripts=True,
                    curriculum_directions=True, now=now)
            else:
                sid = self.store.start_session(questions, seed=seed, now=now)
                self.store.save_session_progress(sid, 0, 0, state={})
        finally:
            self.store.practice_lane = old_lane
        return with_prerequisites({"added": len(questions), "session_id": sid})
