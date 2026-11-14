"""添加学习内容: the add-content use case without its page (design 13 S2).

model (pure): the inputs read from the device and the settings, results,
the section-count rule. display (pure): the display models and every fixed
text of the page. costs: the two caches of the workload line, per learner
store. service: AddContentService (the operations) and AddSession (one
showing of the page). assembly: StoreAdmission, built per store. No module
here imports Toga or takes the app object; the app side is
kanamemory.admission_runtime.
"""
