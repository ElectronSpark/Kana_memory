"""Heard expression meaning: listen to the sentence of a meaning question, choose its meaning.

Pure rules: no Toga, database, platform, network or content loading. The kind
has no templates of its own. For an expression lesson (a grammar lesson whose
domain is "expression", nothing else) every reviewed grammar_meaning template
gives the variant with the same number: the same prompt, task, answer, options,
explanation and practice metadata, the fixed task HEARD_TASK (the source's task
names the expression; it stays in the template as source_task only), plus the
audio_text: what the app speaks for that prompt today (TemplateEnv.spoken_prompt)
without speaker labels. The prompt stays in the snapshot so
that both questions show the same sentence units (one sentence per lesson) and
the result page can show the sentence; the question page hides it, as for
every kind that needs audio.

A source whose prompt cannot be spoken as a whole (no spoken text, spoken text
that would carry a Chinese instruction or Latin letters, or an order
translation bound to the visible sentence) keeps its number with an empty
audio_text: learning_questions retires that variant, like the twin of a retired
source, so the numbers of the two kinds always agree. A lesson none of whose
sources can be spoken has no variant at all.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from ..question_sentences import without_speaker_labels

KIND = "grammar_audio_meaning"
SOURCE_KIND = "grammar_meaning"
KINDS = (KIND,)
DOMAIN = "expression"
SPOKEN_FIELD = "audio_text"
# The twin's own task line: a heard question never prints its sentence or the expression.
HEARD_TASK = "听句子，选出它的意思。"
# The source's task, kept in the derived template only (learning_questions._derived_source).
SOURCE_TASK_FIELD = "source_task"
_LATIN = re.compile(r"[A-Za-z\uff21-\uff3a\uff41-\uff5a]")

# Chinese instruction words the course writes in its tasks (learning_questions
# keeps the same list for order translations), and Han characters that Japanese
# text never uses (no Shift_JIS form: simplified Chinese).
_INSTRUCTION_WORDS = ("选择", "写出", "请用", "改成", "的意思", "怎么读")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def eligible(item: Mapping) -> bool:
    """Only a bundled or library expression lesson; never AI or custom text."""
    return (isinstance(item, Mapping) and item.get("family") == "grammar" and item.get("domain") == DOMAIN
            and not item.get("ai_generated") and not str(item.get("id", "")).startswith("local:"))


def chinese_instruction(text: str) -> bool:
    """The spoken text carries a Chinese instruction: an instruction word, or a Han character Japanese lacks."""
    if any(word in text for word in _INSTRUCTION_WORDS):
        return True
    for char in _HAN.findall(text):
        try:
            char.encode("cp932")
        except UnicodeEncodeError:
            return True
    return False


def spoken_text(item: Mapping, raw: Mapping, env) -> str:
    """The twin's audio_text for one source template, or "" when the prompt cannot be spoken as a whole.

    The page's spoken prompt line after line without speaker labels (A：, B：; question_sentences). A text
    with a Chinese instruction or with Latin letters (the Japanese voice misreads them) gives none.
    """
    # An order translation is bound to a visible sentence: a heard question never carries one.
    if (not isinstance(raw, Mapping) or SPOKEN_FIELD in raw or SOURCE_TASK_FIELD in raw
            or "order_translation" in raw):
        return ""
    text = env.spoken_prompt(item, SOURCE_KIND, raw)
    # A Chinese instruction is looked for before the labels go: 「选择意思：…」 has the shape of a label.
    if not isinstance(text, str) or not text.strip() or chinese_instruction(text):
        return ""
    text = without_speaker_labels(text)
    if not text.strip() or chinese_instruction(text) or _LATIN.search(text):
        return ""
    return text


def twin(item: Mapping, raw: Mapping, env) -> dict:
    """The heard twin of one source template: same fields, the fixed heard task, the spoken text."""
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw, task=HEARD_TASK, **{SOURCE_TASK_FIELD: raw.get("task"),
                                          SPOKEN_FIELD: spoken_text(item, raw, env)})


def templates(item: Mapping, spec, env) -> list[dict]:
    """TemplateProvider: one heard twin per source template, same variant numbers; [] when none can be heard."""
    if not eligible(item) or spec.ability not in item.get("abilities", (spec.ability,)):
        return []
    sources = env.source_templates(item, spec.derived_from)
    twins = [twin(item, raw, env) for raw in sources]
    return twins if any(value.get(SPOKEN_FIELD) for value in twins) else []


def quotes_sentence(spoken: str, text: str) -> bool:
    """A page text quotes the heard sentence: a Japanese run (kana or kanji) of two or more characters of
    the text occurs in the sentence, or the text holds three or more running characters of the sentence
    (わかりました beside よくわかりません). A single kana (ね, は) names a particle, not the sentence."""
    if not isinstance(spoken, str) or not isinstance(text, str):
        return False
    if any(len(run) >= 2 and run in spoken for run in _JAPANESE_RUN.findall(text)):
        return True
    return any(run[start:start + 3] in text for run in _JAPANESE_RUN.findall(spoken)
               for start in range(len(run) - 2))


def shown_before_answer(question: Mapping, *texts) -> bool:
    """Whether a page line made of texts may be shown on question before its answer.

    Always, except on a heard twin: there a line with a Japanese run of its (hidden) sentence would print
    what is to be heard (a word gloss, a rule hint naming a form of the sentence).
    """
    if not isinstance(question, Mapping) or question.get("kind") != KIND:
        return True
    sentence = question.get("prompt")
    return not any(quotes_sentence(sentence, text) for text in texts if isinstance(text, str))


_JAPANESE_RUN = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff々]+")
