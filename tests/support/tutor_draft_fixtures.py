"""Synthetic completed tutor history, independent of private course material."""
from kanamemory import tutor_store as module


def completed_state(count=3):
    value = module._empty()
    value.update(session_id='synthetic-draft', completed_count=count)
    for index in range(count):
        question = dict(id=f'q{index}', question_id=f'q{index}', target_id='grammar:synthetic',
            revision='1', kind='grammar_meaning', ability='understanding', family='grammar',
            prompt=f'これは例文{index}です。', answer='说明类别', choices=['说明类别', '否定'],
            input_mode='choice', scheduled=False, tutor_practice=True,
            evidence_mode='exposure_only', guided=False, variant=index)
        draft = {'answer': question['answer']}
        stamp = '2026-11-01T12:00:00+00:00'
        value['entries'].append(dict(id=f'row{index}', question=question, source='local',
            fingerprint=module.question_fingerprint(question), draft=draft,
            result=module._result(question, draft, question['answer'], False, stamp),
            status='completed', created_at_utc=stamp, completed_at_utc=stamp))
    value['seen'] = [row['fingerprint'] for row in value['entries']]
    return module.validate_tutor_value(value)
