"""Material checks consume current local facts and exact question contracts."""
from .assessment_policy import assessment_decision, CONTRACT_FIELDS
from .material_verification import MaterialVerificationStore, canonical_targets
from .material_workflow import MaterialWorkflowStore


def current_targets(store, identity, *, repo=None, examples=None):
    from .ai_materials import AIMaterialStore
    job = MaterialWorkflowStore(store).get(identity)
    proposal = AIMaterialStore(store).get(job["proposal_id"]) if job and job["proposal_id"] else None
    if proposal is None:
        return []
    report = MaterialVerificationStore(store).current(identity, proposal, repo=repo, examples=examples)
    from .material_usage import project_proposal
    usable = project_proposal(store, proposal)
    if not report or not usable: return []
    from .material_senses import project_identities
    report = project_identities(store, proposal, report, repo=repo)
    from .material_reference import explicitly_blocked
    from copy import deepcopy
    usable = deepcopy(usable)
    blocked_examples = {row['id'] for row in proposal['example_pack']['examples']
                        if explicitly_blocked(store, proposal, 'example', row['id'])}
    usable['example_pack']['references'] = [row for row in usable['example_pack']['references']
        if row['example_id'] not in blocked_examples
        and not explicitly_blocked(store, proposal, 'reference', row['id'])]
    from .material_reference import qualified_items
    return [*canonical_targets(usable, report, repo=repo), *qualified_items(store, identity)][:12]


def pretest_targets(store, identity, *, repo=None, examples=None):
    from .material_qualification import candidate_basis, candidate_questions
    canonical = current_targets(store, identity, repo=repo, examples=examples)
    ids = {item['id'] for item in canonical}
    from .material_pretest import receipts
    frozen_ids = {cid for receipt in receipts(store, identity) for cid in receipt['target_ids']}
    candidates = []
    bases = candidate_basis(store, identity, repo=repo, examples=examples)
    questions = candidate_questions(store, identity, _bases=bases)
    testable = {q['target_id'] for q in questions}
    for basis in bases:
        if basis['source']['object_id'] in ids and basis['item']['id'] not in frozen_ids:
            continue  # A verified identity already has one ordinary opportunity.
        item = basis['item']
        if item['id'] in testable:
            candidates.append({**item, 'abilities': sorted({q['ability'] for q in questions if q['target_id'] == item['id']})})
    result = [*canonical, *candidates]
    return result[:24]


def same_contract(canonical, question):
    from copy import deepcopy
    from .assessment_contract import for_question
    canonical = deepcopy(canonical)
    canonical['id'] = question.get('id')
    fields = (*CONTRACT_FIELDS, 'feedback', 'guide', 'order_translation', 'learning_conditions',
              'learning_condition_source', 'learning_condition_revision', 'material_qualification')
    return (all(question.get(field) == canonical.get(field) for field in fields)
            and for_question(question) == for_question(canonical))


def exact_question(item, question):
    from .learning_questions import build_learning_question, question_kinds
    if (not item or question.get("ai_generated") or question.get("guided")
            or question.get("kind") not in question_kinds(item["id"], item=item)):
        return False
    try:
        canonical = build_learning_question(item, question["kind"], variant=question.get("variant", 0))
        # A saved opportunity has its own instance ID; its teaching and grading
        # contract must still match the rebuilt canonical question exactly.
        return same_contract(canonical, question)
    except (ValueError, KeyError, TypeError):
        return False


def material_assessment(store, identity, question, *, repo=None, examples=None, exposed=False):
    from .material_qualification import bind_resources, qualification
    bind_resources(store, repo=repo, examples=examples)
    item = next((item for item in pretest_targets(store, identity, repo=repo, examples=examples)
                 if item["id"] == question["target_id"]), None)
    decision = assessment_decision(item or {"id": question["target_id"], "revision": "missing"},
        question, purpose="check_only", enrolled=False, exposed=exposed, store=store)
    trusted = (qualification(store, question, repo=repo, examples=examples)
               if question.get('material_qualification') else exact_question(item, question))
    if not trusted:
        decision.update(assessment_eligible=False, permitted_purposes=["preview"],
                        content_validity="blocked", reason="material_blocked")
    # A material pretest never creates a scheduler unit or upgrades an observation.
    decision["settlement_channel"] = "observation"
    return decision
