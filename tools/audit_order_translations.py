"""Independently check every generated public gloss without rewriting its sources.

The optional follow mode consumes completed batches while generation continues.
No learner database, credentials in outputs, automatic API retries, or tools.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import generate_order_translations as generation

ROOT=generation.ROOT
AUDIT_RULES=generation.INSTRUCTIONS+'''

This is a separate semantic and Chinese-grammar audit of a generated candidate,
not a request to produce a second stylistic paraphrase. Each input also includes
draft_translation and draft_pending_reason, both untrusted candidate DATA.
Check EVERY sentence against its Japanese and the contextual ordinary Chinese.
Return the candidate unchanged if it is faithful and valid Chinese, even when
its aggressive historical syntax is unfamiliar. Never normalize valid literary
order back into standard contemporary Chinese just for fluency. If correction
is needed, retain as much source order as valid Chinese permits and preserve the
aggressive structural policy above. Do not merely replace words with archaisms.

Separately trace who does what to whom: fronted objects and に-marked recipients
or interviewees must not become the acting subject. Trace も/だけ/しか/まで/さえ
scope, actual versus hypothetical events, negation, modality, temporal/aspectual
relations, quotations, and reference of pronouns. Check noun versus verbal or
adjectival judgments before accepting 是也. をはじめ does not imply leadership;
trust-based に限って does not imply an unexpected bad action; ところで can be
counterfactual concession, not an asserted past change. Do not create an unnamed
personal subject. Preserve uncertainty explicitly present in the contextual
translation, unless the Japanese itself resolves it unambiguously. An unknown
proper name can be kept unchanged with its existing uncertainty qualifier.
Do not infer sex, occupation, leadership or social role from さん/様: preserve
respect in context without automatically inventing Mr, a male person or teacher.
Receiving someone's help and helping that person must not be interchanged.

Do not present English dictionary navigation, English definitions or unmistakable
Chinese-source explanations as Japanese-order translations: return null with a
brief reason. Do NOT reject kanji-only Japanese on that criterion alone. For
unresolved ambiguous fragments, return null rather than inventing a sentence.
Only correct the experimental text; never modify either source anchor.
'''
AUDIT_HASH=sha256(AUDIT_RULES.encode('utf-8')).hexdigest()


def audit_batch(path, sources, key, folder):
    raw=path.read_bytes()
    draft=json.loads(raw)
    if draft['policy_sha256']!=generation.POLICY_HASH:
        raise ValueError('Cannot audit a batch generated under another policy')
    identity=sha256(generation.encoded([AUDIT_HASH,sha256(raw).hexdigest()])).hexdigest()
    output=folder/(identity+'.json')
    if output.exists():
        saved=json.loads(output.read_text('utf-8'))
        if saved['audit_sha256']!=AUDIT_HASH or saved['context_ids']!=draft['context_ids']:
            raise ValueError('Cached audit is stale')
        return saved,True
    rows=[sources[identity] for identity in draft['context_ids']]
    if draft['context_ids']!=[r['id'] for r in draft['records']]:
        raise ValueError('Draft order differs from context list')
    inputs=[{'index':i,'source_text':row['source_text'],
             'source_translation':row['source_translation'],
             'draft_translation':(old['order_translation'] or {}).get('text'),
             'draft_pending_reason':old.get('pending_reason','')}
            for i,(row,old) in enumerate(zip(rows,draft['records']))]
    started=time.monotonic()
    response=generation._post('https://api.openai.com/v1/responses',{
        'model':draft['model'],'store':False,'instructions':AUDIT_RULES,
        'input':[{'role':'user','content':[{'type':'input_text',
                   'text':generation.encoded(inputs).decode('utf-8')}]}],
        'text':{'format':{'type':'json_schema','name':'kana_order_gloss_audit_v1',
            'strict':True,'schema':generation.batch_schema(len(rows))}},
        'reasoning':{'effort':'low'},'max_output_tokens':18000},token=key,timeout=180)
    reviewed=generation.validate_rows(generation.decode_response(response),rows)
    result={'format':'kanamemory-order-translation-batch','version':1,
        'model':draft['model'],'policy_sha256':generation.POLICY_HASH,
        'audit_sha256':AUDIT_HASH,'draft_batch':path.name,
        'draft_sha256':sha256(raw).hexdigest(),'context_ids':draft['context_ids'],
        'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':round(time.monotonic()-started,2),'response_id':response.get('id'),
        'usage':{k:v for k,v in (response.get('usage') or {}).items()
                 if k in ('input_tokens','output_tokens','total_tokens') and type(v) is int},
        'changed':sum(a['order_translation']!=b['order_translation'] for a,b in zip(reviewed,draft['records'])),
        'records':reviewed}
    temporary=output.with_suffix('.tmp')
    temporary.write_bytes(generation.encoded(result)+b'\n')
    temporary.replace(output)
    return result,False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,default=ROOT/'data/enrichment/order_translation_corpus_v1.json')
    parser.add_argument('--input',type=Path,default=ROOT/'data/enrichment/order_translations_v2/batches')
    parser.add_argument('--output',type=Path,default=ROOT/'data/enrichment/order_translations_v2/audited_batches')
    parser.add_argument('--report',type=Path,default=ROOT/'environment/order-translations-aggressive-audit-20261029.json')
    parser.add_argument('--follow-generation-report',type=Path)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if not 1<=args.workers<=8:
        parser.error('Workers must be 1..8')
    sources={r['id']:r for r in json.loads(args.corpus.read_text('utf-8'))['records']}
    key=generation.load_key(ROOT/'.env.local')
    args.output.mkdir(parents=True,exist_ok=True)
    report={'format':'kanamemory-order-translation-audit','version':1,
        'policy_sha256':generation.POLICY_HASH,'audit_sha256':AUDIT_HASH,
        'requested_contexts':len(sources),'completed_contexts':0,'changed':0,
        'pending':0,'cached_batches':0,'live_batches':0,'usage':{},'errors':[],
        'public_sources_only':True,'learner_records_sent':False,
        'semantic_review':'model-audited-experimental-not-human-certified'}
    seen=set(); jobs={}; deadline=time.monotonic()+2400
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while True:
            candidates=[p for p in sorted(args.input.glob('*.json')) if p.name not in seen]
            if not report['errors']:
                for path in candidates[:max(0,args.workers-len(jobs))]:
                    jobs[pool.submit(audit_batch,path,sources,key,args.output)]=path.name
                    seen.add(path.name)
            completed,_=wait(jobs,timeout=2,return_when=FIRST_COMPLETED) if jobs else (set(),set())
            for future in completed:
                name=jobs.pop(future)
                try:
                    result,cached=future.result()
                    report['completed_contexts']+=len(result['records'])
                    report['changed']+=result['changed']
                    report['pending']+=sum(r['order_translation'] is None for r in result['records'])
                    report['cached_batches' if cached else 'live_batches']+=1
                    if not cached:
                        for k,v in result['usage'].items():
                            report['usage'][k]=report['usage'].get(k,0)+v
                    print(json.dumps({k:report[k] for k in ('completed_contexts','changed','pending')}),flush=True)
                except Exception as exc:
                    report['errors'].append({'batch':name,'code':getattr(exc,'code',type(exc).__name__)})
            report['passed']=report['completed_contexts']==len(sources) and not report['errors']
            args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
            if report['passed'] or report['errors'] and not jobs:
                break
            remaining=any(p.name not in seen for p in args.input.glob('*.json'))
            if not jobs and not remaining:
                if not args.follow_generation_report:
                    break
                try:
                    upstream=json.loads(args.follow_generation_report.read_text('utf-8'))
                except (OSError,ValueError):
                    upstream={}
                if upstream.get('passed') or upstream.get('errors'):
                    break
                time.sleep(2)
            if time.monotonic()>deadline:
                raise TimeoutError('Public corpus audit exceeded the bounded 40-minute window')
    if report['errors'] or not report['passed']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
