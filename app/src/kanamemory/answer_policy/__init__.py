"""The answer policy of a typed question: the answer overlay and the rules that read it.

overlay (pure): the ``answer_overlay`` teaching namespace (format and checks) and the one
effective policy of a question (strict, accepted answers, known-wrong answers with their
feedback, the fold mark), computed from the question and its overlay row; never stored.
numbers (pure): kanji numerals and digits made the same for comparing typed answers.
near_miss (pure): a typed kana answer one or two kana from an accepted answer, and its
differences named in Chinese (design 03, typed-answer rule 10).
source: the installed overlay row of a question's own template, the lesson forms and the
lesson readings that the policy reads (content lookup, no learner database), and the
near miss as the judge and the result pages ask for it. kana_answer: whether a typed
question has an answer that can be typed in kana (the judge decides; design 03).
No module here imports Toga, the learner database or the clock.
"""
