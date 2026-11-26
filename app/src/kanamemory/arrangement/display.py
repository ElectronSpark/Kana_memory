"""Display models of the study arrangement (design 12 UI1-UI6, design 13 C2).

Pure, immutable, plain data: strings, numbers, enums and tuples. No
widgets, stores, services or callbacks. The presenter
(services/presenters.ArrangementPresenter) composes every sentence; a page
only lays them out. Actions are named by data (an orientation, a depth
level, a section id), never by a closure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import DeckAnswerMode, Focus, FocusScope, KanaDirection, KanaTilt, Orientation, Tilt
from .rules.depth import KanaDepth

# Fixed wording (design 12 UI1, UI3, UI4, UI5).
BREADTH_TITLE = "广度优先"
BREADTH_TEXT = "新内容和复习穿插着来；刚学的先巩固，旧课的复习做不完也继续学新的。"
# The learner-facing name of THOROUGH since 2026-11-18 (it reads as the counterpart of 广度优先).
THOROUGH_TITLE = "深度优先"
THOROUGH_TEXT = "先把到期的复习做完，再学新的。"
DEPTH_TITLES = {KanaDepth.RECOGNITION: "只认读", KanaDepth.SPELLING: "认读＋拼写",
                KanaDepth.HANDWRITING: "认读＋拼写＋手写"}
KEEP_TITLE = "保持现在的题型设置"
KEEP_TEXT = "不改动现在的题型开关。"
INVITE_TEXT = "可以调整学习方式：现在是先复习完再学新的。去看看"
# The orientation never chosen (stored): said in plain words on the home card and the orientation screen.
UNCHOSEN_TEXT = "学习方式还没有选。现在按“深度优先”运行：先把到期的复习做完，再学新的。"
UNCHOSEN_PAGE_TEXT = "还没有选择学习方式。现在按“深度优先”运行：先把到期的复习做完，再学新的。"
CHOOSE_BREADTH = "改为广度优先"
KEEP_THOROUGH = "保持深度优先"
# The orientation screen's two cards (2026-11-18): the one in force says so; the other has its button.
IN_USE = "当前使用"
RECOMMENDED = "推荐"
SWITCH_TO = {"breadth": "改用广度优先", "thorough": "改用深度优先"}
CHANGED_TEXT = "已改为{title}，从下一题起生效。"
# 深度优先 holds new lessons back while reviews are due: the rule, said once per round on the practice page.
THOROUGH_HOLD_TEXT = "深度优先：新课在一轮开始时、或答完 {count} 道到期复习后才开始；已加入 {lessons} 课还没学。"
NO_NEW_TEXT = "已加入的内容都学过了"
ADD_NEXT = "加入下一小节"   # the home page's existing caption, followed by " · <section title>"
ADD_NEXT_AND_START = "加入下一小节并开始"
REVIEW_FIRST = "先复习"
PROGRESS_NOTE = "广度优先时复习排得晚是正常的；想少一些，可以放慢加入新小节、停掉不需要的方向，或改用深度优先。"
PROGRESS_HEADING = "时间与进度"
# Where the three units are named (design 12 section 21, N3): the learning day's consolidation is new content.
RECENT_UNITS_NOTE = "刚学的内容当天的巩固算在新内容里；最近 14 天新学的，到期复习排在最前。"
REVIEWS_HEADING = "复习"
NO_REVIEWS_TEXT = "还没有复习记录"
# The course review line's count of today (design 12 UI4): the answers the home page's 课程复习 minutes are made of.
ANSWERED_TODAY_TEXT = "今日已答 {count} 题"
# D2 when a depth level rewrites the mixed switches but the kinds in force stay the same (a single-kind plan).
SINGLE_KIND_UNCHANGED_TEXT = "只改综合练习的题型开关；现在用的单一题型练习不受影响。"
PLAN_UNCHANGED_TEXT = "只改综合练习的题型开关；现在用的练习不受影响。"
COURSE_REVIEW_TITLE = "课程复习"
DIRECTION_TITLES = {KanaDirection.READING: "识读", KanaDirection.SPELLING: "拼写",
                    KanaDirection.HANDWRITING: "手写"}
# The pass rate looks at this many of the latest formal reviews (UI4).
PASS_RATE_REVIEWS = 40
# The focus (design 12 H1, UI6).
FOCUS_HEADING = "学习重点"
TILT_HEADING = "新内容与复习"
KANA_TILT_HEADING = "复习里的假名"
SCOPE_HEADING = "范围"
TILT_TITLES = {Tilt.ONLY_NEW: "只学新的", Tilt.NEW: "多学新的", Tilt.EVEN: "不偏", Tilt.REVIEW: "多复习",
               Tilt.ONLY_REVIEW: "只复习"}
KANA_TILT_TITLES = {KanaTilt.LESS: "假名少", KanaTilt.NORMAL: "正常", KanaTilt.MORE: "假名多"}
TILT_THOROUGH_TEXT = "偏重只在广度优先时起作用；深度优先是先把到期的复习做完再学新的，没有份额可调。"
KANA_SHARE_TEXT = "假名只占自己的那一份；课程复习用不完的时间留给新内容。"
SCOPE_TEXT = "范围内的先做；范围内都做完才轮到别的，别的复习会排得更晚。"
CHANGE_TEXT = "改重点会重新排一次当前这一轮。"
NO_SCOPE_TITLE = "不设范围"
SCOPE_GONE_TITLE = "所选范围"
SCOPE_GONE_TEXT = "已停用或不存在，暂按没有范围安排"
CANCEL_FOCUS = "取消重点"
# The learner's two switches (design 12 H8, H9) on the focus part; the off state of 只做范围内 is SCOPE_TEXT's 先做.
FIRST_TITLE = "先做范围内"
ONLY_TITLE = "只做范围内"
ONLY_TEXT = "打开后，“继续学习”和加练只出范围内的内容；范围外到期的照常累计，随时可以关掉。"
ONLY_ON = "改为只做范围内"
ONLY_OFF = "改为先做范围内"
PAUSED_HEADING = "暂不复习"
PAUSED_TEXT = ("列在这里的部分不出题：复习、新课和加练都不出。学习记录和到期时间不变，恢复后照常安排。"
               "被暂不复习的内容如果是别的课的前置，那些课会等它。")
PAUSED_NONE = "现在没有暂不复习的部分。"
PAUSED_RESTORE = "恢复"
PAUSED_ADD = "添加…"
PAUSED_FULL = "最多 {count} 个部分；先恢复一个再添加。"
PAUSE_CHOOSER_TEXT = "选一个部分暂不复习："
PAUSE_PICK = "暂不复习这一部分"
PAUSE_BACK = "返回"
# H8's end page, H10's two apart lines and the home's 加练… entry (E9).
SCOPE_DONE_TITLE = "范围内的都做完了"
SCOPE_DONE_OUTSIDE = "范围外到期 {count} 题"
SCOPE_DONE_EXTRA = "继续加练（范围内）"
SCOPE_DONE_LIFT = "做范围外的"
SCOPE_DONE_BACK = "回到今日"
SCOPE_DONE_HINT = "只做范围内：范围外的到期照常累计，不出题。"
PAUSED_LINE = "暂不复习 {parts} 个部分，其中到期 {count} 题"
EXTRA_ENTRY = "加练…"
DROP_SCOPE = "改为不设范围"
SET_FOCUS = "设为重点"
FOCUS_SET_TEXT = "这是现在的学习重点。"
# A scope title shown twice in the scope choice gets its kind beside it.
SCOPE_KIND_TITLES = {"route": "路线", "node": "路线的一部分", "library": "内容库", "family": "类别"}


@dataclass(frozen=True)
class OrientationOption:
    mode: Orientation
    title: str   # 广度优先 / 深度优先
    text: str    # the explanatory sentence of UI1


@dataclass(frozen=True)
class DepthOption:
    """One choice of "假名练到哪一步". ``level`` None is "keep the current switches"."""

    level: KanaDepth | None
    title: str
    text: str           # the consequence sentence (D2), its number already in it
    stopping_due: int   # kana cards due now that this choice would stop serving (0 for keep)


@dataclass(frozen=True)
class OrientationModel:
    """The orientation screen (UI1 with D1, D2). Nothing is written until the page confirms.

    ``chosen`` is the stored orientation, None when never chosen.
    ``current_depth`` is the level the current switches match, None when none.
    ``depths`` are the three levels and, only when ``current_depth`` is None,
    a last option with level None that keeps the switches. ``preselected``
    is the orientation in force: the chosen one, or THOROUGH when never chosen
    (nothing on the screen shows another one as active); use
    preselected_depth(mode) for the depth to preselect (None means the keep
    option). ``recommended`` (BREADTH) is marked 推荐 while nothing is chosen;
    ``notice`` is the confirming line after a card's button stored a choice.
    """

    chosen: Orientation | None
    orientations: tuple[OrientationOption, ...]
    depths: tuple[DepthOption, ...]
    current_depth: KanaDepth | None
    preselected: Orientation
    breadth_depth: KanaDepth | None
    thorough_depth: KanaDepth | None
    recommended: Orientation | None = None
    notice: str | None = None

    def preselected_depth(self, mode: Orientation) -> KanaDepth | None:
        return self.breadth_depth if Orientation(mode) is Orientation.BREADTH else self.thorough_depth

    def depth_option(self, level: KanaDepth | None) -> DepthOption:
        return next(option for option in self.depths if option.level == level)


@dataclass(frozen=True)
class SectionNote:
    """B10: no new content to learn; the route's next section is offered first (never added automatically).

    ``add_and_start`` / ``review_first`` are the two button captions of the
    once-per-launch prompt before the first "继续学习".
    """

    section_id: str
    title: str
    text: str = NO_NEW_TEXT
    add_and_start: str = ADD_NEXT_AND_START
    review_first: str = REVIEW_FIRST
    # The route of the section: None for the current route (as before WP-H); a route id when the
    # section is the next one inside the learner's focus scope (B10 with a scope, H6).
    route_id: str | None = None
    # The first part of the two-line design: "建议先加 · <line>" when the section is the suggestion, and why
    # (only when the suggestion changed the line); "" otherwise.
    mark: str = ""
    reason: str = ""

    @property
    def add_next(self) -> str:
        """The caption of the button that adds this section (the home page's existing wording)."""
        return f"{ADD_NEXT} · {self.title}"


@dataclass(frozen=True)
class HomeRowsModel:
    """The arrangement's rows on the home page (UI2, UI3, UI5).

    ``invite`` is True when the orientation was never chosen; ``invite_text``
    is then the card's sentence (UNCHOSEN_TEXT), else None; the card offers
    ``choose_breadth`` and ``keep_thorough``, which store the choice at once.
    Under breadth only: ``progress_line`` (UI2; without its 7-day part when
    the clock window does not cover the 7 days, and None when the route is
    empty as well), ``usage_line`` (UI2, None when all three minutes are 0 or
    the window does not cover today; it leads to the orientation screen)
    and ``note`` (B10, None unless the caller said there is no new content
    and the route has a next section; with a focus scope that still has a
    section not added, the scope's next section, its ``route_id`` set).
    Under thorough or a missing setting all three are None. The focus row
    is a model of its own (FocusRowModel, presenter.home_focus).
    """

    invite: bool = False
    invite_text: str | None = None
    choose_breadth: str = CHOOSE_BREADTH
    keep_thorough: str = KEEP_THOROUGH
    progress_line: str | None = None
    usage_line: str | None = None
    note: SectionNote | None = None
    usage_note: str | None = None   # RECENT_UNITS_NOTE under the usage line (design 12 N3)
    breadth: bool = False   # the orientation read for these rows is 广度优先 (the home's line mark follows it)


@dataclass(frozen=True)
class FocusRowModel:
    """The home page's focus row (UI6), under every orientation; there is no row without a focus.

    ``line`` "重点：<scope title> · <tilt title>" with only the parts that are
    set and act (the tilts only under breadth); tapping it opens the
    orientation screen. ``cancel`` the caption of the action beside it
    (取消重点; the page calls its cancel callback, which the wiring maps to
    FocusService.clear). ``outside_due`` the due reviews outside the scope
    that 继续学习 can serve under the learner's kinds (presenters.
    ArrangementPresenter.outside_due: due work, not questions answerable this
    minute) and ``outside_due_line`` "重点之外到期 N 题"; both None without a
    scope that resolves.
    """

    line: str
    cancel: str = CANCEL_FOCUS
    outside_due: int | None = None
    outside_due_line: str | None = None
    only: bool = False   # 只做范围内 acts (H8): the line names it and outside_due_line is H10's 范围外到期 N 题


@dataclass(frozen=True)
class TiltOption:
    """One of the five new-against-review tilts: ``share`` "70:30", ``text`` "多学新的 70:30"."""

    tilt: Tilt
    title: str
    share: str
    text: str


@dataclass(frozen=True)
class KanaTiltOption:
    """One of the three kana tilts inside review: ``share`` course:kana "85:15", ``text`` "假名少 85:15"."""

    kana: KanaTilt
    title: str
    share: str
    text: str


@dataclass(frozen=True)
class ScopeOption:
    """A scope the screen offers; ``scope`` None is "no scope" (不设范围)."""

    scope: FocusScope | None
    title: str


@dataclass(frozen=True)
class OnlySwitchModel:
    """只做范围内 on the focus part (H8), as a pair: 先做范围内 (SCOPE_TEXT) and 只做范围内 (ONLY_TEXT), each with
    its sentence; the stored one says 当前使用, the other has its button (stored at once). ``acts`` False:
    the stored scope does not resolve, so neither acts (the part's scope line says so)."""

    on: bool
    acts: bool = True
    first_title: str = FIRST_TITLE
    first_text: str = SCOPE_TEXT
    only_title: str = ONLY_TITLE
    only_text: str = ONLY_TEXT
    turn_on: str = ONLY_ON
    turn_off: str = ONLY_OFF
    in_use: str = IN_USE


@dataclass(frozen=True)
class PausedRow:
    """One scope listed under 暂不复习 with its title (the scope line's wording when it no longer resolves)."""

    scope: FocusScope
    title: str
    restore: str = PAUSED_RESTORE


@dataclass(frozen=True)
class PausedModel:
    """The section 暂不复习 (H9): the note, the listed scopes each with 恢复, and 添加… (``can_add`` False at the
    limit, then ``full_text``)."""

    rows: tuple[PausedRow, ...] = ()
    can_add: bool = True
    heading: str = PAUSED_HEADING
    text: str = PAUSED_TEXT
    none_text: str = PAUSED_NONE
    add: str = PAUSED_ADD
    full_text: str | None = None


@dataclass(frozen=True)
class PauseChooserModel:
    """添加… under 暂不复习: the focus scope's choices (routes, libraries, families, the stored scope) not paused yet."""

    options: tuple[ScopeOption, ...]
    header: str = PAUSED_HEADING
    text: str = PAUSE_CHOOSER_TEXT
    pick: str = PAUSE_PICK
    back: str = PAUSE_BACK


@dataclass(frozen=True)
class ScopeDoneModel:
    """H8's end page 范围内的都做完了: ``outside_line`` (None at 0), 继续加练（范围内） only when something inside can
    be practised (``extra`` None otherwise), 做范围外的 and 回到今日."""

    outside_line: str | None
    extra: str | None
    title: str = SCOPE_DONE_TITLE
    hint: str = SCOPE_DONE_HINT
    lift: str = SCOPE_DONE_LIFT
    back: str = SCOPE_DONE_BACK


@dataclass(frozen=True)
class FocusModel:
    """The third part of the orientation screen: the learner's focus (UI6, H1).

    ``focus`` the stored focus (preselect from it). ``tilts`` (five) and
    ``kana_tilts`` (three) are offered only under breadth: offers_tilts(mode)
    for the orientation picked on the screen; ``tilt_note`` is the sentence
    to show instead under thorough. ``scope_line`` says the current scope:
    its title, the fact that it no longer resolves (``scope_resolves``
    False), or that none is set. ``scopes``: none first, then the stored
    scope when the list below would not hold it (a node scope set from a
    route page, or a scope that no longer resolves; titled as in
    ``scope_line``), then each enabled route, each usable content library,
    each family; titles are unique (a repeated one carries its kind).
    ``kana_note`` is the sentence under the kana tilt (B4: kana takes only
    its own share). ``drop_scope`` is the caption of the page's offer to pick "no scope"
    while the stored scope does not resolve. ``scope_text`` and
    ``change_text`` are the two sentences of UI6. Confirming calls the
    focus service's change() with the picked tilt, kana tilt and scope.
    """

    focus: Focus
    tilts: tuple[TiltOption, ...]
    kana_tilts: tuple[KanaTiltOption, ...]
    scopes: tuple[ScopeOption, ...]
    scope_line: str
    scope_resolves: bool = True
    heading: str = FOCUS_HEADING
    tilt_heading: str = TILT_HEADING
    kana_heading: str = KANA_TILT_HEADING
    scope_heading: str = SCOPE_HEADING
    tilt_note: str = TILT_THOROUGH_TEXT
    kana_note: str = KANA_SHARE_TEXT
    scope_text: str = SCOPE_TEXT
    change_text: str = CHANGE_TEXT
    drop_scope: str = DROP_SCOPE
    only_switch: OnlySwitchModel | None = None   # only while a scope is stored (H8)
    paused: PausedModel | None = None            # the section 暂不复习 (H9)

    @staticmethod
    def offers_tilts(mode: Orientation) -> bool:
        """The tilts act only under breadth first (H1): the page shows them, or ``tilt_note``."""
        return Orientation(mode) is Orientation.BREADTH


@dataclass(frozen=True)
class FocusButtonModel:
    """设为重点 on the page of a route, stage, unit, section or content library (UI6, H6).

    ``scope`` the page's own object as a focus scope. ``current`` True when
    it is the stored focus scope: ``caption`` is then 取消重点 and ``lines``
    say so with UI6's sentence (范围内的先做……); otherwise ``caption`` is
    设为重点 and ``lines`` is empty. The page calls its set callback with
    ``scope`` or its cancel callback; the wiring maps them to the focus
    service (with_scope), which rearranges the current round once.
    """

    scope: FocusScope
    current: bool
    caption: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class DayMinutes:
    """Whole minutes the study clock charged on one study day (``day``: ISO date of the learner's study day).

    ``line`` is the shown line, its label (month-day) and minutes already in it.
    """

    day: str
    new: int
    course: int
    kana: int
    line: str = ""


@dataclass(frozen=True)
class ReviewFigures:
    """Review figures of course review or one kana direction (UI4).

    ``due``: questions due now and not answered. ``longest_overdue_days``:
    whole days since the earliest due among them, None when nothing is due.
    ``pass_percent``: rounded percentage of passed (rating > 1) answers among
    the latest PASS_RATE_REVIEWS formal reviews, None when there is none;
    ``reviews`` is how many it is based on; ``basis`` is that number when
    fewer than PASS_RATE_REVIEWS (to show beside the rate), else None.
    ``answered_today`` (course review only, None for kana): today's course
    answers the study clock charges to 课程复习, the events of the home page's
    "今日 … 课程复习 N 分". ``line`` is the shown line with all of these already in it.
    """

    title: str
    direction: KanaDirection | None   # None for course review
    due: int
    longest_overdue_days: int | None
    pass_percent: int | None
    reviews: int
    line: str = ""
    answered_today: int | None = None

    @property
    def basis(self) -> int | None:
        return self.reviews if 0 < self.reviews < PASS_RATE_REVIEWS else None


@dataclass(frozen=True)
class ProgressModel:
    """"时间与进度" on the records page (UI4), shown under every orientation; read-only.

    ``days``: the last 7 study days, oldest first, today last. ``new_lessons``
    and ``new_kana``: lessons and kana first learned on those days.
    ``route_position`` / ``route_total``: N and M of 第 N／M 小节 (RouteFacts).
    ``route_line``: "路线：第 N／M 小节" ("路线：没有小节" for an empty route);
    ``learned_line``: "近 7 天新学 L 课 · 新假名 K 个".
    ``lines_line``: "课程用时 · 近 7 天：词汇 N 分 · 语法 M 分" (design 12 LS9), the
    minutes the clock charged to course actions of each study line on the 7
    days; "" when both are 0 (the page leaves the line out).
    ``reviews``: course review, then 识读, 拼写, 手写. ``note``: the fixed sentence.
    ``heading``, ``days_heading`` ("近 7 天每天用时（分钟）") and
    ``reviews_heading``: the block's headings. Every shown text is composed
    here or by the presenter; the page only lays the lines out.
    """

    days: tuple[DayMinutes, ...]
    new_lessons: int
    new_kana: int
    route_position: int
    route_total: int
    route_line: str
    learned_line: str
    reviews: tuple[ReviewFigures, ...]
    days_heading: str = ""
    lines_line: str = ""
    heading: str = PROGRESS_HEADING
    reviews_heading: str = REVIEWS_HEADING
    note: str = PROGRESS_NOTE
    units_note: str = RECENT_UNITS_NOTE


# The consolidation deck of what was taught today (design 12 N5).
CONSOLIDATION_HEADER = "巩固"
CONSOLIDATION_NOTE = "刚学的再练一遍：只算练习，不评分。"
CONSOLIDATION_LEAVE = "先到这里"

# The practice deck and practice-only answers (design 12 E5, UI8). No rating word appears here:
# a practice answer is never rated, and a miss is never called "重来" or "遗忘".
PRACTICE_HEADER = "练习"
PRACTICE_CORRECT = "答对了"
PRACTICE_RETRY = "稍后再练一次"
PRACTICE_ADVANCED = "下次复习提前了一点"
PRACTICE_ONLY_NOTE = "24 小时内已经评过分，这次只算练习，不改复习安排。"
PRACTICE_ONLY_MOVED_NOTE = "24 小时内已经评过分，这次只算练习。"   # with 下次复习提前了一点 after it
PRACTICE_CONTINUE = "继续"
PRACTISED_LINE = "只算练习 {count} 题（不评分）"   # the end page of a batch (design 12 E4)
DECK_MISSES_NOTE = "刚才答错的再练一次：只算练习，不评分。"
DECK_SUBMIT = "提交"
DECK_INPUT = "输入答案"
DECK_REVEAL = "不会，看答案"
DECK_SHOW_ANSWER = "想好了，看答案"
DECK_SELF_RIGHT = "我答对了"
DECK_SELF_WRONG = "没答对"
DECK_NEXT = "下一题"
DECK_AGAIN = "再来一组"   # extra practice: after the last question of a deck, while the mode can serve more (N13)
DECK_GO_ON = "继续学习"   # the consolidation deck: after its last question 继续学习 goes on (N5, N13)
DECK_LEAVE = "结束练习"
DECK_FINISH = "完成"
DECK_PLAY = "播放读音"
DECK_ANSWER_PREFIX = "答案："
DECK_DONE_TITLE = "这组练习做完了"


@dataclass(frozen=True)
class PracticeResultModel:
    """The result of one practice-only answer (UI8): never a rating.

    ``verdict``: 答对了 or 稍后再练一次 (empty when the answer could not be scored);
    ``lines``: further sentences in order (on the queue's result page the
    practice-only note, then 下次复习提前了一点 after a confirmed miss that
    moved the due; the judge's own note for an answer it could not score);
    ``continue_caption``: the one button that goes on.
    """

    verdict: str
    lines: tuple[str, ...] = ()
    correct: bool | None = None
    continue_caption: str = PRACTICE_CONTINUE


@dataclass(frozen=True)
class DeckQuestionModel:
    """One question of the practice deck as the page shows it (header 练习).

    ``mode`` decides the controls: option buttons, a text field with
    ``submit_caption``, or (self-check) ``show_answer_caption`` and then the
    answer with the two self-report buttons. ``answer`` is shown only in
    self-check mode, after the learner asks for it.
    """

    header: str
    progress: str
    prompt: str
    mode: DeckAnswerMode
    task: str = ""
    note: str = ""
    options: tuple[str, ...] = ()
    audio_text: str = ""
    answer: str = ""
    answer_cells: object = field(default=None, compare=False)   # DeckTexts.answer_cells, for the shown answer
    cue: object = field(default=None, compare=False)   # DeckTexts.cue: the task as the question page shows it (N12)
    # DeckTexts.prompt_view: the prompt's widgets as the question page draws them (N12); None: ``prompt`` as a label.
    prompt_view: object = field(default=None, compare=False)
    play_caption: str = DECK_PLAY
    submit_caption: str = DECK_SUBMIT
    input_hint: str = DECK_INPUT
    reveal_caption: str = DECK_REVEAL
    show_answer_caption: str = DECK_SHOW_ANSWER
    self_right_caption: str = DECK_SELF_RIGHT
    self_wrong_caption: str = DECK_SELF_WRONG
    leave_caption: str = DECK_LEAVE


@dataclass(frozen=True)
class DeckResultModel:
    """The deck's result page of one answer: the UI8 result, the answer and its explanation; no rating."""

    header: str
    result: PracticeResultModel
    answer_line: str = ""
    answer_cells: object = field(default=None, compare=False)   # DeckTexts.answer_cells, for answer_line
    explanation: str = ""
    next_caption: str | None = DECK_NEXT   # None: no way on after the deck's last question (N13)
    leave_caption: str = DECK_LEAVE
    scored: bool = True   # False: the answer could not be scored; the page offers the same question again


@dataclass(frozen=True)
class DeckEndModel:
    """The end of a deck: what was practised, and the one way on."""

    header: str
    title: str
    summary: str
    finish_caption: str = DECK_FINISH


# Extra practice (design 12 E1, E5, UI7, E9): the button on the end and wait pages of 继续学习, the page 加练 with
# page before the first start, and the deck's header and leave button while the mode is on.
EXTRA_BUTTON = "继续加练"
EXTRA_HEADER = "加练"
EXTRA_INTRO_TEXT = "这些题还没到期。24 小时内评过分的只算练习；同一题连着答错两次，它的下次复习会提前一点。"
# With course questions answerable now (the home page's 现在可答 count of the course), the page says first that the
# dispatch serves them first, scored as usual; the sentences about practice follow, naming their own questions.
EXTRA_DUE_FIRST = "现在有 {count} 题到期：先出这些题，照常评分；之后才是加练。"
EXTRA_INTRO_AFTER_DUE = "加练的题还没到期。24 小时内评过分的只算练习；同一题连着答错两次，它的下次复习会提前一点。"
# The header of a question extra practice asks before its due (an early kana batch, an early course question):
# the lane's own header behind 加练, so it is told apart from a due check, which keeps the lane's header.
EXTRA_EARLY_HEADER = EXTRA_HEADER + " · {title}"
EXTRA_BACK = "回到今日"
EXTRA_END = "结束加练"
EXTRA_DECK_NOTE = "这些题 24 小时内已经评过分：只算练习，不评分。"
EXTRA_NOTHING = "现在没有可以加练的题，稍后再试。"   # the mode ended before any extra question


@dataclass(frozen=True)
class ExtraButtonModel:
    """继续加练 on an end or wait page of 继续学习 (shown only when something can be practised)."""

    caption: str = EXTRA_BUTTON


PART_TITLES = {"today": "今天新学的", "recent": "最近 14 天新学的", "scope": "学习重点范围内", "all": "全部"}
PART_COUNT = "{title} · 现在可练 {count} 个"
PART_EMPTY = "{title} · 现在没有可练的"


@dataclass(frozen=True)
class PartOption:
    """One part on the page 加练 (E9): its caption with the count; ``startable`` False at 0 (shown, no button)."""

    part: str
    caption: str
    count: int

    @property
    def startable(self) -> bool:
        return self.count > 0


@dataclass(frozen=True)
class ExtraPartsModel:
    """The page 加练 (E9): the explanation, the parts with their counts, 回到今日; ``due_line`` (EXTRA_DUE_FIRST) above
    the explanation when course questions can be answered now, else None."""

    parts: tuple[PartOption, ...]
    header: str = EXTRA_HEADER
    text: str = EXTRA_INTRO_TEXT
    back_caption: str = EXTRA_BACK
    due_line: str | None = None
