"""Bounded AI-visible capabilities, separate from permission to issue a question.

This catalogue describes real controls. Only supplied candidate references are
executable; experimental kinds and device-side recognizers are not model tools.
"""
from copy import deepcopy

COURSE = {
    "vocab_meaning": "word sense", "vocab_audio_meaning": "heard word meaning",
    "vocab_reading": "marked word reading", "vocab_recall": "word recall",
    "vocab_orthography": "word orthography", "vocab_usage": "word in context; includes Chinese-to-Japanese word banks and multiple gaps",
    "vocab_zh_ja_contrast": "Chinese/Japanese meaning contrast",
    "reading_comprehension": "sentence meaning", "listening_comprehension": "heard sentence meaning",
    "grammar_meaning": "grammar meaning", "grammar_cloze": "grammar gap; includes multiple independently editable gaps",
    "grammar_transform": "sentence transformation", "grammar_reorder": "Chinese-to-Japanese word block ordering",
    "targeted_correction": "correct the marked error",
    "controlled_sentence_production": "constrained sentence production",
    "pronunciation_rule": "reading rule; includes pitch identification",
    "pronunciation_application": "reading rule application; includes whole-sentence pitch/segmentation",
}
NATIVE = {
    "kana_choice": "kana to romaji choice", "kana_romaji": "kana to typed romaji",
    "romaji_kana": "romaji to limited kana choices",
    "romaji_kana_full": "romaji to full kana board",
    "romaji_handwriting": "romaji to handwritten kana",
    "kana_handwriting": "audio to handwritten kana",
    "word_kana": "heard word to kana spelling",
    "word_meaning_kana": "word meaning and romaji to kana spelling",
    "word_romaji": "word kana to romaji, one kana unit at a time",
}
GENERATIVE = {"vocab_meaning", "vocab_usage", "vocab_zh_ja_contrast",
              "reading_comprehension", "grammar_meaning", "grammar_cloze"}


def capability_catalog(enabled=(), candidates=()):
    enabled, eligible = set(enabled), {q.get("kind") for q in candidates}
    return [{"kind": kind, "task": task, "route": "course" if kind in COURSE else "native",
             "generation": kind in GENERATIVE, "enabled": kind in enabled,
             "candidate_available": kind in eligible}
            for kind, task in {**COURSE, **NATIVE}.items()]


def validate_catalog(value):
    if not isinstance(value, list) or len(value) != len(COURSE) + len(NATIVE):
        raise ValueError("Invalid capability catalogue")
    if any(not isinstance(row, dict) or any(type(row.get(k)) is not bool for k in
            ("enabled", "candidate_available", "generation")) for row in value):
        raise ValueError("Invalid capability flags")
    expected = capability_catalog([r["kind"] for r in value if r["enabled"]],
                                  [r for r in value if r["candidate_available"]])
    if value != expected:
        raise ValueError("Unknown capability")
    return deepcopy(value)


def question_semantics(question):
    """Public target anchors and control semantics; never infer from an answer."""
    if "presentation" in question:
        value = deepcopy(question["presentation"])
    else:
        from .question_presentation import question_presentation
        from .learning_questions import validate_practice
        meta = validate_practice(question, target_id=question.get("target_id"))
        public = question_presentation(question)
        value = {"target_spans": public["target_spans"],
                 "task_family": meta["task_family"] if meta else question.get("kind", ""),
                 "scaffold": meta["scaffold"] if meta else "unspecified",
                 "hidden_transcript": bool(question.get("audio_text") or question.get("has_hidden_transcript")),
                 "tests_reading": bool(question.get("tests_reading") or question.get("kind") == "vocab_reading"),
                 "tests_pitch": bool(question.get("tests_pitch")),
                 "has_gap": bool(question.get("has_gap") or question.get('input_mode') == 'multi_cloze'
                                 or "（　）" in question.get("prompt", "")),
                 "response_unit": "gap_slot" if question.get('input_mode') == 'multi_cloze' else "mora_and_word_boundary" if question.get("input_mode") in
                    {"pitch_fill", "pitch_identify"} else "kana_unit" if question.get("kind") == "word_romaji" else "answer"}
    fields = {"target_spans", "task_family", "scaffold", "hidden_transcript", "tests_reading",
              "tests_pitch", "has_gap", "response_unit"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Invalid question semantics")
    for key in ("hidden_transcript", "tests_reading", "tests_pitch", "has_gap"):
        if type(value[key]) is not bool:
            raise ValueError("Invalid disclosure flag")
    for key in ("task_family", "scaffold", "response_unit"):
        if not isinstance(value[key], str) or len(value[key]) > 256:
            raise ValueError("Invalid operation")
    spans = value["target_spans"]
    if not isinstance(spans, list) or len(spans) > 32 or value["hidden_transcript"] and spans:
        raise ValueError("Invalid public target")
    prompt = question.get("prompt", "")
    for span in spans:
        if (not isinstance(span, dict) or set(span) != {"start", "end", "quote"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not 0 <= span["start"] < span["end"] <= len(prompt)
                or prompt[span["start"]:span["end"]] != span["quote"]):
            raise ValueError("Public target is outside its sentence")
    return value


def public_response_parts(question):
    """Describe each visible gap without sending its solution or grading authority."""
    if question.get('input_mode') == 'multi_cloze' and 'multi_cloze' in question:
        from .multi_cloze import specification
        rows = [{key: slot[key] for key in ('id', 'start', 'end', 'quote', 'choices', 'target_id', 'role')}
                for slot in specification(question)['slots']]
        if 'response_parts' in question and question['response_parts'] != rows:
            raise ValueError('Public controls differ from their local slots')
    elif 'response_parts' in question:
        rows = deepcopy(question['response_parts'])
    else:
        return None
    fields = {'id', 'start', 'end', 'quote', 'choices', 'target_id', 'role'}
    if question.get('input_mode') != 'multi_cloze' or not isinstance(rows, list) or not 2 <= len(rows) <= 6:
        raise ValueError('Invalid public gap controls')
    prompt, ids, end = question.get('prompt', ''), set(), 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != fields
                or not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 64 or row['id'] in ids
                or type(row['start']) is not int or type(row['end']) is not int
                or not end <= row['start'] < row['end'] <= len(prompt)
                or prompt[row['start']:row['end']] != row['quote']
                or not isinstance(row['quote'], str)
                or not isinstance(row['target_id'], str) or not 1 <= len(row['target_id']) <= 1024
                or not isinstance(row['role'], str) or row['role'] not in {'primary', 'background'}
                or not isinstance(row['choices'], list) or not 2 <= len(row['choices']) <= 12
                or any(not isinstance(value, str) or not 1 <= len(value) <= 160 for value in row['choices'])
                or len(set(row['choices'])) != len(row['choices'])):
            raise ValueError('Invalid public gap anchor')
        ids.add(row['id'])
        end = row['end']
    from .multi_cloze import blank_ranges
    primary = {row['target_id'] for row in rows if row['role'] == 'primary'}
    if (len(primary) != 1 or question.get('target_id') not in primary
            or any(row['role'] == 'background' and row['target_id'] in primary for row in rows)
            or blank_ranges(prompt) != [(row['start'], row['end']) for row in rows]):
        raise ValueError('Public gaps disagree with their primary target or blanks')
    return rows


def capability_rules(questions=(), *, authoring=False, generation=False):
    """Send control-specific advice only when those public controls are present."""
    blocks = [
        "question_capabilities describes actual app controls, not permission to create or execute every type. "
        "Only supplied candidates are executable now; candidate_available=false is not evidence of forgetting. "
        "Local code owns grading, enrollment, queues, due dates and device operations."
    ]
    if authoring:
        blocks.append(
            "Content authoring and examples obey their separate item-template schema; the practice-generation "
            "kind list does not override it. Proposals remain drafts, not reviewed or published courses. "
            "One bounded request need not satisfy a whole curriculum's publication quotas.")
    if generation:
        blocks.append(
            "Standalone practice generation uses only generation=true kinds AND allowed scope kinds under the "
            "existing choice schema. Multi-gap, word-bank, pitch and native tasks must be retrieved from supplied "
            "local candidates, not freely generated.")
    questions = [q for q in questions if isinstance(q, dict)]
    if not questions:
        return "\n".join(blocks)
    blocks.append(
        "Consider kind, input_mode, presentation.task_family and scaffold together. Use supplied public "
        "presentation.target_spans for the marked word; do not infer the target from the whole sentence. "
        "Missing control details are unknown, not permission to invent buttons or facts. "
        "TTS reads Japanese words/sentences; keep IPA, pitch numbers, analysis and Chinese explanation separate. "
        "No audio has been heard merely because text was supplied. Playback is not learner pronunciation assessment "
        "or a gold standard for pitch, devoicing or nasal realization.")
    modes = {q.get("input_mode") for q in questions}
    families = {q.get("presentation", {}).get("task_family") for q in questions}
    kinds = {q.get("kind") for q in questions}
    if "multi_cloze" in modes:
        blocks.append(
            "multi_cloze uses independently editable response_parts, not one free-text answer. Selections appear "
            "inside the sentence; only when asked about controls, explain selecting a gap and undoing the latest "
            "selection. Copy exact option values; an empty-option display label is not a new answer value. "
            "A wrong background gap does not prove the primary target or every related item was forgotten. "
            "Use the exact local candidate; never invent gap solutions or unseen primary/background roles.")
    if "translation_wordbank" in families or "grammar_reorder" in kinds:
        blocks.append(
            "translation_wordbank builds Japanese from the supplied Chinese meaning and Japanese chunks. "
            "Keep chunks intact and use each according to its local controls. A supported word-order error need "
            "not mean the target word was forgotten; a difference from the reference order alone is not proof of error.")
    from .answer_input import romaji_reading_input
    if any(romaji_reading_input(q) for q in questions):
        blocks.append(
            "Unrestricted vocab_reading and grammar text composition accept keyboard-spelling romaji with a kana "
            "preview. A partial consonant is unfinished input, not a memory failure: finish it or switch to kana. "
            "The app preserves the raw draft; grammar composition preserves existing kanji/kana/digits. "
            "Use keyboard spellings for written particles; do not silently guess the answer from pronunciation. "
            "For input help use an unrelated example, not the current solution. Fixed choices and romaji-to-kana "
            "selection keep their original controls; do not claim all Japanese tasks accept romaji.")
    if (kinds & {"pronunciation_rule", "pronunciation_application"}
            or modes & {"pitch_fill", "pitch_identify"}):
        blocks.append(
            "Pitch uses morae, not character counts. prosody_observation/prosody_cue is a supplied visual reference, "
            "not inferred dictionary pitch or evidence of the learner's speech. Do not derive phrase accents by "
            "concatenating word accents or impose one realization where valid variants exist. Reviewed contraction, "
            "phrase grouping, accent/reading alternation and articulatory tasks use their local candidates; "
            "they do not grant free-generation or listening-assessment permission.")
    if kinds & set(NATIVE):
        blocks.append(
            "Native routes resume their existing queues and local first-teaching/due checks. No new cards. "
            "Auxiliary word spelling supports related kana/rules, not automatic word enrollment. "
            "Handwriting recognition and all device actions remain local.")
    return "\n".join(blocks)


CATALOG_RULES = (
    "question_capabilities inventories actual app controls, not permission to create tasks. "
    "Only supplied candidates are executable now; candidate_available=false is not evidence of forgetting. "
    "Consider kind, input_mode, presentation.task_family and scaffold together, including pitch/segmentation. "
    "translation_wordbank reconstructs Japanese from the supplied Chinese meaning and Japanese chunks. "
    "multi_cloze has independently editable response_parts; use the exact local candidate, never invent gap solutions. "
    "A wrong background gap does not prove the primary item or every related item was forgotten. "
    "Use public presentation.target_spans to identify the marked word; never guess it from the whole sentence. "
    "For reading, hidden-transcript, gap or pitch tasks, do not leak the answer through hints, ruby or audio. "
    "Pitch uses morae, not written-character counts; a kanji word can span several morae. "
    "prosody_observation supplies a public prosody_cue reference contour, with relative levels and intra-mora turns; "
    "it is a visual stimulus, not inferred dictionary pitch or evidence of the learner's speech. "
    "Other sound task families include articulatory_choice, sound_application, contraction_restore, "
    "contraction_apply, phrase_grouping, accent_alternation and reading_alternation. "
    "These are locally reviewed pronunciation candidates, not new free-generation permissions. "
    "Native routes resume existing queues via local teaching and due-date checks, never duplicate cards. "
    "Handwriting recognition, pronunciation playback and all grading remain local. "
    "Auxiliary word spelling is evidence for related kana/rules, not automatic word enrollment. "
    "Standalone practice generation uses only generation=true kinds under the existing choice schema. "
    "Content authoring and example proposals obey their separate item-template schema, not that practice limit. "
)
