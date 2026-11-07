"""Shared material study fixtures and assertions; no collected scenarios."""
from kanamemory.ai_materials import AIMaterialStore
from kanamemory.example_store import ExampleStore
from kanamemory.material_study import MaterialStudyStore
from kanamemory.material_verification import MaterialVerificationStore
from kanamemory.material_workflow import MaterialWorkflowStore
from support.retention_check_support import WORD
from kanamemory.example_catalogue import bundled_example_pack
from copy import deepcopy
from kanamemory.learning_content import get_learning_item
from support.material_workflow_support import request


def material_fixture(store, learning, target=WORD):
    item = get_learning_item(target)
    bundled = bundled_example_pack([item])
    example_store = ExampleStore(store, learning, [item], bundled_pack=bundled)
    example = bundled['examples'][0]
    pack = {**deepcopy(bundled), 'status': 'draft', 'examples': [deepcopy(example)],
            'references': [deepcopy(r) for r in bundled['references'] if r['example_id'] == example['id']]}
    materials = AIMaterialStore(store, [item], example_store)
    flow = MaterialWorkflowStore(store)
    job = flow.create(request('study-material-' + target))
    token = flow.begin(job['id'])
    job = flow.complete_generation(job['id'], token, {'items': [], 'example_pack': pack, 'segments': []}, materials)
    proposal = materials.get(job['proposal_id'])
    MaterialVerificationStore(store).prepare(job, proposal, examples=example_store)
    return job, MaterialStudyStore(store, learning, examples=example_store)
