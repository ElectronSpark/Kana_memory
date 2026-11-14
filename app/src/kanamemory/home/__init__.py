"""今日学习: the home page's reading and computing without its page (design 13 S3).

model (pure): the device inputs read at every refresh (HomeInputs) and what a home
button does (HomeAction). display (pure): every fixed text of the home and the
frozen HomeModel, composed from numbers; it embeds design 12's HomeRowsModel and
FocusRowModel. previews: the two counts of work without a queued question (course
abilities, kana reviews). service: HomeOverviewService (the overview, can_continue,
the records page's course due, the two previews). ui: the page. Nothing here
imports Toga except ui, and nothing takes the app object; the app side is
kanamemory.home_runtime.
"""
