"""Validate the packaged local curriculum and write an auditable coverage report.

Structural checks are not a claim of native-speaker editorial certification.
Content review and Android manual use are separately recorded in the release.
"""
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/src'))

from kanamemory.learning_content import learning_items, learning_sources
from kanamemory.learning_dependencies import validate_catalog
from kanamemory.learning_questions import build_learning_question, grade_learning_answer, question_kinds, practice_templates
from kanamemory.question_support import prompt_audio_text, prompt_support


def can_compose(answer, parts):
    """Use every visible block once, including duplicate-text blocks."""
    @lru_cache(None)
    def visit(offset, used):
        if used == (1 << len(parts)) - 1:
            return offset == len(answer)
        return any(not (used & (1 << index)) and answer.startswith(part, offset)
                   and visit(offset + len(part), used | (1 << index))
                   for index, part in enumerate(parts))
    return bool(parts) and all(parts) and visit(0, 0)


def inspect():
    items = learning_items()
    ordering = validate_catalog(items)
    questions, safe_audio, annotations = 0, 0, 0
    kinds = Counter()
    failures = []
    for item in items:
        try:
            for field in ('summary', 'rule', 'example', 'translation'):
                assert item.get('instruction', {}).get(field), f'Missing introduction {field}'
            available = question_kinds(item)
            templates = practice_templates(item)
            assert available, 'No questions'
            guide_kind = ('pronunciation_rule' if item.get('domain') == 'pronunciation' else
                          'vocab_meaning' if item['family'] == 'word' else 'grammar_meaning')
            guide = build_learning_question(item, guide_kind, guided=True, variant=1)
            assert guide['evidence_mode'] == 'exposure_only'
            assert guide['input_mode'] in {'choice', 'text', 'reorder', 'pitch_fill', 'pitch_identify'}
            assert not prompt_audio_text(guide)
            for kind in available:
                variants = len((templates or {}).get(kind, [])) or 2
                for variant in range(variants):
                    q = build_learning_question(item, kind, variant=variant)
                    assert q['ability'] in item['abilities'], 'Question targets an absent ability'
                    assert grade_learning_answer(q, q['answer'])['correct'] is True, 'Own answer rejected'
                    if q['input_mode'] == 'choice':
                        assert len(q['choices']) == len(set(q['choices'])) >= 2, 'Duplicate choices'
                        for choice in q['choices']:
                            assert grade_learning_answer(q, choice)['correct'] == (choice == q['answer'])
                    elif q['input_mode'] == 'reorder':
                        assert can_compose(q['answer'], q['choices']), 'Answer cannot use all word blocks exactly once'
                        assert not prompt_audio_text(q), 'Reorder answer must not be read aloud'
                        assert q['answer'] not in q['prompt'], 'Reorder answer visible in prompt'
                    audio = prompt_audio_text(q)
                    if audio:
                        assert audio == q['prompt'], 'Audio is not public prompt'
                        # Semantic extraction may select public text itself;
                        # hidden reading, gaps and production remain forbidden.
                        assert q['kind'] in {'vocab_meaning', 'vocab_zh_ja_contrast',
                                             'grammar_meaning', 'reading_comprehension'}
                        assert q['input_mode'] == 'choice'
                        assert not q.get('tests_reading') and not q.get('has_gap')
                    support = prompt_support(q)
                    safe_audio += bool(audio)
                    annotations += bool(support.get('furigana_visible'))
                    questions += 1
                    kinds[kind] += 1
        except Exception as error:
            failures.append({'id': item['id'], 'error': str(error)})
    return {
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'passed': not failures,
        'content_count': len(items), 'families': dict(Counter(i['family'] for i in items)),
        # 'families' preserves the on-disk schema; groups mirror the library.
        'learning_groups': dict(Counter('pronunciation' if i.get('domain') == 'pronunciation'
                                        else i['family'] for i in items)),
        'grammar_categories': dict(Counter(i.get('category', '基础') for i in items
                                           if i['family'] == 'grammar' and i.get('domain') != 'pronunciation')),
        'pronunciation_categories': dict(Counter(i.get('category', '基础') for i in items
                                                 if i.get('domain') == 'pronunciation')),
        'questions_checked': questions, 'question_kinds': dict(kinds),
        'safe_optional_audio_questions': safe_audio, 'annotated_questions': annotations,
        'dependency_edges': sum(len(i.get('depends_on', [])) for i in items),
        'tracks': dict(Counter(i.get('curriculum_track', 'foundation') for i in items)),
        'topological_order': ordering, 'sources': learning_sources(), 'failures': failures,
        'scope': 'Modern standard Japanese, including common spoken, written and polite registers; not an exhaustive inventory of all living dialects or pragmatic uses.',
    }


if __name__ == '__main__':
    report = inspect()
    destination = ROOT / 'research/curriculum_validation.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps({key: report[key] for key in ('passed', 'learning_groups', 'questions_checked', 'dependency_edges', 'failures')}, ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)
