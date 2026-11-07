"""Audit the ten-example floor, additive identity and portable pack contract."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app/src'))
from kanamemory import example_catalogue
from kanamemory.example_store import validate_pack
from kanamemory.learning_content import learning_items
from kanamemory.build_info import VERSION


def main():
    items = learning_items()
    before = deepcopy(items)
    start = time.perf_counter()
    pack = example_catalogue.bundled_example_pack(items)
    elapsed = time.perf_counter() - start
    assert before == items
    validate_pack(pack, {i['id'] for i in items})
    encoded = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    refs = {r['id']: r for r in pack['references']}
    examples = {e['id']: e for e in pack['examples']}
    coverage = {i['id']: set() for i in items}
    for ref in refs.values():
        coverage[ref['target_id']].add(ref['example_id'])
    assert all(len(ids) >= 10 for ids in coverage.values())
    original_rows = example_catalogue.expansion_rows
    try:
        example_catalogue.expansion_rows = lambda item: ()
        original = example_catalogue.bundled_example_pack(items)
    finally:
        example_catalogue.expansion_rows = original_rows
    assert all(examples[e['id']] == e for e in original['examples'])
    assert all(refs[r['id']] == r for r in original['references'])
    assert example_catalogue.bundled_example_pack(reversed(items)) == pack
    sharing = Counter(r['example_id'] for r in refs.values())
    report = {
        'version': VERSION, 'format_version': pack['version'], 'passed': True,
        'learning_items': len(items), 'examples': len(examples), 'references': len(refs),
        'shared_examples': sum(n > 1 for n in sharing.values()),
        'max_targets_per_example': max(sharing.values()),
        'minimum_examples_per_item': min(map(len, coverage.values())),
        'maximum_examples_per_item': max(map(len, coverage.values())),
        'reviewed_core_examples_retained': len(original['examples']),
        'reviewed_core_references_retained': len(original['references']),
        'input_item_snapshots_not_mutated': True, 'order_independent': True,
        'linguistic_accuracy_proven_by_this_structural_check': False,
        'pack_bytes_utf8': len(encoded), 'pack_sha256': hashlib.sha256(encoded).hexdigest(),
        'build_seconds_desktop': round(elapsed, 4),
        'counts_by_family': dict(Counter('pronunciation' if i.get('domain') == 'pronunciation'
                                       else i['family'] for i in items)),
        'coverage': [{'id': i['id'], 'title': i['title'], 'examples': len(coverage[i['id']])}
                     for i in sorted(items, key=lambda i: i['id'])],
    }
    path = ROOT / 'research/example_coverage_validation.json'
    path.write_bytes((json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode())
    print(json.dumps({k: v for k, v in report.items() if k != 'coverage'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
