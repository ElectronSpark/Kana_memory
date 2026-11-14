"""继续学习: what the continue action does next, as values, and the three end pages it draws (design 13 S4).

model (pure): the next step the continue dispatch decides (CourseItem, KanaItem,
ExtraOr, RoundEnd, Reference, NoKanaKind, RouteEnd, the two waits, Stay,
Failed) and what an end-page button does (EndAction). display (pure): the page
names, every fixed text of the three end pages and their display models. ui:
the pages, a display model and one action callback in, widgets out. Nothing
here imports Toga except ui, and nothing takes the app object.
"""
