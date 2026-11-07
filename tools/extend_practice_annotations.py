"""Append annotations for reviewed practice prompts; keep existing rows exact.

Uses the existing offline annotator, citation dictionary and editorial ledgers.
Only public content-pack text is read. No progress database, network or API.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'app/src'), str(ROOT/'tools')]


def project_unchanged_annotations(text, original, record, span):
    """Keep context-derived readings outside one explicit public replacement.

    A gap is never sent to a tokenizer as though it were an ordinary sentence.
    Segments crossing it are omitted, not truncated or filled from the answer.
    """
    from kanamemory.sentence_annotations import KANJI
    start, end = span['start'], span['end']
    source_end = len(original) - (len(text) - end)
    if (record.get('text') != original or not 0 <= start < end <= len(text)
            or not start < source_end <= len(original)
            or text[start:end] != span['quote'] or KANJI.search(span['quote'])
            or text[:start] != original[:start] or text[end:] != original[source_end:]):
        return None
    delta = end - source_end
    out = {'text': text, 'version': record['version'], 'status': 'precomputed', 'segments': []}
    for field in ('segments', 'excluded'):
        rows = []
        for entry in record.get(field, []):
            if entry['start'] < source_end and entry['end'] > start:
                continue
            copied = deepcopy(entry)
            if copied['start'] >= source_end:
                copied['start'] += delta
                copied['end'] += delta
            if text[copied['start']:copied['end']] != copied['surface']:
                return None
            rows.append(copied)
        if rows or field == 'segments':
            out[field] = rows
    return out


def common_projection(plans):
    """Ambiguous source readings stay pending instead of choosing an answer."""
    result = deepcopy(plans[0])
    for field in ('segments', 'excluded'):
        result[field] = [entry for entry in result.get(field, [])
                         if all(entry in plan.get(field, []) for plan in plans[1:])]
    return result


def project_context_spans(text, original, record, spans):
    """Project whole tokens through a unique set of public edit regions.

    Neither answers nor tokenizer guesses participate. Repeated anchors that
    allow different alignments fail closed; crossings and edit tokens disappear.
    """
    if record.get('text') != original or not spans:
        return None
    chunks, offset = [], 0
    for span in spans:
        start, end = span['start'], span['end']
        if not offset <= start < end <= len(text) or text[start:end] != span['quote']:
            return None
        chunks.append((offset, text[offset:start]))
        offset = end
    chunks.append((offset, text[offset:]))
    if not original.startswith(chunks[0][1]) or any(not s for _, s in chunks[1:-1]):
        return None
    paths = []
    def visit(index, position, found):
        if len(paths) > 1:
            return
        if index == len(chunks):
            if position == len(original): paths.append(found)
            return
        target, chunk = chunks[index]
        if index == len(chunks) - 1:
            starts = [len(original) - len(chunk)] if original.endswith(chunk) else []
        else:
            starts, cursor = [], position
            while (cursor := original.find(chunk, cursor)) >= 0:
                starts.append(cursor)
                cursor += 1
        for start in starts:
            if start >= position:
                visit(index + 1, start + len(chunk), [*found, (start, start + len(chunk), target)])
    visit(1, len(chunks[0][1]), [(0, len(chunks[0][1]), 0)])
    if len(paths) != 1:
        return None
    out = {'text': text, 'version': record['version'], 'status': 'precomputed', 'segments': [],
           'context_projection_sha256': sha256(original.encode()).hexdigest()}
    for field in ('segments', 'excluded'):
        for entry in record.get(field, []):
            for start, end, target in paths[0]:
                if start <= entry['start'] < entry['end'] <= end:
                    copied = deepcopy(entry)
                    copied['start'] += target - start
                    copied['end'] += target - start
                    if text[copied['start']:copied['end']] != copied['surface']:
                        return None
                    out.setdefault(field, []).append(copied)
                    break
    return out


def bound_contexts(paths):
    """Load final authoring bindings in explicit override order, hash checked."""
    from build_varied_practice import digest
    rows = {}
    for path in paths:
        for line in Path(path).read_text('utf8').splitlines():
            row = json.loads(line)
            projection, binding = row['projection'], row['binding']
            if digest(projection) != binding['projection_sha256'] or projection['slot_id'] != row['slot_id']:
                raise ValueError('Authoring projection fingerprint mismatch')
            rows[row['slot_id']] = row
    result = defaultdict(list)
    for row in rows.values():
        p, b = row['projection'], row['binding']
        contexts = b['source_contexts']
        if (not contexts or not all(isinstance(s, str) and s for s in contexts)
                or '\n'.join(contexts) != p['context_text']):
            continue
        result[(p['target_id'], p['kind'], p['question']['prompt'])].append((contexts, p['target_spans']))
    return result


def resolve_public_plan(text, projections, fallback):
    """Never analyze an incomplete sentence when a source projection failed."""
    if projections and all(p is not None for p in projections):
        return common_projection(projections)
    from kanamemory.question_support import _GAP
    if projections or _GAP.search(text):
        return {'text': text, 'version': 1, 'segments': [], 'status': 'precomputed'}
    return fallback(text)


def extend(database, report_path, *, bindings=()):
    from kanamemory.learning_content import learning_items
    from kanamemory.learning_questions import practice_templates, LISTENING_KINDS
    from kanamemory.sentence_annotations import FORMAT, VERSION, _validated, validated_accent_phrases
    from sentence_annotation_corpus import public_prompt_parts
    from build_sentence_annotations import Annotator, dictionary_entries, apply_editorial_records
    from annotation_followup_review import apply_reviews, load_reviews
    from sentence_annotation_overrides import EXACT_OVERRIDES, EXACT_EXCLUSIONS
    from kanamemory.example_catalogue import bundled_example_pack

    database, report_path = Path(database), Path(report_path)
    prompts, origins = set(), defaultdict(list)
    bound = bound_contexts(bindings)
    items = learning_items()
    examples = {e['id']: e['text'] for e in bundled_example_pack(items)['examples']}
    prompts.update(examples.values())
    for item in items:
        for kind, rows in (practice_templates(item) or {}).items():
            for variant, raw in enumerate(rows):
                if 'practice' not in raw:
                    continue
                # Reviewed practice already stores its exact displayed prompt.
                # Do not rebuild/decorate the entire question bank just to read it.
                q = dict(raw, kind=kind, domain=item.get('domain'), requires_audio=kind in LISTENING_KINDS)
                if q.get('requires_audio') or q.get('has_hidden_transcript'):
                    continue
                public = public_prompt_parts(q)
                prompts.update(public)
                if q['prompt'] in public:
                    for contexts, spans in bound.get((item['id'], kind, q['prompt']), []):
                        if spans and '\n'.join(contexts) != q['prompt']:
                            origins[q['prompt']].append((contexts, spans))
                original = examples.get(raw['practice'].get('example_id'))
                spans = raw['practice'].get('target_spans', [])
                if original and original != q['prompt'] and q['prompt'] in public and len(spans) == 1:
                    pair = ([original], spans)
                    if pair not in origins[q['prompt']]:
                        origins[q['prompt']].append(pair)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as before:
        metadata = dict(before.execute('SELECT key,value FROM meta'))
        if metadata.get('format') != FORMAT or metadata.get('version') != str(VERSION):
            raise ValueError('Only an existing annotation content sidecar may be extended')
        old = dict(before.execute('SELECT fingerprint,record FROM sentence'))
        old_fallback = list(before.execute('SELECT * FROM fallback ORDER BY surface'))
        missing = sorted(text for text in prompts if sha256(text.encode()).hexdigest() not in old)
        handle=tempfile.NamedTemporaryFile(prefix='.practice-annotations-',suffix='.sqlite3',dir=database.parent,delete=False)
        temp=Path(handle.name);handle.close()
        with closing(sqlite3.connect(temp)) as copy:
            before.backup(copy)
    errors, unresolved, added, projected, skipped = [], [], 0, 0, 0
    before_hash=sha256(database.read_bytes()).hexdigest()
    try:
        annotator=Annotator(dictionary_entries(ROOT/'data/enrichment/raw/kanjium_accents.txt')) if missing else None
        reviews=load_reviews()
        source_plans = {}
        def full_plan(text):
            if text in source_plans:
                return source_plans[text]
            frozen = old.get(sha256(text.encode()).hexdigest())
            if frozen:
                plan = json.loads(frozen)
            else:
                plan = annotator.annotate(text)
                plan = apply_editorial_records(text,plan,EXACT_OVERRIDES.get(text,()),EXACT_EXCLUSIONS.get(text,()))
                plan = apply_reviews(text,plan,reviews.get(text,()))
            plan = _validated(text, plan)
            if plan is None:
                raise ValueError('Invalid full-sentence annotation')
            source_plans[text] = plan
            return plan
        def project(text, contexts, spans):
            original = '\n'.join(contexts)
            combined = {'text': original, 'version': VERSION, 'segments': [], 'excluded': []}
            offset = 0
            for context in contexts:
                plan = full_plan(context)
                for field in ('segments', 'excluded'):
                    for row in plan.get(field, []):
                        combined[field].append({**row, 'start': row['start'] + offset, 'end': row['end'] + offset})
                offset += len(context) + 1
            return project_context_spans(text, original, combined, spans)
        with closing(sqlite3.connect(temp)) as destination:
            for text in missing:
                try:
                    projections = [] if text in examples.values() else [project(text, contexts, spans) for contexts, spans in origins[text]]
                    plan = full_plan(text) if text in examples.values() else resolve_public_plan(text, projections, full_plan)
                    projected += bool(projections and all(p is not None for p in projections))
                    plan = _validated(text, plan)
                    if plan is None:
                        raise ValueError('Invalid derived annotation')
                except (ValueError,RuntimeError) as exc:
                    errors.append({'text':text,'error':str(exc)[:200]})
                    continue
                unresolved.extend({'text':text,'surface':s['surface'],'reading':s.get('reading')}
                    for s in plan['segments'] if s.get('reading') is None
                    or s.get('accent_number') is None and not validated_accent_phrases(s))
                covered = {i for s in plan['segments'] for i in range(s['start'], s['end'])}
                unresolved.extend({'text':text,'surface':s['surface'],'reading':None}
                    for s in plan.get('unresolved', [])
                    if any(i not in covered for i in range(s['start'], s['end'])))
                from kanamemory.question_support import _GAP
                if not plan['segments'] and (origins[text] or _GAP.search(text)):
                    # Keep the old conservative fallback rather than shadowing
                    # it with a newly manufactured empty exact record.
                    skipped += 1
                    continue
                destination.execute('INSERT INTO sentence VALUES (?,?,?)',
                    (sha256(text.encode()).hexdigest(),text,json.dumps(plan,ensure_ascii=False,separators=(',',':'))))
                added += 1
            destination.commit()
            now=dict(destination.execute('SELECT fingerprint,record FROM sentence'))
            assert all(now.get(key)==value for key,value in old.items())
            assert list(destination.execute('SELECT * FROM fallback ORDER BY surface'))==old_fallback
            assert dict(destination.execute('SELECT key,value FROM meta'))==metadata
            assert destination.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        temp.replace(database)
    finally:
        temp.unlink(missing_ok=True)
    report={'format':'kanamemory-practice-annotation-extension-v1','passed':not errors,
        'public_texts':len(prompts),'previous_sentences':len(old),'inserted_sentences':added,
        'projected_from_full_sentence_context':projected,
        'unresolved_incomplete_texts_left_at_existing_fallback': skipped,
        'old_sentence_rows_unchanged':True,'fallback_and_metadata_unchanged':True,
        'before_sha256':before_hash,'after_sha256':sha256(database.read_bytes()).hexdigest(),
        'failures':errors,'pending_word_occurrences':len(unresolved),'pending':unresolved,
        'limits':['Existing automatic annotation provenance and uncertainty rules apply.',
                  'Unknown accents remain unknown, never coerced to zero.',
                  'Runtime independently blocks ruby/audio for reading and hidden-answer tasks.'],
        'learner_data_accessed':False,'network_used':False}
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'app/src/kanamemory/resources/sentence_annotations.sqlite3')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--bindings', type=Path, action='append', default=[])
    args=parser.parse_args()
    result=extend(args.database,args.report,bindings=args.bindings)
    print(json.dumps({key:result[key] for key in ('passed','public_texts','inserted_sentences','pending_word_occurrences','old_sentence_rows_unchanged')}))
