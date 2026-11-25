"""StoreArrangement: the study arrangement put together for one learner store (design 12 WP-B3, WP-B6, WP-H).

The composition root of the sub-package: the learner's preferences, the
study clock read from the store, the dispatcher that joins them, the memo
of fruitless new-content probes, what this launch has already shown once
(launch notes), the focus scope catalogue and focus service, the presenter
of the arrangement's display models, and what one answer counts as
(PracticeCredit; practice_credit and practice_sitting build it and the
memory of one sitting of extra practice for code that has no
StoreArrangement, such as the mastery check), and the practice side of the
kana queue (QueuePractice: the practice-only answers of reinforce batches
and the misses held for the deck at the end of a batch; practice_deck builds
a deck over this store), and extra practice (ExtraSession: the 加练 mode,
its pool of learned states read through sources/extra_states and its
sitting), and the consolidation deck of what was taught today
(Consolidation over sources/consolidation, design 12 N5), and the learner's review filter (ReviewFilterService:
只做范围内, 暂不复习 and the extra-practice part as one filter that the dispatch, the counts, extra practice's pool
and the consolidation deck read, design 12 H8-H10, E9). Built lazily by its user and replaced together with the store
object (a restore or reset swaps the connection): the clock's memory and
the extra-practice mode belong to one store.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from .services import ArrangementPreferences, ArrangementPresenter, FocusService, LaunchNotes, ShareDispatcher
from .model import DeckItem
from .services.consolidation import Consolidation
from .services.extra_parts import ExtraParts
from .services.extra_session import ExtraSession
from .services.kana_depth import KanaDepthSetting
from .services.practice import PracticeCredit
from .services.practice_deck import PracticeDeck
from .services.practice_sitting import PracticeSitting
from .services.queue_practice import QueuePractice
from .services.review_filter import ReviewFilterService
from .services.probe_memo import ProbeMemo
from .sources.consolidation import StoreConsolidation
from .sources.deck_course import CourseDeckLane
from .sources.deck_kana import KanaDeckLane
from .sources.deck_questions import StoreDeckQuestions
from .sources.due_reviews import DueReviews
from .sources.events import StoreEventSource
from .sources.extra_states import StoreExtraStates
from .sources.practice_exposure import ConsolidationExposure, StorePracticeExposure
from .sources.practice_moves import StorePracticeMoves
from .sources.recent import lesson_firsts
from .sources.scope_catalog import StoreScopeCatalog
from .sources.states import StoreStates


@dataclass(frozen=True)
class StoreArrangement:
    store: object
    preferences: ArrangementPreferences
    events: StoreEventSource
    dispatcher: ShareDispatcher
    probe_memo: ProbeMemo   # fruitless new-content probes of this store's current round
    launch_notes: LaunchNotes   # once-per-launch prompts (B10 first continue, fresh-install orientation)
    presenter: ArrangementPresenter
    catalog: StoreScopeCatalog   # focus scope -> content, title, next section, choices
    focus: FocusService          # the focus for the scheduling; change() is the one rearrangement
    credit: PracticeCredit       # what one answer counts as (design 12 E3)
    practice: QueuePractice      # the kana queue's practice-only answers and held deck items (E3, E4)
    extra: ExtraSession          # extra practice (加练), memory only (E1-E8)
    consolidation: Consolidation   # the consolidation deck of what was taught today (section 21, N5)
    review_filter: ReviewFilterService   # 只做范围内, 暂不复习 and the extra-practice part (H8-H10, E9)
    extra_parts: ExtraParts       # the page 加练's parts and their counts (E9)

    @classmethod
    def for_store(cls, store, *, routes: Callable[[], object] | None = None,
                  repository: Callable[[], object] | None = None,
                  kana_kinds: Callable[[], Iterable[str]] | None = None,
                  course_kinds: Callable[[], Iterable[str]] | None = None,
                  japanese_input: Callable[[], bool] | None = None,
                  catalog: StoreScopeCatalog | None = None) -> StoreArrangement:
        """``routes``: a callable giving the app's current StudyPathService (route and node scopes need it).

        Without it route and node scopes resolve to None (as if no scope
        were set); library and family scopes still resolve. ``repository``:
        a callable giving the app's content repository, asked only when the
        practice deck builds a kana question on the spot. ``kana_kinds`` /
        ``course_kinds`` / ``japanese_input``: the kana question kinds the
        main lane uses now, the enabled course kinds and whether the system
        Japanese input is on, as the app has them (asked by extra practice at
        each pool read and deck); without them the stored preferences decide
        and no system input is assumed. ``catalog``: the scope catalogue (a ports.ScopeCatalog; default the
        store's StoreScopeCatalog over ``routes``), one for the focus and the review filter alike.
        """
        preferences = ArrangementPreferences(store)
        events = StoreEventSource(store)
        dispatcher = ShareDispatcher(preferences, events)
        launch_notes = LaunchNotes()
        catalog = catalog if catalog is not None else StoreScopeCatalog(store, routes)
        focus = FocusService(preferences, catalog, restart=lambda: _restart_main_round(store))
        review_filter = ReviewFilterService(preferences, focus, study_day=lambda at: store.study_day(at).isoformat(),
                                            restart=lambda: _restart_main_round(store),
                                            started=lambda: store.teaching_queries.explicit_started_work(),
                                            learned=lambda: store.queries.learned_members())
        # The extra-practice part lives as long as the mode: every stop() forgets it (design 12 E9).
        extra = extra_session(store, scope=focus.scope_contents, repository=repository, kana_kinds=kana_kinds,
                              course_kinds=course_kinds, japanese_input=japanese_input, serve=review_filter.current,
                              on_stop=review_filter.end_extra)
        parts = ExtraParts(lambda now: extra.states.read(now, with_part=False), lambda: lesson_firsts(store),
                           study_day=lambda at: store.study_day(at).isoformat(), scope=focus.scope_contents)
        return cls(store, preferences, events, dispatcher, ProbeMemo(), launch_notes,
                   ArrangementPresenter(store, preferences, events, dispatcher, launch_notes, focus=focus,
                                        due=DueReviews(store), review_filter=review_filter),
                   catalog, focus, practice_credit(store), queue_practice(store, repository=repository), extra,
                   consolidation(store, repository=repository, course_kinds=course_kinds,
                                 japanese_input=japanese_input, serve=review_filter.current),
                   review_filter, parts)

    def serves(self, store) -> bool:
        return self.store is store

    def rearrange(self, identities) -> None:
        """Design 12 B5: re-arrange the main round once for new content it could not show.

        The memo records ``identities`` first (never a second re-arrangement
        for them), then the same restart as a focus change.
        """
        self.probe_memo.rearranged(identities)
        _restart_main_round(self.store)

    def hold_note_once(self) -> bool:
        """True the first time it is asked in the current main round (design 12 G3's line, once per round)."""
        from ..practice_round import round_id
        return self.probe_memo.first_note(round_id(self.store))

    def choose_orientation(self, mode) -> None:
        """Store the learner's orientation (the home's never-chosen card) and re-arrange the main round once.

        The kana depth and the focus are left as they are (the orientation
        screen's 确定 can change those too); the restart is the one a focus
        change makes, so what is in progress stays first.
        """
        self.preferences.set_orientation(mode)
        _restart_main_round(self.store)

    def renew_after_break(self, now, *, leave_alone: Callable[[], bool] = lambda: False) -> bool:
        """Design 12 B11: a round does not outlive its sitting; True when it was re-arranged now.

        Called at the start of a main-lane "继续学习", before the round is
        resumed. When the active round's last dispatch lies before a sitting
        break (rules/clock.sitting_break: more than 30 minutes, or another
        study day), the same restart as a focus change ends it and the caller
        collects a fresh round, what is in progress first. Both orientations.
        ``leave_alone()``, asked only after a break: another owner manages the
        round now (extra practice), so it is not touched.
        """
        from ..practice_round import last_dispatch
        from .rules.clock import sitting_break
        store = self.store
        previous = last_dispatch(store)
        renewed = bool(previous is not None and sitting_break(
            previous, store.study_day(previous).isoformat(), now, store.study_day(now).isoformat()))
        if renewed and leave_alone():
            return False
        if renewed:
            _restart_main_round(store)
        return renewed

    def dispatched(self, now) -> None:
        """Remember ``now`` as the active main round's last dispatch (memory only; design 12 B11)."""
        from ..practice_round import mark_dispatch
        mark_dispatch(self.store, now)

    def scope_contents(self) -> frozenset[str] | None:
        """The focus scope's content for the scheduling (one settings read without a scope); None for none."""
        return self.focus.scope_contents()

    def serving(self, serve):
        """The block in which this store's lanes read ``serve`` (services.review_filter.serving, design 12 H10)."""
        from .services.review_filter import serving
        return serving(self.store, serve)

    def serving_now(self):
        """The review filter of the running "继续学习" or home refresh on this store, None outside one."""
        from .services.review_filter import serving_filter
        return serving_filter(self.store)

    def serve_filter(self, now):
        """The review filter of ``now`` for one "继续学习" (design 12 H10): the lift lasts while the sitting does."""
        self.review_filter.touch(now)
        return self.review_filter.current(now)


def practice_credit(store, learning=None) -> PracticeCredit:
    """PracticeCredit over ``store`` (``learning``: the caller's LearningStore of it, else a new one)."""
    return PracticeCredit(StoreStates(store, learning))


def practice_sitting(store, learning=None) -> PracticeSitting:
    """A new, empty sitting of extra practice over ``store``: marks and positions in memory only."""
    states = StoreStates(store, learning)
    return PracticeSitting(states, StorePracticeMoves(store, states))


def practice_deck(store, items: Iterable[DeckItem], *, sitting: PracticeSitting | None = None, learning=None,
                  repository: Callable[[], object] | None = None, course_kinds: Iterable[str] = (),
                  system_japanese_input: bool = False, consolidation: bool = False,
                  avoid_last: bool = False) -> PracticeDeck:
    """A practice deck of ``items`` over ``store`` (design 12 E5).

    ``sitting``: the sitting it belongs to (a new one when None);
    ``repository``: the content repository, for kana questions built on the
    spot; ``course_kinds``: the learner's enabled course kinds, for course
    questions built on the spot (none: no course question is built).
    ``consolidation``: a consolidation deck (design 12 N5): its exposure
    rows carry practice:consolidation: event ids. ``avoid_last``: a course question is planned with another
    template than the state's most recent exposure whenever the ability has one (the consolidation deck always;
    extra practice's rotation, design 12 E10).
    """
    states = StoreStates(store, learning)
    sitting = sitting or PracticeSitting(states, StorePracticeMoves(store, states))
    questions = StoreDeckQuestions(KanaDeckLane(store, repository),
                                   CourseDeckLane(store, states.learning if course_kinds else learning, course_kinds,
                                                  system_japanese_input=system_japanese_input,
                                                  avoid_last=consolidation or avoid_last))
    exposure = StorePracticeExposure(store, learning)
    return PracticeDeck(items, credit=PracticeCredit(states), sitting=sitting, questions=questions,
                        exposure=ConsolidationExposure(exposure) if consolidation else exposure)


def queue_practice(store, *, repository: Callable[[], object] | None = None) -> QueuePractice:
    """The kana queue's practice side over ``store``; checkpoint moves go through StudyStore.save_session_progress."""
    states = StoreStates(store)

    def checkpoint(session_id: str, index: int, session_right: int, assisted: Sequence[int]) -> None:
        store.save_session_progress(session_id, index, session_right, list(assisted), {})

    def new_deck(items: Sequence[DeckItem], sitting: PracticeSitting) -> PracticeDeck:
        return practice_deck(store, items, sitting=sitting, repository=repository)

    return QueuePractice(PracticeCredit(states), new_sitting=lambda: practice_sitting(store),
                         new_deck=new_deck, exposure=StorePracticeExposure(store), checkpoint=checkpoint,
                         advanced_at=StorePracticeMoves(store, states).last_advanced)


def extra_session(store, *, scope: Callable[[], frozenset[str] | None] = lambda: None,
                  repository: Callable[[], object] | None = None,
                  kana_kinds: Callable[[], Iterable[str]] | None = None,
                  course_kinds: Callable[[], Iterable[str]] | None = None,
                  japanese_input: Callable[[], bool] | None = None,
                  serve: Callable[..., object] = lambda now, **kwargs: None,
                  on_stop: Callable[[], object] = lambda: None) -> ExtraSession:
    """Extra practice over ``store`` (design 12 E1-E8): the pool reader, a new sitting per start, decks over it.

    ``serve(now, with_part=...)``: the review filter of the moment (design 12 H10; ReviewFilterService.current);
    the pool holds only what it allows."""
    kana_kinds = kana_kinds or (lambda: KanaDepthSetting.kinds_in_force(KanaDepthSetting(store).preferences()))
    course_kinds = course_kinds or (lambda: _stored_course_kinds(store))
    japanese_input = japanese_input or (lambda: False)
    states = StoreExtraStates(store, kana_kinds=kana_kinds, course_kinds=course_kinds, scope=scope, serve=serve)

    def new_deck(items: Sequence[DeckItem], sitting: PracticeSitting) -> PracticeDeck:
        return practice_deck(store, items, sitting=sitting, repository=repository, course_kinds=course_kinds(),
                             system_japanese_input=japanese_input(), avoid_last=True)

    return ExtraSession(states, new_sitting=lambda: practice_sitting(store), new_deck=new_deck, on_stop=on_stop)


def consolidation(store, *, repository: Callable[[], object] | None = None,
                  course_kinds: Callable[[], Iterable[str]] | None = None,
                  japanese_input: Callable[[], bool] | None = None,
                  serve: Callable[..., object] = lambda now: None) -> Consolidation:
    """The consolidation deck over ``store`` (design 12 N5): its facts from the store, its decks practice only.

    ``serve(now)``: the review filter of the moment (design 12 H10); a deck holds only what it allows."""
    course_kinds = course_kinds or (lambda: _stored_course_kinds(store))
    japanese_input = japanese_input or (lambda: False)
    source = StoreConsolidation(store)

    def new_deck(items: Sequence[DeckItem]) -> PracticeDeck:
        return practice_deck(store, items, repository=repository, course_kinds=course_kinds(),
                             system_japanese_input=japanese_input(), consolidation=True)

    return Consolidation(source.facts, new_deck, serve=serve)


def _stored_course_kinds(store) -> tuple[str, ...]:
    """The learner's enabled course kinds as stored (the list LearningStore.submit checks)."""
    from ..learning_questions import stored_enabled_kinds
    return tuple(stored_enabled_kinds(store.get_setting("learning_preferences_v1", {})))


def _restart_main_round(store) -> None:
    """One new scheduling pass of the main lane (design 12 H4, 1.7(3))."""
    from ..practice_round import restart
    restart(store, lane="main")
