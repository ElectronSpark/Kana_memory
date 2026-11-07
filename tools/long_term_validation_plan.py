"""Read-only launch checks for the paused bounded validation plan.

Checking a file never resumes a goal, locks a budget, freezes inputs or starts
a simulation. A later explicit instruction must authorize those operations.
"""
from __future__ import annotations

import argparse
import json
import math
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / 'config/long_term_validation_v2.json'


def load_plan(path=DEFAULT_PLAN):
    document = json.loads(Path(path).read_text(encoding='utf8'))
    if document.get('format') != 'kanamemory-bounded-long-term-validation-plan' or document.get('schema_version') != 2:
        raise ValueError('Unsupported bounded validation plan')
    return document


def launch_blockers(plan):
    reasons = []
    if (plan.get('status') != 'ready' or plan.get('execution_authorized') is not True
            or plan.get('plan_only') is not False):
        reasons.append('Goal remains paused or execution has not been explicitly authorized')
    gate = plan.get('execution_gate', {})
    if not gate.get('resume_authorization_reference'):
        reasons.append('Missing human resume instruction reference')
    for key in ('budgets_locked', 'acceptance_thresholds_locked', 'short_contracts_passed', 'benchmark_completed',
                'resource_watchdog_verified', 'checkpoint_scan_policy_verified', 'frozen_run_binding_verified'):
        if gate.get(key) is not True:
            reasons.append('Unfinished execution gate: ' + key)
    budget = plan.get('budgets', {})
    phases = ('benchmark', 'short_contracts', 'paired_reentry_total',
              'main_run_including_checkpoints', 'repair_and_revalidation', 'handoff')
    def positive(value):
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    if any(not positive(budget.get(key)) for key in (*phases, 'total', 'peak_memory_bytes', 'local_storage_bytes')):
        reasons.append('Finite positive time and resource budgets must be locked first')
    elif sum(budget[key] for key in phases) > budget['total']:
        reasons.append('Phase budgets exceed the total cap')
    limits = plan.get('acceptance', {}).get('numeric_limits_to_lock', {})
    if any(limits.get(key) is None for key in ('eligible_service_wait', 'event_latency', 'storage_growth', 'rationale')):
        reasons.append('Acceptance limits and rationale have not been specified')
    baseline = plan.get('baseline', {})
    if not baseline.get('future_frozen_run_id') or not baseline.get('future_frozen_inputs_sha256'):
        reasons.append('Current runtime, model, auditors and corpus have not been frozen')
    main = plan.get('bounded_runs', {}).get('main', {})
    if (main.get('count') != 1 or main.get('calendar_days') != 1461
            or main.get('stage_checkpoints') != [3, 30, 90, 1461]
            or main.get('real_api_calls') != 0
            or main.get('separate_from_zero_runs_per_stage') is not False):
        reasons.append('Main run must remain one offline resumable four-year run')
    if plan.get('repair_policy', {}).get('max_iterations') != 1:
        reasons.append('Only one bounded repair iteration is permitted')
    return reasons


def require_main_launch(path, *, output, days, stop_after=None, learner_model='responses_v2', seed=None):
    plan = load_plan(path)
    reasons = launch_blockers(plan)
    if reasons:
        raise ValueError('Simulation not started: ' + '; '.join(reasons))
    main = plan['bounded_runs']['main']
    if seed is not None and seed != main['seed']:
        raise ValueError('Seed differs from the fixed main-run plan')
    if days != main['calendar_days'] or (stop_after is not None and stop_after not in main['stage_checkpoints']):
        raise ValueError('Use the fixed calendar and declared checkpoints of this run')
    if Path(output).name != plan['baseline']['future_frozen_run_id'] or learner_model != 'responses_v2':
        raise ValueError('Run identity/model differs from the bounded validation plan')
    if plan.get('execution_contract_version') == 3:
        frozen = plan.get('frozen_inputs', {})
        payload = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if (hashlib.sha256(payload).hexdigest() != plan['baseline']['future_frozen_inputs_sha256']
                or frozen.get('parameters') != plan['bounded_runs'] or not frozen.get('files')):
            raise ValueError('Frozen validation inputs or parameters changed')
        for relative, expected in frozen['files'].items():
            source = (ROOT / relative).resolve()
            if (not source.is_relative_to(ROOT) or not source.is_file()
                    or hashlib.sha256(source.read_bytes()).hexdigest() != expected):
                raise ValueError('Frozen validation source changed: ' + relative)
        budget = plan['budgets']
        if budget['benchmark'] > 120 or budget['total'] > 7200 or budget['local_storage_bytes'] > 8*1024**3:
            raise ValueError('Validation exceeds the authorized resource caps')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path, nargs='?', default=DEFAULT_PLAN)
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    print(json.dumps({'status': plan['status'], 'can_launch': not launch_blockers(plan),
                      'blockers': launch_blockers(plan), 'simulation_started': False,
                      'goal_status_changed': False}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
