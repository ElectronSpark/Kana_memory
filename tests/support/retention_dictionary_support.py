"""Shared retention dictionary fixtures and assertions; no collected scenarios."""
from kanamemory.retention_session import DiagnosticStore
from kanamemory.material_admission import MaterialAdmissionStore
from kanamemory import app_clock
from support.material_dictionary_support import dictionary_material
from support.material_admission_support import reviewed_examples
from datetime import timedelta


def prepare(store, learning):
    job, study, word, item = dictionary_material(store, learning, candidate_reading='みかくにん')
    reviewed_examples(study, item)
    admission = MaterialAdmissionStore(study)
    admission.enroll(admission.preview(job['id'], {job['candidate_ids'][0]: ['understanding']}))
    now = app_clock.utc_now()
    for action in ('intro_done', 'guide_correct'):
        learning.advance_instruction(item['id'], action, now=now)
    learning.advance_instruction(item['id'], 'check', now=now + timedelta(minutes=2))
    return DiagnosticStore(store, learning, repo=study.repo, examples=study.examples), word, item, now + timedelta(days=1)
