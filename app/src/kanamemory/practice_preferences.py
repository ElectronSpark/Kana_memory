"""Pure practice defaults and legacy preference normalization; no App imports."""

PRACTICE_KINDS = {
    "综合自适应练习": "adaptive_mixed",
    "假名综合练习": "kana_mixed", "词汇综合练习": "word_mixed",
    "看假名选读音": "kana_choice", "看假名拼罗马音": "kana_romaji",
    "看罗马音选假名": "romaji_kana", "看词义和罗马音选假名": "word_meaning_kana",
    "看罗马音从完整假名表选择": "romaji_kana_full", "看罗马音手写假名": "romaji_handwriting",
    "听音手写假名": "kana_handwriting", "听词选假名": "word_kana",
}
MIXED_PRACTICE_KINDS = {
    "adaptive_mixed": ("kana_choice", "romaji_kana", "kana_romaji", "romaji_kana_full",
                       "romaji_handwriting", "kana_handwriting", "word_meaning_kana", "word_kana"),
    "kana_mixed": ("kana_choice", "kana_romaji", "romaji_kana", "romaji_kana_full", "romaji_handwriting", "kana_handwriting"),
    "word_mixed": ("word_meaning_kana", "word_kana"),
}
ALL_MIXED_KINDS = MIXED_PRACTICE_KINDS["adaptive_mixed"]
DEFAULT_MIXED_KINDS = tuple(kind for kind in ALL_MIXED_KINDS if kind != "kana_handwriting")
KANA_WORD_PRACTICE_KINDS = {"word_kana", "word_meaning_kana"}
WORD_PRACTICE_KINDS = KANA_WORD_PRACTICE_KINDS | {"word_romaji"}
WORD_SETUP_KINDS = WORD_PRACTICE_KINDS | {"word_mixed", "adaptive_mixed"}
LISTENING_PRACTICE_KINDS = {"kana_handwriting", "word_kana"}
HANDWRITING_PRACTICE_KINDS = {"kana_handwriting", "romaji_handwriting"}
PRACTICE_SCRIPTS = {"平假名与片假名（逐题分开）": "both", "平假名": "hiragana", "片假名": "katakana"}
WORD_DRILL_SCRIPTS = {"词条原有字形": "native", "平假名": "hiragana", "片假名": "katakana"}
WORD_TABLES = {"部分假名（较容易）": "partial", "完整假名表（较难）": "full"}
PRACTICE_WORD_COLLECTIONS = {
    "全部可用词汇": None, "基础词": "foundation_listening", "英语借词": "english_loanwords",
    "核心词": "japanese_core", "日常补充": "everyday_supplement",
}
PRACTICE_SCOPES = {
    "清音与拨音": ("basic",), "浊音": ("dakuten",), "半浊音": ("handakuten",),
    "拗音": ("yoon",), "外来语组合（含ヴ）": ("foreign_combination", "foreign_voiced"),
}
DEFAULT_PRACTICE_SCOPES = tuple(PRACTICE_SCOPES)[:4]
PRACTICE_COUNTS = {**{f"{count} 项": count for count in (5, 10, 15, 20, 30)}, "自定义": None}
PRACTICE_BATCHES = {"到期优先，补充新内容": False, "只学新内容": True}
PRACTICE_SCHEDULES = {"间隔复习": "spaced", "多轮强化": "reinforce"}

def practice_number(value, caption):
    text = str(value or "").strip()
    try:
        number = int(text) if text.isascii() and text.isdigit() else 0
    except ValueError:
        number = 0
    if number < 1:
        raise ValueError(f"{caption}请输入大于 0 的整数。")
    return number


def clean_preferences(value):
    value = dict(value) if isinstance(value, dict) else {}
    if isinstance(value.get("count"), str):
        value["count"] = value["count"].replace(" 题", " 项")
    # Question payloads stay intact; continuing a queue skips kinds that
    # are no longer enabled by the current preferences.
    if value.get("question_plan_version") != 2:
        value["kind"] = "综合自适应练习"
        value["script"] = "平假名与片假名（逐题分开）"
        value["mixed_kinds"] = list(DEFAULT_MIXED_KINDS)
    result = {}
    for key, options, default in (("kind", PRACTICE_KINDS, "综合自适应练习"),
            ("script", PRACTICE_SCRIPTS, "平假名与片假名（逐题分开）"), ("word_table", WORD_TABLES, "部分假名（较容易）"),
            ("count", PRACTICE_COUNTS, "10 项"),
            ("batch", PRACTICE_BATCHES, next(iter(PRACTICE_BATCHES))),
            ("schedule", PRACTICE_SCHEDULES, "间隔复习"),
            ("word_collection", PRACTICE_WORD_COLLECTIONS, "全部可用词汇")):
        candidate = value.get(key)
        result[key] = candidate if isinstance(candidate, str) and candidate in options else default
    result["research"] = value.get("research") is True
    scopes = value.get("scopes", DEFAULT_PRACTICE_SCOPES)
    result["scopes"] = [item for item in scopes if isinstance(item, str) and item in PRACTICE_SCOPES] if isinstance(scopes, (list,tuple)) else list(DEFAULT_PRACTICE_SCOPES)
    try:
        result["custom_count"] = str(practice_number(value.get("custom_count", "10"), "本批记忆个数"))
    except ValueError:
        result["custom_count"] = "10"
    try:
        result["rounds"] = str(practice_number(value.get("rounds", "1"), "每种题型重复轮数"))
    except ValueError:
        result["rounds"] = "1"
    kinds = value.get("mixed_kinds", DEFAULT_MIXED_KINDS)
    result["mixed_kinds"] = list(dict.fromkeys(kind for kind in kinds
        if isinstance(kind, str) and kind in ALL_MIXED_KINDS)) if isinstance(kinds, (list, tuple)) else list(DEFAULT_MIXED_KINDS)
    # Old full/default selections were automatic. Preserve custom subsets
    # and explicit single-mode choices; migrate only the old default once.
    if (value.get("listening_handwriting_opt_in_version") != 1
            and set(result["mixed_kinds"]) == set(ALL_MIXED_KINDS)):
        result["mixed_kinds"] = list(DEFAULT_MIXED_KINDS)
    result["question_plan_version"] = 2
    result["listening_handwriting_opt_in_version"] = 1
    return result


def enabled_preference_kinds(preferences):
    preferences = clean_preferences(preferences)
    kind = PRACTICE_KINDS[preferences["kind"]]
    return (tuple(item for item in MIXED_PRACTICE_KINDS[kind]
                  if item in preferences["mixed_kinds"])
            if kind in MIXED_PRACTICE_KINDS else (kind,))
