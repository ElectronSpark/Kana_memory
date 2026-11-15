"""课程学习: the course line as values, services and pages (design 13).

Four groups. Each has a model (pure values), a display module (pure: the
fixed texts and the display models), a service where the group reads or
writes, and its pages in ui:

- model, display, ui.line_pages: the course step the decision returns
  (LibraryOff, MaterialBlocked, Batch, Paused, Done, Diagnostic, Wait,
  Prerequisite, Teach, Ask), what a button of the line's six own pages does
  (CourseAction), and those pages. The decision itself is made on the app
  side (course_runtime).
- batch_model, batch_display, batch, ui.batch_pages: the batch
  introduction (BatchIntroduction).
- answer_model, answer_display, answer, ui.answer_pages: answering a
  question, from the moment it is shown to its rating (CourseAnswering, and
  the AnswerSession it works on).
- teaching_model, teaching_display, teaching, ui.teaching_pages: what a
  teaching head becomes, the page 开始前 and the guide (CourseTeaching, and
  the TeachingSession).

A service is built from the stores and a few callables and never takes the
app object. A page takes a display model and one action callback and
returns widgets. Only ui imports the control components.
"""
