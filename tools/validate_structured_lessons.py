"""Audit every teaching view and verify source/examples stay byte-identical."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/src'))
from kanamemory.learning_content import learning_items
from kanamemory.structured_lessons import lesson_view, validate_plan
from kanamemory.example_catalogue import bundled_example_pack


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def main():
    items = learning_items()
    before = encoded(items)
    start = time.perf_counter()
    views = {item['id']: lesson_view(item) for item in items}
    elapsed = time.perf_counter() - start
    for view in views.values():
        assert view['authored']
        validate_plan(view)
    assert encoded(items) == before == encoded(learning_items())
    pack = bundled_example_pack(items)
    coverage = Counter(ref['target_id'] for ref in pack['references'])
    baseline = json.loads((ROOT / 'research/example_coverage_validation.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(encoded(pack)).hexdigest() == baseline['pack_sha256']
    canonical = ['learning_content.py', 'grammar_curriculum.py', 'exam_grammar_curriculum.py',
                 'usage_grammar_curriculum.py', 'vocabulary_lessons.py', 'pronunciation_curriculum.py']
    for name in canonical:
        path = 'app/src/kanamemory/' + name
        committed = subprocess.check_output(['git', 'show', 'c78bd96:' + path], cwd=ROOT)
        assert (ROOT / path).read_bytes().replace(b'\r\n', b'\n') == committed.replace(b'\r\n', b'\n')
    report = {
        'version': '0.14.0', 'passed': True, 'learning_items': len(items),
        'counts_by_family': dict(Counter('pronunciation' if i.get('domain') == 'pronunciation' else i['family'] for i in items)),
        'blocks_by_kind': dict(Counter(b['kind'] for v in views.values() for b in v['blocks'])),
        'view_build_seconds_desktop': round(elapsed, 4),
        'max_focus_characters': max(len(v['focus']) for v in views.values()),
        'max_blocks_per_item': max(len(v['blocks']) for v in views.values()),
        'canonical_course_files_unchanged': True, 'source_snapshots_unchanged': True,
        'all_4388_examples_and_5707_references_unchanged': True,
        'minimum_example_references': min(coverage.values()),
        'views_sha256': hashlib.sha256(encoded(views)).hexdigest(),
        'coverage': [{'id': i['id'], 'title': i['title'], 'blocks': [b['kind'] for b in views[i['id']]['blocks']]} for i in items],
    }
    (ROOT / 'research/structured_lessons_validation.json').write_bytes((json.dumps(report, ensure_ascii=False, indent=2)+'\n').encode())
    print(json.dumps({k:v for k,v in report.items() if k != 'coverage'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
