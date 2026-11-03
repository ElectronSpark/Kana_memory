"""Shared editorial policy and inert API schema for experimental Chinese glosses."""

ORDER_TRANSLATION_RULES = """For each Japanese sentence, provide a separate experimental
Chinese order_translation; never replace its ordinary translation. This reference
deliberately pursues Japanese information order MUCH MORE AGGRESSIVELY than a
normal Chinese translation. Semantic fidelity and valid Chinese grammar are hard
constraints; within those constraints, preserving Japanese constituent order and
making the functions of particles/auxiliaries perceptible take priority over
contemporary Chinese's usual word order or register. Do not settle for the ordinary
translation merely because it sounds more familiar.

Actively search for a licensed Chinese construction that retains the source order
of topics, arguments, modifiers, clauses and predicates. The full historical range
of Chinese is available: established Classical/literary Chinese, premodern Chinese,
modern written Chinese and established colloquial constructions. Neither modern
vocabulary nor modern syntax is mandatory. Prefer understandable wording where it
does not sacrifice the structural goal, but do not reject a valid literary form
simply because it is uncommon in daily speech. Use regional forms cautiously:
their syntax and sense must be established and intelligible in the given context.

Useful devices include genuine topic-comment structures, grammatical fronted
objects with anaphoric resumption where licensed, modifier-before-head structures,
nominalization with 所/者/之 where appropriate, and Chinese sentence-final nominal
judgments such as ...是也 or ...也. These are construction choices, not a character
substitution table. In a suitable affirmative nominal identity sentence, retain
the nominal predicate before the final judgment rather than automatically moving
是 before it. Do NOT append 是也 to every です: negative nominal judgments, questions,
adjectival predicates, verbal predicates and polite auxiliary endings have
different grammar and must be handled separately. A construction must work as a
whole; do not splice incompatible historical or regional patterns together.

Do not invent Chinese grammar, place words in a disconnected SOV list, or leave
Japanese particles inside Chinese, such as 水を饮. Topic/object fronting must not
invent contrast, exclusivity or emphasis. Do not mechanically use 将/把 for を,
乃/则 for は, or add a completed disposal construction merely to move an object.
If no semantically faithful, established Chinese construction preserves a source
sequence, reorder that sequence only as much as necessary. Fallback is a genuine
last resort after checking the available Chinese constructions, not a license to
reuse the ordinary translation without attempting a closer structural rendering.
If the ordinary translation already preserves the achievable order and function,
it may remain identical; decorative archaism alone is not the objective.

Interpret particles, auxiliaries and other function words in context, including
their scope. Preserve topic versus grammatical subject, semantic roles, possession,
location/direction/instrument/cause, coordination, comparison, focus and limits.
Account for は/が, を/に/で/へ/と/の/から/まで/より, も/だけ/しか/さえ/こそ,
compound particles, conjunctions and nominalizers. In particular distinguish
まで from までに, だけ from しか...ない, に from で, and contrastive は from
a plain topic. Preserve negation and its scope, quantifiers, tense and aspect,
completed action versus resulting state or ongoing/habitual activity, passive
and causative roles, modality, possibility, obligation, evidentiality, politeness,
questions and contextual sentence-final tone (か/ね/よ etc.). Do not mechanically
turn every た into 了 or every ている into 正在. Do not add assertion strength,
certainty, blame, completion, contrast, emphasis, emotional force, a personal
subject or a new ambiguity absent from the source. Sentence-final 是也/也 must
serve a corresponding judgment, not manufacture theatrical emphasis. A function
word need not have a separate Chinese token if an established Chinese construction
already conveys its role and scope. Understand fixed constructions as a whole;
do not separately negate or translate their component words.

Training examples demonstrate structural choices, not reusable suffix rules:
1. 私は常山の趙子龍です。 -> 吾，常山赵子龙是也。
   The topic remains first and the affirmative nominal judgment remains final;
   の's origin/modifier relation is conveyed by 常山赵子龙 without a Japanese token.
2. 彼が昨日買ったのはこの本です。 -> 他昨日所买者，此书是也。
   所买者 conveys the nominalized clause; its topic precedes the identified book.
3. 私が欲しいのは水だけです。 -> 吾所欲者，水而已。
   而已 restricts the desired thing to water; do not apply the restriction to 吾.
4. これは私の本ではありません。 -> 此，非吾之书也。
   A valid negative nominal judgment needs 非 before its complement. Do not force
   a postposed negative or append affirmative 是也 merely to imitate the source.
5. あなたは教師ですか。 -> 君，教师乎？
   乎 makes a genuine yes/no question, not an assertion, rhetorical challenge or
   request for confirmation. This is not the suffix pattern for a negative question.
6. 昨日、この本は田中さんだけが読んだ。 -> 昨日，此书，唯田中读之。
   唯 scopes over the reader 田中; 之 resumes the already fronted book, introduces
   no new entity, and 昨日 grounds the past event without adding completion.
7. 田中さんしか来なかった。 -> 除田中外，无人来。
   Interpret しか...ない together: do not incorrectly negate Tanaka's arrival.
8. 雨が降ったので、出かけませんでした。 -> 雨下了，故未出门。
   The cause precedes its causal connection and the negative main event; 故
   reflects ので, while 未 scopes over going out, not the rain.
9. 私は水を飲みます。 -> 我饮水。
   Without a context licensing a new object topic or another established fronting
   construction, Chinese needs this local SVO fallback. Do not write 我水饮,
   水を饮 or 我把水喝了; the last adds a completed event absent from the source.

Keep the exact Japanese source_text and the exact existing ordinary Chinese
source_translation as anchors, including punctuation and whitespace. An absent
ordinary translation is the empty string. Do not rewrite either anchor. Resolve
the sentence's intended sense with the existing translation and teaching context,
but never preserve a demonstrable semantic error merely to imitate an existing
translation. Return only the Chinese translation in text, without added Japanese,
romaji, word-by-word labels, sources, explanations, or claims of human review.
Use version=1 and status=experimental. If no faithful, grammatical translation
can be determined from the available context, return null rather than guessing.
This is optional learning-reference metadata, never an answer, audio transcript,
grading signal, evidence of mastery, new learning identity or scheduling input.
"""


def order_translation_schema(*, nullable=True):
    """Strict structured-output shape; old saved content may omit this field."""
    text = lambda: {"type": "string", "maxLength": 4000}
    properties = {
        "version": {"type": "integer", "enum": [1]},
        "status": {"type": "string", "enum": ["experimental"]},
        "source_text": text(), "source_translation": text(), "text": text(),
    }
    value = {"type": "object", "properties": properties,
             "required": list(properties), "additionalProperties": False}
    return {"anyOf": [value, {"type": "null"}]} if nullable else value
