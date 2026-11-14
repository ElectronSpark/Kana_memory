"""Authored word kinds answered by choosing: meaning, form, usage, contrast, comprehension.

Pure rules: no Toga, database, platform, network or content loading. This
module owns the kind-specific parts of the authored choice kinds (every word
kind that is neither free text, a generated sound task nor reviewed context
choice): the legacy question built from a lesson's own fields and the few
content tables the builder lends (reviewed templates go through the generic
template path), and their instruction cues. Grading is the generic exact
match, except vocab_orthography, whose options are written words and use the
written-form check of question_kinds/vocab_text (wired in builtin). A
dictionary-only meaning lesson keeps its historical text recall under the
same kind string.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..question_kind_registry import KindPresentation

MEANING_KIND = "vocab_meaning"
AUDIO_MEANING_KIND = "vocab_audio_meaning"
ORTHOGRAPHY_KIND = "vocab_orthography"
USAGE_KIND = "vocab_usage"
CONTRAST_KIND = "vocab_zh_ja_contrast"
READING_COMPREHENSION_KIND = "reading_comprehension"
LISTENING_COMPREHENSION_KIND = "listening_comprehension"
KINDS = (MEANING_KIND, AUDIO_MEANING_KIND, ORTHOGRAPHY_KIND, USAGE_KIND, CONTRAST_KIND,
         READING_COMPREHENSION_KIND, LISTENING_COMPREHENSION_KIND)

# Instruction cues; the same literals are study text in question_presentation.
MEANING, WRITING = "意味", "書き方"
_PLAY_THEN_CHOOSE = "先播放，再选择。"


def _build_meaning(item, base, env):
    if item.get("dictionary_only"):
        # Dictionary entries can have overlapping senses; an unknown
        # alternative is never auto-marked wrong. These questions use recall
        # rather than guessed dictionary distractors.
        meaning = item["meaning"]
        return dict(prompt=item["title"], task="写出本课义项的中文或英文意思。也可显示答案后核对。",
                    answer=meaning, accepted_answers=item.get("accepted_meanings", [meaning]),
                    input_mode="text", choices=[], feedback=meaning, strict=False,
                    safe=False)
    base["task"] = "这个词在本课中的意思是？"
    return base


def _build_audio_meaning(item, base, env):
    base.update(prompt="听单词，选择本课所学的意思。", task=_PLAY_THEN_CHOOSE,
                audio_text=item["reading"], safe=False)
    return base


def _build_orthography(item, base, env):
    form, reading, meaning = item["title"], item["reading"], item["meaning"]
    choices = [form, *[x["title"] for x in env.legacy_word_lessons(item)
                       if x["id"] != item["id"] and x["title"] != form][:3]]
    base.update(prompt=f"{reading} · {meaning}", task="选择对应的常用写法。",
                answer=form, choices=choices, safe=False)
    return base


def _build_usage(item, base, env):
    prompt, context = item["cloze"]
    form = item["title"]
    answer = env.content("USAGE_ANSWER_OVERRIDES", {}).get(form, form)
    choices = [answer, *[x for x in env.content("USAGE_CHOICES", ()) if x != answer][:3]]
    base.update(prompt=prompt, task=context, answer=answer, choices=choices,
                feedback=item["instruction"]["rule"], safe=False)
    return base


def _build_contrast(item, base, env):
    choices = env.content("CONTRAST_CHOICES", {})[item["title"]]
    base.update(prompt=item["title"], task="本课这个日语词应该用于哪种情况？", answer=choices[0],
                choices=choices, feedback=item["instruction"]["contrast"], safe=True)
    return base


def _build_comprehension(item, base, env):
    instruction = item["instruction"]
    sentence = instruction["example"]
    base.update(prompt=sentence, task=f"句中「{item['title']}」表达的本课义项是？",
                feedback=instruction["translation"], safe=True)
    return base


def _build_listening_comprehension(item, base, env):
    base = _build_comprehension(item, base, env)
    base.update(prompt="听短句，选择所学单词在句中的意思。", task=_PLAY_THEN_CHOOSE,
                audio_text=item["instruction"]["example"], safe=False)
    return base


_BUILDERS = {MEANING_KIND: _build_meaning, AUDIO_MEANING_KIND: _build_audio_meaning,
             ORTHOGRAPHY_KIND: _build_orthography, USAGE_KIND: _build_usage,
             CONTRAST_KIND: _build_contrast, READING_COMPREHENSION_KIND: _build_comprehension,
             LISTENING_COMPREHENSION_KIND: _build_listening_comprehension}


def build(item: Mapping, spec, env, variant: int) -> dict:
    """Builder for a lesson without reviewed templates (legacy raw ignores variant)."""
    return _BUILDERS[spec.kind](item, env.legacy_word_base(item), env)


PRESENTATIONS = {
    MEANING_KIND: KindPresentation(
        quoted_tasks=(r"句中「(?P<word>[^「」\n]+)」对应本课的哪个意思？",
                      r"句中「(?P<word>[^「」\n]+)」表达的本课义项是？",
                      r"「(?P<word>[^「」\n]+)」在本课中的意思是？"),
        quoted_cue=MEANING,
        focused_tasks=(("标记部分在这里是什么意思？", MEANING),),
        plain_tasks=(("这个词在本课中的意思是？", MEANING),
                     ("写出本课义项的中文或英文意思。也可显示答案后核对。", "意味 · 中文 / English")),
        # Historical: it asks for a Chinese/English answer, so it is not study text.
        ui_cues=("意味 · 中文 / English",)),
    AUDIO_MEANING_KIND: KindPresentation(plain_tasks=((_PLAY_THEN_CHOOSE, MEANING),)),
    ORTHOGRAPHY_KIND: KindPresentation(
        focused_tasks=(("选出标记词的写法。", WRITING),),
        plain_tasks=(("选择对应的常用写法。", WRITING),)),
    # Its sentence completion cues are often only a translation of the gap.
    USAGE_KIND: KindPresentation(folds_translation=True),
    READING_COMPREHENSION_KIND: KindPresentation(plain_tasks=(("选出整句的意思。", "文の意味"),)),
    LISTENING_COMPREHENSION_KIND: KindPresentation(plain_tasks=((_PLAY_THEN_CHOOSE, MEANING),)),
}
