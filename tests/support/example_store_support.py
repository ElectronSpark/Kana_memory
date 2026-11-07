"""Shared example store fixtures and assertions; no collected scenarios."""
from kanamemory.example_store import empty_pack
from kanamemory.example_store import example_id_for
from support.learning_store_fixtures import item
from kanamemory.example_store import reference_id_for


def targets():
    return [item('grammar:topic', title='话题は'),
            item('grammar:polite', title='礼貌句'),
            item('word:student', title='学生', family='word', reading='がくせい',
                 abilities=['understanding', 'reading', 'use', 'listening'])]


def reference(example, target, quote, **kwargs):
    start = example['text'].index(quote)
    value = {'example_id': example['id'], 'example_revision': example['revision'],
             'target_id': target, 'spans': [{'start': start, 'end': start + len(quote), 'quote': quote}],
             'hint': {'rule': '提示当前语境'}, 'explanation': '本句中的用法。', 'weight': 1.0, **kwargs}
    value['id'] = reference_id_for(value)
    return value


def pack():
    example = {'id': example_id_for('私は学生です。'), 'revision': '1', 'text': '私は学生です。',
               'translation': '我是学生。', 'reading': 'わたしはがくせいです。', 'source_ids': []}
    refs = [reference(example, 'grammar:topic', 'は', hint={'rule': 'は标记话题'}),
            reference(example, 'grammar:polite', 'です', hint={'rule': 'です使表达礼貌'}),
            reference(example, 'word:student', '学生', hint={'meaning': '学生', 'reading': 'がくせい'})]
    return {**empty_pack(), 'examples': [example], 'references': refs}


def snapshot(store):
    names = [row[0] for row in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {name: [tuple(row) for row in store._db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
            for name in names if name != 'app_setting'}
