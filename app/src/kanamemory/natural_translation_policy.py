"""Shared content policy for ordinary Chinese translations, not order glosses."""

NATURAL_TRANSLATION_RULES = """Ordinary Japanese-to-Chinese translation policy:
Write ordinary translation fields in clear, natural contemporary Simplified
Chinese. Make the sentence intelligible on first reading, using normal Chinese
word order, collocations and punctuation. A faithful modern paraphrase is better
than a stiff literal rendering. Do not copy Japanese constituent order or use
Classical Chinese merely to imitate its grammar. This rule applies to ordinary
translations and Chinese meanings in examples, lesson instructions and question
content; it does NOT apply to the separate experimental order_translation.text,
whose deliberately aggressive order-preserving policy remains unchanged.

Understand the Japanese sentence and its actual context before translating.
An existing translation, gloss or target label is a contextual clue, not proof:
correct demonstrable errors rather than reproduce them. Preserve the actual
sense, semantic roles, causal or conditional relations, comparison, quantifiers,
focus and negation scope. Distinguish は/が, に/で, だけ/しか...ない, まで/までに,
and compound particles in context. Preserve questions, requests, permissions,
obligations, possibility, evidentiality and sentence-final tone without adding
certainty, reproach, contrast or emphasis. Preserve tense and aspect, including
ongoing activity, habitual action and resulting state; do not mechanically turn
た into 了 or ている into 正在. Preserve who benefits, who acts and who receives
an action in giving/receiving, passive and causative constructions. Honorifics
do not by themselves establish a person's gender, age, relationship or identity.
In particular 氏/さん/様 are not evidence of a male or female referent; an old
Chinese translation saying 先生 or 女士 is not independent evidence either.
Check shared kanji expressions for false friends instead of copying their modern
Chinese-looking spelling: 順路 on a visitor-route sign means 参观路线 or 游览路线
in that context, not the Chinese adverb 顺路. Preserve inference plus confirmation
in constructions such as ということですね; do not reduce them to a flat assertion.
Use a natural expression fitting the actual tone, not one stock phrase everywhere.

Handle Japanese ellipsis conservatively. Prefer a natural Chinese subjectless
sentence or an already licensed pronoun omission when it communicates the sense
clearly. Do not automatically add 我/你/他/她/他们 or parenthesized placeholders.
Do not retain 我们 from an old translation unless the Japanese or reliably supplied
context supports the first-person plural; a previous translator's addition alone
does not license a participant, even when repeating it would sound fluent.
Only restore an omitted participant, object or contextual relation when Chinese
otherwise becomes unclear AND the supplied context reliably identifies it.
Then use the shortest natural wording; concise parentheses such as （我） may
mark a genuinely necessary, context-supported restoration when useful. Never
infer gender, family relationship, possession, number, time or a new entity
merely to make a sentence sound complete. Keep ambiguity when it can be conveyed
clearly in Chinese. If a decisive sense or referent cannot be established, use
the caller's pending/review mechanism rather than guess. If the response schema
has no such mechanism, omit that uncertain proposal; do not invent new fields.

Translation fields contain only the translation itself. Do not insert grammar
labels, teaching commentary, parsing notes, citations, confidence claims or
review flags such as 待核 into the translated sentence. Use the existing hint,
explanation, rule or review fields for those purposes. Parentheses may restore
supported omitted content, never hide commentary such as （此处是主语）. Do not
turn the translation into a slash-separated dictionary of incompatible senses.
When an old source_translation contains non-translation teaching comments,
pronunciation/pitch labels or other instructional annotations, do not silently
discard them while cleaning the translated sentence. Preserve their supported
meaning separately in a caller-provided teaching_note or existing explanation
field. Extract only information actually present in the old source_translation;
do not invent new rules, readings, pitch numbers or example interpretations.
Do not relocate ordinary translated content into a note just because it uses
parentheses. If the source annotation itself cannot be interpreted responsibly,
use the caller's review mechanism instead of silently guessing. Never introduce
an unrequested note field into a schema that has none.

For newly generated examples and instruction.example, finish and check the
ordinary translation FIRST. Then generate the separate order_translation and
copy the final ordinary translation exactly into its source_translation anchor;
copy the exact Japanese into source_text. Do not anchor the reference to an older
draft translation, silently retarget an existing reference, or change the normal
translation merely to retain an old reference. Existing saved content and its
identities/revisions are not rewritten by this generation policy. A question or
template without an ordinary-translation field keeps source_translation empty;
do not invent a field, place a translation in a pre-answer stimulus, or weaken
any existing anti-answer-leak rule. Chinese answer choices may convey meanings
only when the question kind calls for them; explain grammar in the designated
explanation/feedback rather than adding it to an otherwise plain translation.

Examples illustrate context-sensitive decisions, not fixed replacements:
1. 私は学生です。 -> 我是学生。 (No 吾/是也 in the ordinary translation.)
2. 昨日、この本は田中さんだけが読んだ。 -> 昨天只有田中读过这本书。
   だけ restricts the reader; さん does not license inventing 先生 or 女士.
3. 田中さんしか来なかった。 -> 只有田中来了。
   Do not negate Tanaka's arrival or add a gendered title.
4. もう食べました。 -> 已经吃过了。
   With no further context, natural Chinese needs no invented subject or food.
5. Context: A asks B 朝ご飯は食べましたか。 B replies まだです。
   B's reply -> 还没吃早饭。 The supplied question supports the restored action
   and object; do not assume this meaning for an isolated まだです。
6. 窓が開いています。 -> 窗户开着。
   A resulting state, not someone currently opening the window.
7. 先生に本を貸していただきました。 -> 老师把书借给了我。
   Preserve the benefactive direction; never reverse lender and borrower.
8. 明日までに提出してください。 -> 请最迟明天提交。
   The deadline includes tomorrow; do not mistranslate as an action continuing
   until tomorrow or as a deadline that excludes tomorrow. Where the supplied
   context provides an exact cutoff, use that natural Chinese wording instead.
9. この本を読んだ人がみな賛成したわけではない。
   -> 读过这本书的人并不是都赞成。 Preserve partial negation, not nobody agrees.
10. Context: an exhibition visitor-route sign 順路. -> 参观路线。
    Shared characters do not make 顺路 the correct Chinese meaning here.
11. 山田氏が説明します。 -> 由山田来说明。
    Do not assume 山田先生 merely because 氏 or an older translation used a title.
12. 予約が必要です。 -> 需要预约。
    Do not copy 我们需要预约 from an old gloss without actual participant context.
13. 来週は休みということですね。 -> 也就是说，下周休息，对吗？
    Preserve the inferred understanding and the request for confirmation.
14. Source: 学校へ行きます。 Old Chinese: 去学校。（へ在这里读作え。）
    translation -> 去学校。 Separate teaching_note, if the caller provides one:
    へ在这里读作え。 Do not lose this existing instruction or add a pitch number.
"""
