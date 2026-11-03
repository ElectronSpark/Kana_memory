"""Durable, conservative authoring budget; independent of learner progress.

Usage is a provider-reported count, while cost is an estimate at the frozen
rate, never an invoice. Unknown delivery keeps the reservation. Restoring an
older backup cannot erase locally known reservations or reopen a used stage.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re

from .material_workflow import digest, dump, PREFIX as JOB_PREFIX

PREFIX = 'material_budget_v1:'
SETTINGS = 'material_budget_settings_v1'
DEFAULT_LIMITS = {'tokens': 300000, 'microusd': 1000000}
# Standard, short-context, 30-minute cache-write ceilings; checked 2026-11-02.
# https://developers.openai.com/api/docs/models/gpt-6.1-sol
# https://developers.openai.com/api/docs/models/gpt-6-luna
OPENAI_RATES = {'gpt-6.1-sol': (2500, 10000), 'gpt-6-luna': (125, 500)}


def _integer(value, maximum=10**15):
    return type(value) is int and 0 <= value <= maximum


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def connection_key(config):
    return digest({k: config[k] for k in ('base_url', 'protocol', 'model', 'output_mode')})


def validate_policy(value):
    if (not isinstance(value, dict) or set(value) != {'limits', 'rates'}
            or not isinstance(value['limits'], dict) or set(value['limits']) != {'tokens', 'microusd'}
            or not _integer(value['limits']['tokens'], 10000000) or value['limits']['tokens'] < 12000
            or not _integer(value['limits']['microusd'], 100000000)
            or not isinstance(value['rates'], dict) or set(value['rates']) != {'input', 'output'}
            or any(not _integer(v, 10000000) for v in value['rates'].values())):
        raise ValueError('材料预算配置无效。')
    return deepcopy(value)


def validate_settings(value):
    if (not isinstance(value, dict) or len(value) > 128
            or any(not _hash(k) for k in value)):
        raise ValueError('材料预算配置无效。')
    return {k: validate_policy(v) for k, v in value.items()}


def policy_for(store, config):
    values = validate_settings(store.get_setting(SETTINGS, {}))
    saved = values.get(connection_key(config))
    if saved:
        return saved
    rates = OPENAI_RATES.get(config['model']) if config['base_url'] == 'https://api.openai.com/v1' else None
    return {'limits': dict(DEFAULT_LIMITS), 'rates': dict(zip(('input', 'output'), rates))} if rates else None


def context_for(store, config):
    policy = policy_for(store, config)
    if policy is None:
        raise ValueError('请先在 AI 服务的“材料调用预算”中填写此模型的单价；本地免费模型可填 0。')
    return {'connection': connection_key(config), 'model': config['model'], **policy}


def decimal_units(text, multiplier):
    try:
        value = Decimal(str(text)) * multiplier
        if not value.is_finite() or not 0 <= value <= 10**15 or value != value.to_integral_value():
            raise ValueError()
        return int(value)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError('请输入非负数字，并减少小数位数。') from None


def request_body(stage, request, model):
    from .ai_service import generation_spec, structured_body
    from .material_verification import review_spec
    from .material_revision import revision_spec
    spec = {'generation': generation_spec, 'review': review_spec, 'revision': revision_spec}[stage]
    instructions, payload, schema = spec(request)
    name = 'kana_' + request['mode'] + '_v1' if stage == 'generation' else 'kana_material_' + stage + '_v1'
    return structured_body(model, instructions, payload, schema, name,
                           max_output_tokens=12000 if stage == 'generation' else 8000)


def charge(input_tokens, output_tokens, rates):
    return (input_tokens * rates['input'] + output_tokens * rates['output'] + 999) // 1000


def validate_ledger(value):
    fields = {'version', 'id', 'request_sha256', 'limits', 'attempts'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int
            or value['version'] != 1 or not _hash(value['id']) or not _hash(value['request_sha256'])
            or not isinstance(value['attempts'], list) or not 1 <= len(value['attempts']) <= 3):
        raise ValueError('材料预算记录无效。')
    validate_policy({'limits': value['limits'], 'rates': {'input': 0, 'output': 0}})
    stages, tokens = set(), set()
    for row in value['attempts']:
        if (not isinstance(row, dict) or set(row) != {'stage', 'token', 'connection', 'body_sha256', 'rates', 'reserved', 'usage'}
                or row['stage'] not in {'generation', 'review', 'revision'} or row['stage'] in stages
                or not isinstance(row['token'], str) or not 1 <= len(row['token']) <= 64 or row['token'] in tokens
                or not _hash(row['connection']) or not _hash(row['body_sha256'])):
            raise ValueError('材料预算调用记录无效。')
        validate_policy({'limits': value['limits'], 'rates': row['rates']})
        reserve = row['reserved']
        if (not isinstance(reserve, dict) or set(reserve) != {'input', 'output', 'microusd'}
                or any(not _integer(v) for v in reserve.values())
                or reserve['microusd'] != charge(reserve['input'], reserve['output'], row['rates'])):
            raise ValueError('材料预算预留无效。')
        usage = row['usage']
        if usage is not None and (not isinstance(usage, dict) or set(usage) != {'input', 'output'}
                                  or any(not _integer(v) for v in usage.values())):
            raise ValueError('材料用量回执无效。')
        stages.add(row['stage']); tokens.add(row['token'])
    return deepcopy(value)


def read(db, identity):
    row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (PREFIX + identity,)).fetchone()
    return validate_ledger(json.loads(row[0])) if row else None


def write(db, value):
    if not db.in_transaction:
        raise RuntimeError('Budget requires a transaction')
    value = validate_ledger(value)
    db.execute('INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
               (PREFIX + value['id'], dump(value)))


def totals(value):
    tokens = cost = unknown = 0
    for row in value['attempts']:
        counts = row['usage'] or row['reserved']
        tokens += counts['input'] + counts['output']
        cost += charge(counts['input'], counts['output'], row['rates'])
        unknown += row['usage'] is None
    return {'tokens': tokens, 'microusd': cost, 'unknown': unknown}


def reserve(db, job, token, stage, request, context):
    """Called inside the same transaction as the workflow attempt, before I/O."""
    policy = validate_policy({k: context[k] for k in ('limits', 'rates')})
    old = read(db, job['id'])
    if old is None and job['attempts']:
        raise ValueError('旧任务缺少用量回执，不能重新分配调用额度；已保存材料仍可使用。')
    value = old or {'version': 1, 'id': job['id'], 'request_sha256': job['request_hash'],
                    'limits': policy['limits'], 'attempts': []}
    previous = next((a for a in value['attempts'] if a['connection'] == context['connection']), None)
    if previous:
        policy['rates'] = previous['rates']
    if value['request_sha256'] != job['request_hash'] or any(a['stage'] == stage for a in value['attempts']):
        raise ValueError('该阶段的预算已使用；恢复旧备份也不会重新调用。')
    body = request_body(stage, request, context['model'])
    # Covers prompt, schema, protocol duplication and framing without requiring
    # a tokenizer on the phone. This is an estimate, not a provider billing cap.
    input_tokens = len(dump(body).encode('utf-8')) * 2 + 4096
    if input_tokens > 250000:
        raise ValueError('材料请求过长，请减少原文或本批内容。')
    output_tokens = body['max_output_tokens']
    reserved = {'input': input_tokens, 'output': output_tokens,
                'microusd': charge(input_tokens, output_tokens, policy['rates'])}
    spent = totals(value)
    if (spent['tokens'] + input_tokens + output_tokens > value['limits']['tokens']
            or spent['microusd'] + reserved['microusd'] > value['limits']['microusd']):
        raise ValueError('本批材料调用预算不足，未发送请求。已保存材料仍可使用。')
    value['attempts'].append({'stage': stage, 'token': token, 'connection': context['connection'],
        'body_sha256': digest(body), 'rates': policy['rates'], 'reserved': reserved, 'usage': None})
    write(db, value)


def authorize(store, identity, token, provider):
    """Bind the next actual network body to its already committed reservation."""
    from .ai_connection import preset_config
    ledger = read(store._db, identity)
    row = next((r for r in ledger['attempts'] if r['token'] == token), None) if ledger else None
    from .material_workflow import MaterialWorkflowStore
    flow = MaterialWorkflowStore(store)
    if not flow._owns(flow.get(identity), token):
        raise ValueError('材料调用已取消或失去运行权，未发送请求。')
    config = getattr(provider, 'config', None)
    if config is None:
        config = {**preset_config('OpenAI'), 'model': getattr(provider, 'model', '')}
    if row is None or row['usage'] is not None or row['connection'] != connection_key(config):
        raise ValueError('服务配置已变化，请勿沿用此前的调用预留。')
    provider.bind_material_request(row['body_sha256'])


def settle(store, identity, token, usage):
    # Missing/partial/negative/malformed usage keeps the full reservation.
    if (not isinstance(usage, dict) or any(not _integer(usage.get(k)) for k in ('input_tokens', 'output_tokens'))
            or ('total_tokens' in usage and usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens'])):
        return False
    counts = {'input': usage['input_tokens'], 'output': usage['output_tokens']}
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        value = read(store._db, identity)
        row = next((r for r in value['attempts'] if r['token'] == token), None) if value else None
        if row is None or row['usage'] is not None:
            return False
        row['usage'] = counts
        write(store._db, value)
    return True


def snapshot(db):
    return [validate_ledger(json.loads(raw)) for raw, in db.execute(
        'SELECT value_json FROM app_setting WHERE key GLOB ?', (PREFIX + '*',))]


def merge_after_restore(db, retained):
    """Monotone evidence merge; orphan receipts intentionally remain spend fences."""
    for known in retained:
        incoming = read(db, known['id'])
        if incoming is None:
            write(db, known); continue
        if known['request_sha256'] != incoming['request_sha256']:
            raise ValueError('预算回执与恢复的材料请求冲突。')
        incoming['limits'] = {k: min(known['limits'][k], incoming['limits'][k]) for k in known['limits']}
        by_stage = {r['stage']: r for r in incoming['attempts']}
        for row in known['attempts']:
            other = by_stage.get(row['stage'])
            if other is None:
                incoming['attempts'].append(row)
            elif {k: v for k, v in row.items() if k != 'usage'} != {k: v for k, v in other.items() if k != 'usage'}:
                raise ValueError('同一材料阶段出现不同调用回执，恢复已取消。')
            elif row['usage'] is not None:
                other['usage'] = {k: max(row['usage'][k], (other['usage'] or row['usage'])[k]) for k in row['usage']}
        write(db, incoming)


def validate_database(db):
    values = snapshot(db)
    if len(values) > 10000:
        raise ValueError('材料预算记录过多。')
    for value in values:
        row = db.execute('SELECT value_json FROM app_setting WHERE key=?', (JOB_PREFIX + value['id'],)).fetchone()
        if row and json.loads(row[0])['request_hash'] != value['request_sha256']:
            raise ValueError('材料预算与请求不一致。')
        if row:
            attempts = {a['stage']: a for a in json.loads(row[0])['attempts']}
            if any(a['stage'] in attempts and attempts[a['stage']]['token'] != a['token'] for a in value['attempts']):
                raise ValueError('材料预算与调用回执不一致。')
