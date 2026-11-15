"""假名练习: the kana practice line's own state and rules, apart from its pages (design 13).

fields (pure): state whose fields may never have been set (UNSET, PartlySet).
attempt (pure): what happened in the attempt on the page (KanaAttempt), the marks of a session's attempts
(AttemptMarks), and the rules that set them.
session (pure): which queue the practice holds and where the learner is in it (KanaSession).
rating_model (pure): the arguments of the review record, and what a saved rating does to the position.
rating: the rating of an answer as a service over the store (KanaRating); it takes what only the app side knows
as callables.
Nothing here imports Toga or takes the app object.
"""
