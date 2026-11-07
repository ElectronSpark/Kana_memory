"""Shared material fact teaching fixtures and assertions; no collected scenarios."""
from kanamemory.ai_materials import AIMaterialStore
from kanamemory.example_store import ExampleStore
from kanamemory.material_study import MaterialStudyStore
from kanamemory.material_verification import MaterialVerificationStore
from kanamemory.material_workflow import MaterialWorkflowStore
from types import SimpleNamespace
from copy import deepcopy
from support.material_dictionary_support import dictionary_material
from kanamemory.example_store import empty_pack
from kanamemory.learning_content import learning_item_from_word
from support.ai_material_fixtures import new_word
from support.material_workflow_support import request


def two_words(store, learning):
    _, _, original, _ = dictionary_material(store, learning, candidate_reading='みかくにん')
    words = [original, {**deepcopy(original), 'id': original['id'] + ':two', 'form': '試験語二'}]
    items = [learning_item_from_word(word, enrich=False) for word in words]
    repo = SimpleNamespace(
        get_word=lambda cid: next((deepcopy(word) for word in words if word['id'] == cid), None),
        search_words=lambda title, limit=4: [{'id': word['id']} for word in words if word['form'] == title])
    examples = ExampleStore(store, learning, items, bundled_pack=empty_pack())
    materials = AIMaterialStore(store, items, examples)
    source = '試験語。試験語二。'
    flow = MaterialWorkflowStore(store)
    job = flow.create({**request('two-fact-lessons'), 'mode': 'analyze', 'source_text': source})
    token = flow.begin(job['id'])
    candidates = [new_word(alias='word:' + str(index), title=item['title'], reading='みかくにん', meaning=item['meaning'])
                  for index, item in enumerate(items)]
    job = flow.complete_generation(job['id'], token, {'items': candidates, 'example_pack': empty_pack(),
        'segments': [{'start': 0, 'end': len(source), 'quote': source}]}, materials)
    MaterialVerificationStore(store).prepare(job, materials.get(job['proposal_id']), repo=repo, examples=examples)
    return job, MaterialStudyStore(store, learning, repo=repo, examples=examples), words
