"""Select exact reviewed candidates, retaining honest partial-audit provenance.

Completed independent audits supersede their exact draft batch; editorial
completion may only fill contexts absent from the API drafts. No old-style
fallback is permitted. This path needs no network or learner data.
"""
from hashlib import sha256
import json
from pathlib import Path
import re

from build_order_translations import build
from generate_order_translations import POLICY_HASH, ROOT
from kanamemory.order_translations import context_id, validate_order_translation


OFFLINE_FORMAT='kanamemory-order-translation-editorial-completion'


def offline_input_name(output_name):
    if not isinstance(output_name,str) or not re.fullmatch(r'part_[1-9]\d*_output\.json',output_name):
        raise ValueError('Invalid editorial completion filename')
    return output_name.removesuffix('_output.json')+'_input.json'


def validate_offline_completion(name, raw_input, document, *, policy_hash=POLICY_HASH):
    """Retain the declared policy and bind completion to the exact reviewed input."""
    fields={'format','version','policy_sha256','input_file','input_sha256','records'}
    if (not isinstance(document,dict) or set(document)!=fields
            or document.get('format')!=OFFLINE_FORMAT or type(document.get('version')) is not int
            or document['version']!=1):
        raise ValueError('Invalid or missing editorial completion provenance header')
    if document['policy_sha256']!=policy_hash:
        raise ValueError('Editorial completion belongs to another translation policy')
    if (document['input_file']!=offline_input_name(name)
            or document['input_sha256']!=sha256(raw_input).hexdigest()):
        raise ValueError('Editorial completion is not bound to its exact source input')
    source=json.loads(raw_input)
    if (source.get('format')!='kanamemory-order-translation-corpus'
            or type(source.get('version')) is not int or source['version']!=1
            or not isinstance(source.get('records'),list)):
        raise ValueError('Unsupported editorial source input')
    inputs=source['records']; rows=document['records']
    ids=[r['id'] for r in inputs]
    if (len(set(ids))!=len(ids) or not isinstance(rows,list)
            or [r.get('id') for r in rows]!=ids):
        raise ValueError('Editorial completion differs from its exact input contexts')
    for original,row in zip(inputs,rows):
        if original['id']!=context_id(original['source_text'],original['source_translation']):
            raise ValueError('Mismatched editorial source identity')
        if set(row)!={'id','order_translation','pending_reason'}:
            raise ValueError('Unexpected editorial completion fields')
        value=row['order_translation']; reason=row['pending_reason']
        if not isinstance(reason,str) or (value is None and not reason.strip()) or (value is not None and reason):
            raise ValueError('Invalid editorial completion pending reason')
        if value is not None:
            validate_order_translation(value,source_text=original['source_text'],
                                       source_translation=original['source_translation'])
    return {'format':'kanamemory-order-translation-batch','version':1,
        'policy_sha256':document['policy_sha256'],'model':'session-assistant-editorial',
        'generation_method':'offline_editorial_translation','source_file':name,
        'input_file':document['input_file'],'input_sha256':document['input_sha256'],
        'context_ids':ids,'records':rows}


def select_batches(corpus, drafts, audits, offline, *, policy_hash=POLICY_HASH):
    expected={r['id'] for r in corpus['records']}
    chosen={}; provenance={}; used_audits=set()
    for name,(raw,document) in drafts.items():
        if document['policy_sha256']!=policy_hash:
            raise ValueError('Draft belongs to another translation policy')
        selected=document; method='api_first_pass'
        candidates=[(n,a) for n,a in audits.items() if a.get('draft_batch')==name]
        if len(candidates)>1:
            raise ValueError('Multiple independent audits require explicit editorial selection')
        if candidates:
            audit_name,selected=candidates[0]
            if (selected.get('draft_sha256')!=sha256(raw).hexdigest()
                    or selected['context_ids']!=document['context_ids']
                    or selected['policy_sha256']!=policy_hash):
                raise ValueError('Independent audit is not bound to this exact draft')
            method='independent_api_audit'; used_audits.add(audit_name)
        for row in selected['records']:
            if row['id'] in chosen:
                raise ValueError('Overlapping generated contexts')
            chosen[row['id']]=selected
            provenance[row['id']]=method
    if used_audits!=set(audits):
        raise ValueError('Orphan independent audit')
    for name,(raw_input,completion) in offline.items():
        document=validate_offline_completion(name,raw_input,completion,policy_hash=policy_hash)
        for row in document['records']:
            if row['id'] in chosen:
                raise ValueError('Editorial completion must not overwrite an existing candidate')
            chosen[row['id']]=document
            provenance[row['id']]='offline_editorial_translation'
    if set(chosen)!=expected:
        raise ValueError(f'Incomplete corpus: {len(expected-set(chosen))} missing; {len(set(chosen)-expected)} unexpected')
    documents=list({id(document):document for document in chosen.values()}.values())
    counts={method:sum(v==method for v in provenance.values()) for method in set(provenance.values())}
    return documents,{'contexts':len(chosen),'selected_methods':counts,
        'all_contexts_processed_under_current_policy':True,
        'full_independent_api_audit_complete':counts.get('independent_api_audit',0)==len(chosen),
        'old_style_candidates_used':False,'per_context_method':provenance}


def main():
    base=ROOT/'data/enrichment/order_translations_v2'
    corpus=json.loads((ROOT/'data/enrichment/order_translation_corpus_v1.json').read_text('utf-8'))
    drafts={p.name:(p.read_bytes(),json.loads(p.read_text('utf-8'))) for p in (base/'batches').glob('*.json')}
    audits={p.name:json.loads(p.read_text('utf-8')) for p in (base/'audited_batches').glob('*.json')}
    completion=ROOT/'research/order_translation_review/offline_completion'
    offline={p.name:((completion/offline_input_name(p.name)).read_bytes(),
                    json.loads(p.read_text('utf-8'))) for p in completion.glob('part_*_output.json')}
    documents,selection=select_batches(corpus,drafts,audits,offline)
    override_path=base/'editorial_corrections.json'
    if not override_path.exists():
        raise ValueError('The reviewed final editorial corrections are required')
    report=build(corpus,documents,ROOT/'app/src/kanamemory/resources/order_translations.sqlite3',
        overrides=json.loads(override_path.read_text('utf-8')),policy_hash=POLICY_HASH)
    report['selection']={k:v for k,v in selection.items() if k!='per_context_method'}
    report['independent_audit_limitation']='External API stopped with credit_balance_exhausted; first-pass-only entries remain experimental.'
    (base/'selection_manifest.json').write_text(json.dumps(selection,ensure_ascii=False,indent=2)+'\n','utf-8')
    (ROOT/'environment/order-translations-aggressive-build-20261029.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'pending'}},ensure_ascii=False))
    print(json.dumps({'pending':len(report['pending'])}))


if __name__=='__main__':
    main()
