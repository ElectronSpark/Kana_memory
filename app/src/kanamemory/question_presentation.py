"""Small, read-only question cues derived solely from public authored fields.

Unknown instructions remain intact. This is neither a new question format nor
an annotation/speech authorization: answers never supply highlighting anchors.
"""
from collections.abc import Mapping
import re


_QUOTED_TASKS = {
    "vocab_reading": (
        r"只写句中「(?P<word>[^「」\n]+)」在本课中的假名读法，不必写整句。",
        r"只写「(?P<word>[^「」\n]+)」在本课中的假名读法。",
    ),
    "vocab_meaning": (
        r"句中「(?P<word>[^「」\n]+)」对应本课的哪个意思？",
        r"句中「(?P<word>[^「」\n]+)」表达的本课义项是？",
        r"「(?P<word>[^「」\n]+)」在本课中的意思是？",
    ),
    "grammar_meaning": (r"句中「(?P<word>[^「」\n]+)」对应本课的什么作用？",),
}
_FOCUSED_TASKS = {
    ("vocab_reading", "标记部分怎么读？"): "读音",
    ("vocab_meaning", "标记部分在这里是什么意思？"): "含义",
    ("vocab_orthography", "选出标记词的写法。"): "写法",
    ("vocab_recall", "标记部分的基本形是？"): "基本形",
}
_PLAIN_TASKS = {
    ("vocab_reading", "写出假名读法。"): "读音",
    ("vocab_reading", "写出日语假名读法。"): "读音",
    ("vocab_meaning", "这个词在本课中的意思是？"): "含义",
    ("vocab_meaning", "写出本课义项的中文或英文意思。也可显示答案后核对。"): "含义 · 中文 / English",
    ("reading_comprehension", "选出整句的意思。"): "句意",
    ("grammar_meaning", "这句话中，本课的表达起什么作用？"): "含义",
    ("grammar_meaning", "这句话中本课形式表达什么？"): "含义",
    ("grammar_cloze", "补全括号中的部分。"): "填空",
    ("grammar_reorder", "排成一句话。"): "语序",
    ("grammar_reorder", "依次点击词块，拼出句子。"): "语序",
    ("grammar_reorder", "按词块排列成完整句子"): "语序",
    ("vocab_recall", "写出本课学的单词。"): "单词",
    ("vocab_recall", "写出本课所学的单词基本形，可用本课汉字写法或假名。"): "基本形 · 汉字 / 假名",
    ("vocab_orthography", "选择对应的常用写法。"): "写法",
    ("vocab_audio_meaning", "先播放，再选择。"): "含义",
    ("listening_comprehension", "先播放，再选择。"): "含义",
}


def question_presentation(question):
    """Keep a minimal operation cue, indispensable constraints and exact focus.

    Frozen legacy sentence-reading questions encode their target in a known
    public instruction. Only an unambiguous occurrence can replace that text.
    Valid explicit spans may identify one of several identical occurrences.
    """
    if not isinstance(question, Mapping):
        return {"instruction": "", "target_spans": []}
    task = question.get("task", "")
    task = task if isinstance(task, str) else ""
    kind, prompt = question.get("kind"), question.get("prompt", "")
    result = {"instruction": task, "target_spans": []}
    if not isinstance(prompt, str):
        return result
    public = (question.get("prompt_visible") is not False
              and not any(question.get(key) for key in ("audio_only", "has_hidden_transcript", "audio_text")))
    roles = question.get('text_roles')
    instruction_prompt = (isinstance(roles, Mapping) and roles.get('prompt') == 'ui'
        or kind in {'grammar_reorder', 'controlled_sentence_production'}
        or isinstance(question.get('practice'), Mapping)
        and question['practice'].get('task_family') == 'translation_wordbank')
    if public and question.get("practice") is not None:
        from .learning_questions import validate_practice
        try:
            practice = validate_practice(question, target_id=question.get("target_id"))
        except (ValueError, KeyError, TypeError):
            return result
        # Translation stimuli and category labels are instructions. Their
        # publication anchors must not turn Chinese into Japanese study text.
        result["target_spans"] = practice["target_spans"] if practice and not instruction_prompt else []
    for pattern in _QUOTED_TASKS.get(kind, ()):
        match = re.fullmatch(pattern, task)
        if not match:
            continue
        word = match["word"]
        spans = result["target_spans"]
        if public and not spans:
            positions = [m.start() for m in re.finditer("(?=" + re.escape(word) + ")", prompt)]
            if len(positions) == 1:
                start = positions[0]
                spans = [{"start": start, "end": start + len(word), "quote": word}]
        if spans and all(span["quote"] == word for span in spans):
            result.update(target_spans=spans, instruction="读音" if kind == "vocab_reading" else "含义")
        return result
    if (question.get("input_mode", "choice") == "choice"
            and task in {"选择最符合本题语境的答案。", "选择最符合语境的答案。"}):
        result["instruction"] = ""
        return result
    key = (kind, task)
    if result["target_spans"] and key in _FOCUSED_TASKS:
        result["instruction"] = _FOCUSED_TASKS[key]
    elif key in _PLAIN_TASKS:
        result["instruction"] = _PLAIN_TASKS[key]
    elif kind == "vocab_reading":
        # A sense can disambiguate homographs. Keep it verbatim, but remove
        # only the known trailing operation, not arbitrary Chinese sentences.
        match = re.fullmatch(r"本课义项：(.+)。(?:写出本课所学的假名读法|写出日语假名读法)。", task)
        if match:
            result["instruction"] = "读音 · " + match[1]
    return result


def question_instruction(question):
    return question_presentation(question)["instruction"]
