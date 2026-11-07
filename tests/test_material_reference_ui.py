"""Reference editing survives native Back and backup without adopting drafts."""
from types import MethodType

from support.ai_ui_followups_support import ai_navigation_app
from support.ai_ui_support import ai_app
from support.example_ui_support import examples_app
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app
from support.app_fixtures import setup_app
from support.fake_ui import descendants
from support.material_reference_support import prepared
from kanamemory.app import KanaMemory
from kanamemory.material_reference import claims
from kanamemory.progress_backup import export_backup, inspect_backup, import_backup


def prepare(app):
    app.repo.search_words.return_value = []
    for name in ('show_material_references', 'show_material_reference_source', 'show_material_reference_claim'):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    return prepared((app.store, app.learning_store, [], app.example_store(), app.ai_material_store()))


def inputs(app):
    return [v for v in descendants(app.body) if callable(getattr(v, 'on_change', None))]


def test_source_back_restores_fields_and_owner_after_nested_claim(ai_navigation_app):
    app = ai_navigation_app
    job, proposal, refs, _ = prepare(app)
    app.show_material_references(job['id'])
    app.show_material_reference_source(job['id'])
    label, locator, text = inputs(app)
    label.value, locator.value, text.value = 'Draft dictionary', 'page 12', 'Unsubmitted reference passage'
    # Native Back checkpoints the last edit even before a change callback.
    app.go_back()
    assert app.page == 'material_references'
    app.show_material_reference_source(job['id'])
    controls = inputs(app)
    assert [v.value for v in controls] == ['Draft dictionary', 'page 12', 'Unsubmitted reference passage']
    app.show_material_reference_claim(job['id'], claims(proposal)[0]['id'])
    controls[0].value = 'ignored stale callback'; controls[0].on_change(controls[0])
    key = 'material_reference_form_v1:' + proposal['id']
    assert app.store.get_setting(key)['label'] == 'Draft dictionary'
    app.go_back()
    assert app.page == 'material_reference_source'
    controls[0].value = 'restored callback'; controls[0].on_change(controls[0])
    assert app.store.get_setting(key)['label'] == 'restored callback'
    assert len(refs.sources(proposal['id'])) == 1  # Draft never adopted as a source.


def test_claim_draft_survives_backup_restore_without_creating_review(ai_navigation_app):
    app = ai_navigation_app
    job, proposal, refs, _ = prepare(app)
    cid = next(c['id'] for c in claims(proposal) if c['facet'] == 'meaning')
    app.show_material_references(job['id'])
    app.show_material_reference_claim(job['id'], cid)
    fields = inputs(app)
    fields[1].value = 'draft quote'; fields[3].value = 'unsubmitted reasoning'
    fields[-1].value = 'unsubmitted comparison'
    fields[3].on_change(fields[3])
    app.go_back()
    data = export_backup(app.store)
    inspect_backup(app.store, data); import_backup(app.store, data)
    app.show_material_reference_claim(job['id'], cid)
    fields = inputs(app)
    assert fields[1].value == 'draft quote' and fields[3].value == 'unsubmitted reasoning'
    assert fields[-1].value == 'unsubmitted comparison'
    assert refs.latest(proposal['id']) == {}
