"""Shared feedback sources fixtures and assertions; no collected scenarios."""
from kanamemory.public_excerpt_fetch import EXTRACTOR
from kanamemory.feedback_sources import FeedbackSourceStore
from kanamemory.feedback_sources import SCOPE
from hashlib import sha256


URL = 'https://www.coelang.tufs.ac.jp/mt/ja/gmod/contents/explanation/001.html'


TEXT = 'この説明は資料からの引用です。です・ますを使います。別の説明。'


QUOTE = 'です・ますを使います。'


def document(text=TEXT):
    return {'mode': 'retrieved', 'url': URL, 'final_url': URL, 'retrieved_at': '2026-11-02T12:00:00+00:00',
        'raw_sha256': sha256(text.encode()).hexdigest(), 'text': text, 'text_sha256': sha256(text.encode()).hexdigest(), 'extractor': EXTRACTOR}


def attachment(service, parent, *, pasted=False):
    sources = FeedbackSourceStore(service)
    if pasted: doc = sources.paste(parent['id'], TEXT)
    else:
        token = sources.begin_fetch(parent['id'], URL)
        doc = sources.finish_fetch(parent['id'], token, document())['documents'][0]
    excerpt = sources.add_excerpt(parent['id'], doc['id'], parent['request']['bindings'][0]['id'], QUOTE,
        {k: '未确定' for k in SCOPE}, relationship='independent_user_asserted', reason='用户核对说明，不作为系统验证。')
    return sources, doc, excerpt
