"""课程学习: what the course line serves next, as values, and the six pages it draws itself (design 13).

model (pure): the course step the decision returns (LibraryOff,
MaterialBlocked, Batch, Paused, Done, Diagnostic, Wait, Prerequisite, Teach,
Ask) and what a button of the line's own pages does (CourseAction). display
(pure): every fixed text of the six pages and their display models. ui: the
pages, a display model and one action callback in, widgets out. Nothing here
imports Toga except ui, and nothing takes the app object.
"""
