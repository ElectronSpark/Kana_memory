"""Durable check-only episodes for reviewed local material, including candidates.

Each episode is a separate bounded record in the current learner database.
No content/unit/card is enrolled here and no saved answer is later replayed as
a review. AI draft eligibility will consume the same gate when verified.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json

from . import app_clock
from .assessment_contract import observations, validate_result
from .assessment_parts import aggregate
from .assessment_policy import assessment_decision, contract_values
from .material_review import fingerprint

PREFIX = 'retention_check_v1:'
EXPOSURE_PREFIX = 'retention_exposure_v1:'
FORMAT = 'kanamemory-retention-check'
MAX_TARGETS = 12
MAX_BYTES = 512 * 1024


def _date(value):
    if not isinstance(value,str):raise ValueError('Invalid check date')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Missing check timezone')
    return stamp


def _proof(question):
    return fingerprint(contract_values(question))


def validate_exposure(value):
    if (not isinstance(value,dict) or set(value)!={'version','target_id','abilities'}
            or type(value['version']) is not int or value['version']!=1
            or not isinstance(value['target_id'],str) or not 1<=len(value['target_id'])<=1024
            or not isinstance(value['abilities'],dict) or len(value['abilities'])>12):
        raise ValueError('Invalid check exposure record')
    for ability,record in value['abilities'].items():
        if (ability not in ('understanding','reading','use','listening','application')
                or not isinstance(record,dict) or set(record)!={'at','source'}
                or record['source'] not in ('instruction','summary','feedback','declared')):
            raise ValueError('Invalid ability exposure')
        _date(record['at'])
    return deepcopy(value)


def exposure_times(store, now=None):
    """Ability-specific, short independence guard; never edits an FSRS date.

    Read once for a queue pass. A saved draft does not undo an answer revealed
    in a check or explanation; a frozen submitted result remains resumable.
    """
    from .learning_store import RETRIEVAL_DELAY_SECONDS
    stamp = now or app_clock.utc_now()
    result = {}
    for row in store._db.execute('SELECT value_json FROM app_setting WHERE key GLOB ?', (EXPOSURE_PREFIX + '*',)):
        value = validate_exposure(json.loads(row[0]))
        for ability, record in value['abilities'].items():
            until = _date(record['at']) + timedelta(seconds=RETRIEVAL_DELAY_SECONDS)
            if until > stamp:
                key = (value['target_id'], ability)
                if key not in result or until > _date(result[key]):
                    result[key] = until.isoformat()
    from .material_admission import project_exposure_times
    return project_exposure_times(store._db, result)


def validate_episode(value):
    if (not isinstance(value, dict) or set(value) != {'format','version','id','status','created_at_utc','purpose','entries'}
            or value['format'] != FORMAT or type(value['version']) is not int or value['version'] != 1
            or value['purpose'] != 'check_only' or value['status'] not in ('active','completed')
            or not isinstance(value['id'], str) or len(value['id']) != 64
            or any(c not in '0123456789abcdef' for c in value['id'])
            or not isinstance(value['entries'], list) or not 1 <= len(value['entries']) <= MAX_TARGETS):
        raise ValueError('Invalid check episode')
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Check episode exceeds size limit')
    created = _date(value['created_at_utc'])
    units, ids = set(), set()
    for row in value['entries']:
        if not isinstance(row, dict) or set(row) - {'content_retirement'} != {'id','question','proof','exposed','exposure','draft','result','completed'}:
            raise ValueError('Invalid check entry')
        if row.get('content_retirement') and (not isinstance(row['content_retirement'], str)
                or len(row['content_retirement']) != 64 or row['result'] is not None or row['completed']):
            raise ValueError('Invalid retired check entry')
        q = row['question']
        if (not isinstance(q,dict) or any(not isinstance(q.get(k),str) or not q[k] for k in ('id','target_id','ability','kind','answer'))
                or row['id'] != q['id'] or not row['id'].startswith('check:' + value['id'] + ':') or row['id'] in ids
                or row['proof'] != _proof(q) or type(row['exposed']) is not bool or type(row['completed']) is not bool
                or q.get('input_mode','choice') not in ('choice','text','typing','free_text') or q.get('audio_text')):
            raise ValueError('Invalid check question')
        unit = (q['target_id'],q['ability'])
        if unit in units:
            raise ValueError('Duplicate check ability')
        units.add(unit);ids.add(row['id'])
        if row['exposed'] != (row['exposure'] is not None):
            raise ValueError('Check exposure flag differs from its record')
        if row['exposure'] is not None:
            validate_exposure({'version':1,'target_id':q['target_id'],'abilities':{q['ability']:row['exposure']}})
        if (not isinstance(row['draft'],str) or len(row['draft']) > 10000
                or not isinstance(q.get('choices',[]),list)
                or any(not isinstance(x,str) for x in q.get('choices',[]))):
            raise ValueError('Invalid check draft')
        result = row['result']
        if result is not None:
            if (not isinstance(result,dict) or set(result) != {'question_id','answered_text','correct','outcome','assisted','scheduled','qualified','submitted_at_utc','assessment','observed_judgement','observations','unit_observations'}
                    or result['question_id'] != row['id'] or result['scheduled'] is not False
                    or type(result['qualified']) is not bool or type(result['assisted']) is not bool
                    or not isinstance(result['answered_text'],str) or len(result['answered_text']) > 10000
                    or result['outcome'] not in ('correct','incorrect','revealed','unresolved','cancelled')
                    or result['correct'] is not None and type(result['correct']) is not bool
                    or result['outcome']=='correct' and result['correct'] is not True
                    or result['outcome']=='incorrect' and result['correct'] is not False
                    or not result['qualified'] and (result['outcome'] not in ('unresolved','cancelled') or result['correct'] is not None)
                    or row['exposed'] and not result['assisted']
                    or not isinstance(result['assessment'],dict)
                    or not isinstance(result['observed_judgement'],dict)
                    or result['assessment'].get('settlement_channel') != 'observation'
                    or result['assessment'].get('purpose') != 'check_only'):
                raise ValueError('Check result cannot authorize scheduling')
            if _date(result['submitted_at_utc']) < created:
                raise ValueError('Check answer predates its episode')
            if result['outcome']=='cancelled' and (result['qualified'] or result['correct'] is not None
                    or result['answered_text']!=row['draft'] or result['assisted']!=row['exposed']
                    or result['observed_judgement']!={'outcome':'cancelled','correct':None}):
                raise ValueError('Cancelled check cannot claim learning evidence')
            validate_result(q,result)
        if row['completed'] and result is None:
            raise ValueError('Check entry has no answer')
    if (value['status']=='completed') != all(row['completed'] or row.get('content_retirement') for row in value['entries']):
        raise ValueError('Check completion disagrees with entries')
    return deepcopy(value)


class RetentionCheckStore:
    def __init__(self, store):
        self.store, self.db = store, store._db

    def get(self, episode_id):
        raw = self.store.get_setting(PREFIX + episode_id, None)
        return validate_episode(raw) if raw is not None else None

    def active(self):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.status')='active' ORDER BY rowid DESC LIMIT 1", (PREFIX+'*',)).fetchone()
        return validate_episode(json.loads(row[0])) if row else None

    def latest_completed(self):
        row = self.db.execute("SELECT value_json FROM app_setting WHERE key GLOB ? AND json_extract(value_json,'$.status')='completed' ORDER BY rowid DESC LIMIT 1", (PREFIX+'*',)).fetchone()
        return validate_episode(json.loads(row[0])) if row else None

    def recent_targets(self):
        rows=self.db.execute('SELECT value_json FROM app_setting WHERE key GLOB ? ORDER BY rowid DESC LIMIT 20',(PREFIX+'*',))
        return {r['question']['target_id'] for row in rows for r in validate_episode(json.loads(row[0]))['entries']}

    def expose_many(self, items, source, *, now=None):
        stamp=(now or app_clock.utc_now()).isoformat()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for item in items:
                cid=item['id'];key=EXPOSURE_PREFIX+sha256(cid.encode()).hexdigest()
                value=self.store.get_setting(key,{'version':1,'target_id':cid,'abilities':{}})
                validate_exposure(value)
                for ability in item['abilities']:
                    prior=value['abilities'].get(ability)
                    if prior is None or _date(stamp)>=_date(prior['at']):
                        value['abilities'][ability]={'at':stamp,'source':source}
                validate_exposure(value)
                self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                                (key,json.dumps(value,ensure_ascii=False,separators=(',',':'))))

    def recent_exposure(self, target_id, ability, now):
        from .learning_store import RETRIEVAL_DELAY_SECONDS
        value=self.store.get_setting(EXPOSURE_PREFIX+sha256(target_id.encode()).hexdigest(),None)
        candidate=validate_exposure(value)['abilities'].get(ability) if value is not None else None
        from .material_admission import mapped_exposure
        mapped = mapped_exposure(self.store, target_id, ability)
        if mapped and (candidate is None or _date(mapped['at']) > _date(candidate['at'])):
            candidate = mapped
        # Enrolled teaching already has durable exposure timestamps. Read it;
        # never rewrite its checkpoint simply to begin a check-only episode.
        row=self.db.execute('SELECT checkpoint_json FROM learning_content WHERE content_id=?',(target_id,)).fetchone()
        if row:
            checkpoint=json.loads(row[0])
            for field in ('instruction_exposed_at_utc','feedback_exposed_at_utc'):
                stamp=checkpoint.get(field)
                if stamp and (candidate is None or _date(stamp)>_date(candidate['at'])):
                    candidate={'at':stamp,'source':'instruction' if field.startswith('instruction') else 'feedback'}
        # Clock reversal cannot erase a known exposure.
        return candidate if candidate and (now-_date(candidate['at'])).total_seconds()<RETRIEVAL_DELAY_SECONDS else None

    def _write(self, value):
        validate_episode(value)
        self.db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                        (PREFIX+value['id'],json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))))

    def start(self, target_ids, enabled_kinds, request_id, *, exposed_ids=(), now=None):
        from .learning_content import get_learning_item
        from .learning_questions import build_learning_question, question_kinds
        from .learning_evidence import reviewed_question
        if (not isinstance(request_id,str) or not 1 <= len(request_id) <= 256
                or not isinstance(target_ids,list) or not 1 <= len(target_ids) <= MAX_TARGETS
                or any(not isinstance(x,str) or not x for x in target_ids) or len(set(target_ids)) != len(target_ids)):
            raise ValueError('请选择少量不同的学习项。')
        identity = sha256(request_id.encode()).hexdigest()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous = self.get(identity)
            if previous:
                if [r['question']['target_id'] for r in previous['entries']] != target_ids:
                    raise ValueError('同一检查请求不能替换原题。')
                return previous
            if self.active() or self.store.get_setting('retention_active_v2', None) is not None:
                raise ValueError('请先继续未完成的检查。')
            entries=[]
            stamp=now or app_clock.utc_now()
            for cid in target_ids:
                item=get_learning_item(cid)
                if item is None:
                    raise ValueError('这项材料尚无可核验的本地题。')
                candidates=[]
                for kind in question_kinds(cid,item=item):
                    if kind not in enabled_kinds:
                        continue
                    q=build_learning_question(item,kind)
                    from .answer_input import available, system_japanese_enabled
                    if not available(q,system_japanese=system_japanese_enabled(self.store)):
                        continue
                    if q.get('input_mode','choice') not in ('choice','text','typing','free_text') or q.get('audio_text'):
                        continue
                    if not reviewed_question(q) or not assessment_decision(item,q,purpose='check_only',enrolled=False,store=self.store)['assessment_eligible']:
                        continue
                    candidates.append(q)
                if not candidates:
                    raise ValueError('所选内容没有已启用的可靠检查题。')
                # Recognition before retrieval; S3 will add the bounded second probe.
                candidates.sort(key=lambda q:q.get('input_mode','choice')!='choice')
                q=candidates[0]
                if self.db.execute("SELECT 1 FROM learning_question q JOIN learning_unit u ON u.unit_id=q.unit_id WHERE u.content_id=? AND u.ability=? AND q.status='pending'",(cid,q['ability'])).fetchone():
                    raise ValueError('这项能力已有未完成题，请先继续原练习。')
                q['id']='check:'+identity+':'+str(len(entries))
                exposure=({'at':stamp.isoformat(),'source':'declared'} if cid in exposed_ids else self.recent_exposure(cid,q['ability'],stamp))
                entries.append({'id':q['id'],'question':q,'proof':_proof(q),'exposed':exposure is not None,'exposure':exposure,
                                'draft':'','result':None,'completed':False})
            value={'format':FORMAT,'version':1,'id':identity,'status':'active','created_at_utc':stamp.isoformat(),
                   'purpose':'check_only','entries':entries}
            self._write(value)
            return deepcopy(value)

    def _mutate(self, episode_id, question_id, operation):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            value=self.get(episode_id)
            if value is None: raise KeyError(episode_id)
            row=next((r for r in value['entries'] if r['id']==question_id),None)
            if row is None: raise KeyError(question_id)
            old=deepcopy(value);result=operation(value,row)
            if value!=old:self._write(value)
            return deepcopy(result)

    def save_draft(self, episode_id, question_id, answer):
        def write(value,row):
            if row['result'] is None and not row.get('content_retirement'):row['draft']=answer
        self._mutate(episode_id,question_id,write)

    def current(self, episode_id, enabled_kinds):
        value = self.get(episode_id)
        if value is None: return None
        from .retention_sources import current_item, trusted_question
        for row in value['entries']:
            if row['completed'] or row.get('content_retirement'): continue
            if row['result'] is not None: return row
            q = row['question']; item = current_item(self.store, q['target_id'])
            if (q['kind'] in enabled_kinds and item and trusted_question(self.store, q, item=item)
                    and assessment_decision(item, q, purpose='check_only', store=self.store)['assessment_eligible']):
                return row
        return None

    def submit(self, episode_id, question_id, answer, enabled_kinds, *, revealed=False, now=None):
        from .learning_content import get_learning_item
        from .learning_questions import grade_learning_answer
        from .learning_evidence import LearningEvidenceStore, reviewed_question
        def write(value,row):
            if row['result'] is not None:return row['result']
            if row.get('content_retirement'): raise ValueError('此题内容已修订，原题与草稿已保存。')
            q=row['question']
            if q['kind'] not in enabled_kinds:raise ValueError('这个题型已关闭，题目和草稿已保留。')
            if not isinstance(answer,str) or len(answer)>10000 or type(revealed) is not bool:raise ValueError('Invalid check answer')
            judge={'status':'revealed','correct':None} if revealed else grade_learning_answer(q,answer)
            if judge['status'] in ('empty','invalid'):raise ValueError(judge.get('message','请先作答。'))
            item=get_learning_item(q['target_id']) or {'id':q['target_id'],'revision':'missing'}
            stamp=max(now or app_clock.utc_now(),_date(value['created_at_utc']))
            exposure=self.recent_exposure(q['target_id'],q['ability'],stamp)
            if exposure is not None:row.update(exposed=True,exposure=exposure)
            enrolled=self.db.execute('SELECT 1 FROM learning_content WHERE content_id=?',(q['target_id'],)).fetchone() is not None
            decision=assessment_decision(item,q,purpose='check_only',enrolled=enrolled,exposed=row['exposed'],store=self.store)
            qualified=decision['assessment_eligible'] and reviewed_question(q)
            outcome=judge['status'] if qualified else 'unresolved'
            result={'question_id':q['id'],'answered_text':answer,'correct':judge['correct'] if qualified else None,
                    'outcome':outcome,'assisted':row['exposed'] or revealed,'scheduled':False,'qualified':qualified,
                    'submitted_at_utc':stamp.isoformat(),'assessment':decision,
                    'observed_judgement':{'outcome':judge['status'],'correct':judge['correct']}}
            result['observations']=observations(q,result);result['unit_observations']=aggregate(result['observations'])
            LearningEvidenceStore(self.store).record_attempt('diagnostic:'+q['id'],q,result,source='diagnostic',role='exposure',
                trusted=qualified,opportunity_id='diagnostic:'+q['id'],now=stamp,
                part_ids=tuple(p['part_id'] for p in result['observations']))
            row['result']=result
            return result
        return self._mutate(episode_id,question_id,write)

    def advance(self, episode_id, question_id):
        def write(value,row):
            if row['result'] is None:raise ValueError('请先作答。')
            row['completed']=True
            if all(r['completed'] or r.get('content_retirement') for r in value['entries']):value['status']='completed'
            return value
        return self._mutate(episode_id,question_id,write)

    def skip_unavailable_input(self, episode_id, question_id, *, now=None):
        from .answer_input import available, system_japanese_enabled
        from .learning_content import get_learning_item
        def write(value,row):
            if row['result'] is not None:return row['result']
            if row.get('content_retirement'): raise ValueError('此题内容已修订，原题与草稿已保存。')
            q=row['question']
            if available(q,system_japanese=system_japanese_enabled(self.store)):
                raise ValueError('这道题可以用当前输入方式作答。')
            item=get_learning_item(q['target_id']) or {'id':q['target_id'],'revision':'missing'}
            stamp=max(now or app_clock.utc_now(),_date(value['created_at_utc']))
            decision=assessment_decision(item,q,purpose='check_only',enrolled=False,exposed=row['exposed'],store=self.store)
            result={'question_id':q['id'],'answered_text':row['draft'],'correct':None,
                'outcome':'cancelled','assisted':row['exposed'],'scheduled':False,'qualified':False,
                'submitted_at_utc':stamp.isoformat(),'assessment':decision,
                'observed_judgement':{'outcome':'cancelled','correct':None}}
            result['observations']=observations(q,result);result['unit_observations']=aggregate(result['observations'])
            row.update(result=result,completed=True)
            if all(r['completed'] or r.get('content_retirement') for r in value['entries']):value['status']='completed'
            return result
        return self._mutate(episode_id,question_id,write)
