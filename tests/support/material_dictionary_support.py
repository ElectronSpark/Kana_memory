"""Shared material dictionary fixtures and assertions; no collected scenarios."""
from kanamemory.ai_materials import AIMaterialStore
from kanamemory.example_store import ExampleStore
from kanamemory.material_study import MaterialStudyStore
from kanamemory.material_verification import MaterialVerificationStore
from kanamemory.material_workflow import MaterialWorkflowStore
from types import SimpleNamespace
from copy import deepcopy
from support.learning_questions_support import dictionary_word
from kanamemory.example_store import empty_pack
from kanamemory.learning_content import learning_item_from_word
from support.ai_material_fixtures import new_word
from support.material_workflow_support import request


KINDS = ['vocab_meaning', 'vocab_reading']


def dictionary_material(store, learning, *, candidate_reading=None, candidate_meaning=None):
    word = dictionary_word()
    # Inert, dictionary-only fixture; do not collide with a real bundled lesson.
    word.update(form='試験語', reading='しけんご', readings=[{'text': 'しけんご', 'applies_to_kanji': []}])
    word['senses'] = [word['senses'][0]]
    word['senses'][0]['japanese_meaning_english_glosses'][0]['text'] = 'test term'
    word['chinese_senses'][0]['glosses'] = ['测试用语']
    item = learning_item_from_word(word, enrich=False)
    repo = SimpleNamespace(search_words=lambda query, limit=4: [{'id': word['id']}],
                           get_word=lambda identity: deepcopy(word) if identity == word['id'] else None)
    examples = ExampleStore(store, learning, [item], bundled_pack=empty_pack())
    materials = AIMaterialStore(store, [item], examples)
    candidate = new_word(title=item['title'], reading=item['reading'] if candidate_reading is None else candidate_reading,
                         meaning=item['meaning'] if candidate_meaning is None else candidate_meaning)
    flow = MaterialWorkflowStore(store)
    source = '試験語。'
    job = flow.create({**request('dictionary-material'), 'mode': 'analyze', 'source_text': source})
    token = flow.begin(job['id'])
    job = flow.complete_generation(job['id'], token,
        {'items': [candidate], 'example_pack': empty_pack(), 'segments': [{'start': 0, 'end': len(source), 'quote': source}]}, materials)
    proposal = materials.get(job['proposal_id'])
    MaterialVerificationStore(store).prepare(job, proposal, repo=repo, examples=examples)
    return job, MaterialStudyStore(store, learning, repo=repo, examples=examples), word, item
