"""Shared material reference fixtures and assertions; no collected scenarios."""
from kanamemory.material_reference import MaterialReferenceStore
from kanamemory.material_verification import MaterialVerificationStore
from kanamemory.material_workflow import MaterialWorkflowStore
from kanamemory.material_reference import checks
from kanamemory.material_reference import claims
from kanamemory.material_workflow import dump
from support.ai_material_fixtures import examples
import json
from support.ai_material_fixtures import new_word
from support.material_workflow_support import request


def prepared(resources, *, second=False, blocked_dependency=False):
    store, learning, _, ex, materials = resources
    flow = MaterialWorkflowStore(store); job = flow.create({**request('reference-fixture'), 'count': 10})
    token = flow.begin(job['id'])
    pack = examples(count=10)
    translations = ['（我）看星空。', '星空很美。', '（我）拍星空的照片。', '（我）眺望星空。',
        '能看到星空。', '（我）在星空下行走。', '（我们）谈论星空。', '星空铺展开来。',
        '（我）去看星空。', '（我）画星空。']
    for row, text in zip(pack['examples'], translations): row['translation'] = text
    items = [new_word()]
    if second:
        # Independent synthetic item and ten linked examples for append tests.
        other = json.loads(json.dumps(new_word(), ensure_ascii=False).replace('星空', '夜空').replace('ほしぞら', 'よぞら'))
        other['id'] = 'candidate:night-sky'
        if blocked_dependency:
            other['depends_on'] = ['candidate:prerequisite']
        more = json.loads(json.dumps(pack, ensure_ascii=False).replace('星空', '夜空').replace('ほしぞら', 'よぞら'))
        for row in more['examples']: row['id'] += '-night'
        for row in more['references']:
            row['id'] += '-night'; row['example_id'] += '-night'; row['target_id'] = other['id']
        pack['examples'].extend(more['examples']); pack['references'].extend(more['references'])
        items.append(other)
        if blocked_dependency:
            items.append(new_word(alias='candidate:prerequisite', title='星', reading='ほし', meaning='星星'))
    job = flow.complete_generation(job['id'], token,
        {'items': items, 'example_pack': pack, 'segments': []}, materials)
    proposal = materials.get(job['proposal_id'])
    MaterialVerificationStore(store).prepare(job, proposal, examples=ex)
    references = MaterialReferenceStore(store)
    source = references.add_source(proposal['id'], 'Synthetic independent reference', 'fixture edition / section 1',
        'Synthetic reference record for protocol tests. ' + dump(proposal['items']) + dump(proposal['example_pack']))
    return job, proposal, references, source


def confirm(references, proposal, source, claim, *, status='supported', request_id=None):
    quote = dump(claim['value']); start = source['text'].find(quote)
    assert start >= 0
    expected = references.preview(proposal['id'], claim['id'])
    return references.record(expected,
        citations=[{'source_id': source['id'], 'start': start, 'end': start+len(quote), 'quote': quote}],
        status=status, reason='Synthetic scoped human comparison for the storage contract.',
        check_notes={key: 'Synthetic checked scope.' for key in checks(claim)},
        model_comparison='No model assertion is used as independent evidence.', request_id=request_id)


def confirm_all(references, proposal, source):
    for claim in claims(proposal): confirm(references, proposal, source, claim)
