"""Compile checked public-context glosses into an immutable runtime sidecar."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src'))
from kanamemory.order_translations import FORMAT, VERSION, context_id, validate_order_translation


def build(corpus, batches, output, *, overrides=None, policy_hash=None):
    if corpus.get('format')!='kanamemory-order-translation-corpus' or corpus.get('version')!=1:
        raise ValueError('Unsupported bilingual corpus')
    sources={row['id']:row for row in corpus['records']}
    if len(sources)!=len(corpus['records']):
        raise ValueError('Duplicate corpus identity')
    for identity,row in sources.items():
        if identity!=context_id(row['source_text'],row['source_translation']):
            raise ValueError('Mismatched bilingual corpus identity')
    records={}
    provenance={}
    for document in batches:
        if (document.get('format')!='kanamemory-order-translation-batch' or document.get('version')!=1):
            raise ValueError('Unsupported batch')
        if policy_hash and document.get('policy_sha256')!=policy_hash:
            continue
        if document.get('context_ids')!=[row['id'] for row in document['records']]:
            raise ValueError('Generated batch order differs from its context list')
        for row in document['records']:
            identity=row['id']
            if identity not in sources:
                continue  # Earlier corpus contexts are retained, never retargeted.
            source=sources[identity]
            value=row['order_translation']
            if value is not None:
                value=validate_order_translation(value,source_text=source['source_text'],
                                                source_translation=source['source_translation'])
            elif not isinstance(row.get('pending_reason'),str) or not row['pending_reason'].strip():
                raise ValueError('Missing gloss has no review reason')
            normalized={'order_translation':value,'pending_reason':row.get('pending_reason','')}
            if identity in records and records[identity]!=normalized:
                raise ValueError('Conflicting generated glosses require an explicit review')
            records[identity]=normalized
            provenance[identity]={'model':document['model'],'policy_sha256':document['policy_sha256'],
                'source_review_states':source.get('review_states',[]),'curated':False}
            if document.get('generation_method'):
                provenance[identity]['generation_method']=document['generation_method']
            if document.get('audit_sha256'):
                provenance[identity]['audit_sha256']=document['audit_sha256']
                provenance[identity]['draft_batch_sha256']=document['draft_sha256']
    missing=sorted(sources.keys()-records.keys())
    if missing:
        raise ValueError(f'Generation has not covered {len(missing)} bilingual contexts')
    for row in (overrides or {}).get('records',[]):
        identity=row['id']
        if identity not in records or not row.get('reason','').strip():
            raise ValueError('Unknown or unexplained editorial correction')
        old=records[identity]['order_translation']
        if row['previous_text']!=(old['text'] if old else None):
            raise ValueError('Editorial correction is stale')
        source=sources[identity]
        value=(validate_order_translation({'version':1,'status':'experimental',
            'source_text':source['source_text'],'source_translation':source['source_translation'],
            'text':row['text']},source_text=source['source_text'],source_translation=source['source_translation'])
            if row['text'] is not None else None)
        records[identity]={'order_translation':value,'pending_reason':'' if value else row['reason']}
        provenance[identity].update(curated=True,review_reason=row['reason'])
    pending=[{'id':identity,'source_text':sources[identity]['source_text'],
              'source_translation':sources[identity]['source_translation'],
              'reason':row['pending_reason']} for identity,row in records.items()
             if row['order_translation'] is None]
    counts=Counter('pending' if row['order_translation'] is None else 'available' for row in records.values())
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.building.sqlite3')
    if temporary.exists():
        raise ValueError('An unfinished sidecar build exists; inspect it before retrying')
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript('''
                PRAGMA user_version=1;
                CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE translation(id TEXT PRIMARY KEY, source_text TEXT NOT NULL,
                    source_translation TEXT NOT NULL,text TEXT NOT NULL, metadata_json TEXT NOT NULL);
                CREATE TABLE pending(id TEXT PRIMARY KEY,source_text TEXT NOT NULL,
                    source_translation TEXT NOT NULL,reason TEXT NOT NULL);
            ''')
            db.executemany('INSERT INTO metadata VALUES (?,?)',[
                ('format',FORMAT),('version',str(VERSION)),('policy_sha256',policy_hash or ''),
                ('context_count',str(len(sources))),('available_count',str(counts['available'])),
                ('status','experimental')])
            for identity in sorted(records):
                value=records[identity]['order_translation']
                source=sources[identity]
                if value:
                    db.execute('INSERT INTO translation VALUES (?,?,?,?,?)',
                        (identity,source['source_text'],source['source_translation'],value['text'],
                         json.dumps(provenance[identity],ensure_ascii=False,separators=(',',':'))))
                else:
                    db.execute('INSERT INTO pending VALUES (?,?,?,?)',
                        (identity,source['source_text'],source['source_translation'],records[identity]['pending_reason']))
            db.commit()
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Corrupt generated sidecar')
        temporary.replace(output)
    except BaseException:
        # Only this exact newly-created builder file; never a learner database.
        if temporary.is_file():
            temporary.unlink()
        raise
    return {'format':FORMAT,'version':VERSION,'contexts':len(sources),
            'available':counts['available'],'pending':pending,
            'curated_corrections':sum(v['curated'] for v in provenance.values()),
            'identical_to_normal_translation':sum(v['order_translation'] is not None and
                v['order_translation']['text']==sources[k]['source_translation'] for k,v in records.items()),
            'bytes':output.stat().st_size,'sha256':sha256(output.read_bytes()).hexdigest(),
            'normal_translations_modified':False,'learner_database_accessed':False}


def main():
    from generate_order_translations import POLICY_HASH
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,default=ROOT/'data/enrichment/order_translation_corpus_v1.json')
    parser.add_argument('--batches',type=Path,default=ROOT/'data/enrichment/order_translations_v2/audited_batches')
    parser.add_argument('--overrides',type=Path,default=ROOT/'data/enrichment/order_translations_v2/editorial_corrections.json')
    parser.add_argument('--output',type=Path,default=ROOT/'app/src/kanamemory/resources/order_translations.sqlite3')
    parser.add_argument('--report',type=Path,default=ROOT/'environment/order-translations-aggressive-build-20261029.json')
    args=parser.parse_args()
    corpus=json.loads(args.corpus.read_text('utf-8'))
    batches=[json.loads(path.read_text('utf-8')) for path in sorted(args.batches.glob('*.json'))]
    overrides=json.loads(args.overrides.read_text('utf-8')) if args.overrides.exists() else None
    report=build(corpus,batches,args.output,overrides=overrides,policy_hash=POLICY_HASH)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='pending'},ensure_ascii=False))
    print(json.dumps({'pending':len(report['pending'])}))


if __name__=='__main__':
    main()
